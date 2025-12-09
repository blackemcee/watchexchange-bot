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

# Кого слушаем
TARGET_USERS = {"ParentalAdvice", "AudaciousCo"}

# RSS-лента: можно переопределить через RSS_FEED
RSS_URL = os.getenv("RSS_FEED")

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


TARGET_USERS_NORMALIZED = {u.lower() for u in TARGET_USERS}

# -----------------------------
# MAIN LOOP
# -----------------------------
log.info(f"Bot started (RSS mode)! RSS_URL={RSS_URL}")

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
            log.info(f"AUTHOR RAW: '{raw_author}'")

            author_norm = normalize_author(raw_author)
            log.info(f"AUTHOR NORMALIZED: '{author_norm}'")

            if author_norm not in TARGET_USERS_NORMALIZED:
                continue

            title = entry.title
            summary = entry.summary  # HTML с изображениями
            image_url = extract_first_image_from_html(summary)

            message = (
                f"🕵️ Новый пост от *{author_norm}*\n\n"
                f"*{title}*\n\n"
                f"[Открыть пост]({link})"
            )

            # отправка
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

            log.info(f"Sent post {post_id} from {author_norm}")

            # пометили как отправленный
            seen_posts.add(post_id)
            save_seen(seen_posts)

    except Exception as e:
        log.error(f"Error in main loop: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)