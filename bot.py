import time
import json
import os
import requests
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

TARGET_USERS = {"ParentalAdvice", "AudaciousCo", "Vast_Requirement8134"}

RSS_URL = os.getenv("RSS_FEED")
#RSS_URL = "https://www.reddit.com/r/Watchexchange/new/.rss"

bot = Bot(token=TELEGRAM_TOKEN)

SEEN_FILE = "seen.json"


# -----------------------------
# LOAD / SAVE
# -----------------------------
def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


seen_posts = load_seen()


# -----------------------------
# EXTRACT FIRST IMAGE FROM HTML
# -----------------------------
def extract_first_image_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"].replace("&amp;", "&")
    return None


# -----------------------------
# MAIN LOOP
# -----------------------------
log.info("Bot started (RSS mode)!")

while True:
    try:
        feed = feedparser.parse(RSS_URL)

        for entry in feed.entries:
            post_id = entry.id

            # уже отправляли
            if post_id in seen_posts:
                continue

            # автор
            author = entry.get("author", "")
            if author not in TARGET_USERS:
                continue

            # текст
            title = entry.title
            link = entry.link
            summary = entry.summary  # HTML с изображениями
            image_url = extract_first_image_from_html(summary)

            message = (
                f"🕵️ Новый пост от *{author}*\n\n"
                f"*{title}*\n\n"
                f"[Открыть пост]({link})"
            )

            # отправка
            if image_url:
                bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=image_url,
                    caption=message,
                    parse_mode="Markdown"
                )
            else:
                bot.send_message(
                    chat_id=CHAT_ID,
                    text=message,
                    parse_mode="Markdown"
                )

            log.info(f"Sent post {post_id} from {author}")

            # сохраним ID, чтобы не слать дважды
            seen_posts.add(post_id)
            save_seen(seen_posts)

    except Exception as e:
        log.error(f"Error: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)
