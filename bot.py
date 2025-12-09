import time
import json
import os
import re
import feedparser
from bs4 import BeautifulSoup
from telegram import Bot
import logging

# -----------------------------
# LOGGING
# -----------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("watchbot")

# -----------------------------
# CONFIG - ENV VARS
# -----------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL"))

# RSS-лента: можно переопределить через ENV RSS_FEED
RSS_URL = os.getenv("RSS_FEED")

# Включение/выключение keyword-фильтра
# 0 -> игнорируем KEYWORDS, только tracked users
# 1 -> tracked users + посты, где в заголовке есть KEYWORDS
ENABLE_KEYWORD_FILTER = int(os.getenv("ENABLE_KEYWORD_FILTER"))

# Список ключевых слов (брендов) из ENV
raw_keywords = os.getenv("KEYWORDS")
KEYWORDS = {kw.strip().lower() for kw in raw_keywords.split(",") if kw.strip()}

# Список отслеживаемых юзеров из ENV
# Пример: TRACKED_USERS=ParentalAdvice,AudaciousCo,Vast_Requirement8134
raw_tracked = os.getenv("TRACKED_USERS")
TRACKED_USERS_NORMALIZED = {
    u.strip().lower()
    for u in raw_tracked.split(",")
    if u.strip()
}

log.info(f"RSS_URL = {RSS_URL}")
log.info(f"Tracked users: {TRACKED_USERS_NORMALIZED}")
log.info(f"Keyword filter: {ENABLE_KEYWORD_FILTER}, keywords={KEYWORDS}")

bot = Bot(token=TELEGRAM_TOKEN)

# -----------------------------
# SEEN STORAGE (на Volume)
# -----------------------------
SEEN_FILE = "/mnt/data/seen.json"


def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
            seen = set(data)
            log.info(f"Loaded seen_posts: {len(seen)} items")
            return seen
    except FileNotFoundError:
        log.info("seen.json not found, starting with empty set")
        return set()
    except Exception as e:
        log.error(f"Error loading seen.json: {e}")
        return set()


def save_seen(seen):
    try:
        os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen), f)
        log.info(f"Saved seen_posts: {len(seen)} items")
    except Exception as e:
        log.error(f"Error saving seen.json: {e}")


seen_posts = load_seen()

# -----------------------------
# HELPERS
# -----------------------------


def extract_first_image_from_html(html: str):
    """Берём первую <img> из HTML summary."""
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        src = img["src"].replace("&amp;", "&")
        if src.startswith("//"):
            src = "https:" + src
        return src
    return None


def extract_post_id(link: str) -> str:
    """
    Стабильный ID поста из URL вида:
    https://www.reddit.com/r/test/comments/abc123/title/
    Если не нашли — возвращаем сам линк.
    """
    if not link:
        return ""
    match = re.search(r"/comments/([a-z0-9]+)/", link)
    if match:
        return match.group(1)
    return link.strip()


def normalize_author(raw_author: str) -> str:
    """
    /u/Vast_Requirement8134 -> vast_requirement8134
    """
    if not raw_author:
        return ""
    a = raw_author.lower().strip()
    a = a.replace("/u/", "").replace("u/", "")
    return a


log.info("Bot started (RSS mode)!")

# -----------------------------
# MAIN LOOP
# -----------------------------
while True:
    try:
        feed = feedparser.parse(RSS_URL)
        log.info(f"Fetched feed with {len(feed.entries)} entries")

        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            post_id = extract_post_id(link)

            # защитa от дублей
            if post_id in seen_posts:
                continue

            raw_author = entry.get("author", "") or ""
            author_norm = normalize_author(raw_author)

            # Заголовок
            title = getattr(entry, "title", "") or ""
            title_lower = title.lower()

            # Фильтр по ключевым словам в заголовке
            title_matches_keyword = any(kw in title_lower for kw in KEYWORDS)

            # Логика включения
            author_ok = author_norm in TRACKED_USERS_NORMALIZED
            keyword_ok = ENABLE_KEYWORD_FILTER == 1 and title_matches_keyword

            # Если ни tracked user, ни keyword — пропускаем
            if not (author_ok or keyword_ok):
                continue

            summary = entry.summary
            image_url = extract_first_image_from_html(summary)

            if author_ok and keyword_ok:
                source_label = "tracked user + keyword match"
            elif author_ok:
                source_label = "tracked user"
            else:
                # только keyword
                matched = [kw for kw in KEYWORDS if kw in title_lower]
                source_label = f"keyword match: {','.join(matched) or 'unknown'}"

            message = (
                f"🕵️ New post ({source_label})\n\n"
                f"*Author:* {author_norm or 'unknown'}\n\n"
                f"*{title}*\n\n"
                f"[Open post]({link})"
            )

            if image_url:
                bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=image_url,
                    caption=message,
                    parse_mode="Markdown",
                )
            else:
                bot.send_message(
                    chat_id=CHAT_ID,
                    text=message,
                    parse_mode="Markdown",
                )

            log.info(
                f"Sent post {post_id} from {author_norm} "
                f"(author_ok={author_ok}, keyword_ok={keyword_ok})"
            )

            seen_posts.add(post_id)
            save_seen(seen_posts)

    except Exception as e:
        log.error(f"Error in main loop: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)