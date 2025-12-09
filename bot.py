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

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

# Авторов слушаем всегда
TARGET_USERS = {"ParentalAdvice", "AudaciousCo"}
TARGET_USERS_NORMALIZED = {u.lower() for u in TARGET_USERS}

# Основной фид
RSS_URL = os.getenv(
    "RSS_FEED",
    "https://www.reddit.com/r/Watchexchange/new/.rss",
)

# Включение/выключение keyword-фильтра
ENABLE_KEYWORD_FILTER = int(os.getenv("ENABLE_KEYWORD_FILTER"))

# Список ключевых слов
raw_keywords = os.getenv("KEYWORDS")
KEYWORDS = {kw.strip().lower() for kw in raw_keywords.split(",") if kw.strip()}

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


log.info(f"Bot started (RSS mode)! RSS_URL={RSS_URL}")

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

            # -------------------------------
            # ФИЛЬТР ПО КЛЮЧЕВЫМ СЛОВАМ
            # -------------------------------
            title_matches_keyword = any(kw in title_lower for kw in KEYWORDS)

            # Логика включения
            author_ok = author_norm in TARGET_USERS_NORMALIZED
            keyword_ok = ENABLE_KEYWORD_FILTER == 1 and title_matches_keyword

            # Если ни один фильтр не срабатывает — пропускаем
            if not (author_ok or keyword_ok):
                continue

            summary = entry.summary
            image_url = extract_first_image_from_html(summary)

            source_label = (
                "tracked user"
                if author_ok
                else f"keyword match: {','.join([kw for kw in KEYWORDS if kw in title_lower])}"
            )

            message = (
                f"🕵️ New post ({source_label})\n\n"
                f"*Author:* {author_norm or 'unknown'}\n\n"
                f"*{title}*\n\n"
                f"[Open Reddit]({link})"
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