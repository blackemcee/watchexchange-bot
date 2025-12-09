import time
import json
import os
import re
import feedparser
import requests
from bs4 import BeautifulSoup
from telegram import Bot, InputMediaPhoto
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

# RSS-лента
RSS_URL = os.getenv(
    "RSS_FEED",
    "https://old.reddit.com/r/Watchexchange/new/.rss",
)

# Фильтр по ключевым словам
ENABLE_KEYWORD_FILTER = int(os.getenv("ENABLE_KEYWORD_FILTER", "0"))
raw_keywords = os.getenv("KEYWORDS", "")

KEYWORDS = set()
for part in raw_keywords.replace(";", ",").split(","):
    kw = part.strip().strip(" '\"").lower()
    if kw:
        KEYWORDS.add(kw)

# Отслеживаемые юзеры
raw_tracked = os.getenv("TRACKED_USERS", "")
TRACKED_USERS_NORMALIZED = set()
for part in raw_tracked.replace(";", ",").split(","):
    u = part.strip().strip(" '\"").lower()
    if u:
        TRACKED_USERS_NORMALIZED.add(u)

log.info(f"RSS_URL = {RSS_URL}")
log.info(f"Tracked users (normalized): {TRACKED_USERS_NORMALIZED}")
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


def fetch_feed(url: str):
    """RSS через requests + нормальный UA."""
    try:
        if not url:
            log.error("RSS_URL is empty!")
            return feedparser.parse("")

        headers = {
            "User-Agent": "WatchExchangeTelegramBot/0.1 (by u/Vast_Requirement8134)"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        log.info(f"RSS HTTP status={resp.status_code}, length={len(resp.text)}")
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        if getattr(feed, "bozo", 0):
            log.warning(
                f"Feedparser bozo={feed.bozo}, exception={getattr(feed, 'bozo_exception', None)}"
            )
        return feed
    except Exception as e:
        log.error(f"Error fetching RSS: {e}")
        return feedparser.parse("")


def extract_first_image_from_html(html: str):
    """Фоллбэк: берём первую <img> из HTML summary RSS (мелкое превью)."""
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        src = img["src"].replace("&amp;", "&")
        if src.startswith("//"):
            src = "https:" + src
        return src
    return None


def extract_post_id(link: str) -> str:
    """ID поста из URL /comments/<id>/."""
    if not link:
        return ""
    match = re.search(r"/comments/([a-z0-9]+)/", link)
    if match:
        return match.group(1)
    return link.strip()


def normalize_author(raw_author: str) -> str:
    """Приводим автора к 'vast_requirement8134' формату."""
    if not raw_author:
        return ""

    a = raw_author.strip()

    m = re.search(r"u/([A-Za-z0-9_-]+)", a)
    if m:
        return m.group(1).lower()

    a = a.lower()
    a = a.replace("/u/", "").replace("u/", "").strip()

    return a


def escape_html(text: str) -> str:
    """Экранируем для HTML parse_mode."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def build_json_url_from_link(link: str) -> str | None:
    """
    Из любой reddit-ссылки (old/new/mobile) делаем:
    https://www.reddit.com/r/.../comments/.../.json
    """
    if not link:
        return None

    # Отбрасываем параметры
    link = link.split("?", 1)[0]

    m = re.search(r"(/r/[^/]+/comments/[a-z0-9]+/[^/]+)", link)
    if not m:
        log.warning(f"Cannot extract reddit path from link: {link}")
        return None

    path = m.group(1)
    json_url = f"https://www.reddit.com{path}.json"
    return json_url


def get_images_from_reddit(link: str):
    """
    Возвращает список URL картинок:
    - для галереи: все (до 10, через gallery_data + media_metadata)
    - для одиночного поста: одну (из url/preview)
    Если не получилось — [].
    """
    images = []

    try:
        json_url = build_json_url_from_link(link)
        if not json_url:
            return []

        headers = {
            "User-Agent": "WatchExchangeTelegramBot/0.1 (by u/Vast_Requirement8134)"
        }
        resp = requests.get(json_url, headers=headers, timeout=5)
        log.info(f"JSON HTTP status={resp.status_code} for {json_url}")
        resp.raise_for_status()
        data = resp.json()

        post = data[0]["data"]["children"][0]["data"]

        gallery_data = post.get("gallery_data")
        media = post.get("media_metadata") or {}

        # 1) Галерея через gallery_data
        if gallery_data and "items" in gallery_data and media:
            for item in gallery_data["items"]:
                media_id = item.get("media_id")
                if not media_id:
                    continue
                md = media.get(media_id) or {}
                url = None
                if "s" in md and "u" in md["s"]:
                    url = md["s"]["u"]
                elif "p" in md and md["p"]:
                    url = md["p"][-1].get("u")
                if url:
                    url = url.replace("&amp;", "&")
                    images.append(url)

            if images:
                images = images[:10]
                log.info(f"JSON gallery via gallery_data: {len(images)} images for {link}")
                return images

        # 2) Если is_gallery True, но gallery_data нет — старый путь по media_metadata
        if post.get("is_gallery") and media and not images:
            for md in media.values():
                url = None
                if "s" in md and "u" in md["s"]:
                    url = md["s"]["u"]
                elif "p" in md and md["p"]:
                    url = md["p"][-1].get("u")
                if url:
                    url = url.replace("&amp;", "&")
                    images.append(url)
            if images:
                images = images[:10]
                log.info(f"JSON gallery via media_metadata only: {len(images)} images for {link}")
                return images

        # 3) Обычное изображение
        for key in ("url_overridden_by_dest", "url"):
            u = post.get(key)
            if u and any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                images.append(u.replace("&amp;", "&"))
                log.info(f"JSON single image from {key} for {link}")
                return images

        # 4) preview.source
        preview = post.get("preview")
        if preview and "images" in preview and preview["images"]:
            source = preview["images"][0].get("source")
            if source and "url" in source:
                images.append(source["url"].replace("&amp;", "&"))
                log.info(f"JSON preview image for {link}")
                return images

        log.info(f"JSON: no images found in post data for {link}")

    except Exception as e:
        log.error(f"Error fetching full images json for {link}: {e}")

    return images


log.info("Bot started (RSS mode)!")

# -----------------------------
# MAIN LOOP
# -----------------------------
while True:
    try:
        feed = fetch_feed(RSS_URL)
        log.info(f"Fetched feed with {len(feed.entries)} entries")

        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            post_id = extract_post_id(link)

            raw_author = entry.get("author", "") or ""
            author_norm = normalize_author(raw_author)

            title = getattr(entry, "title", "") or ""
            title_lower = title.lower()

            # Фильтр по ключевым словам
            title_matches_keyword = any(kw in title_lower for kw in KEYWORDS)

            author_ok = author_norm in TRACKED_USERS_NORMALIZED
            keyword_ok = ENABLE_KEYWORD_FILTER == 1 and title_matches_keyword

            log.info(
                f"ENTRY post_id={post_id}, raw_author='{raw_author}', "
                f"author_norm='{author_norm}', title='{title}', "
                f"author_ok={author_ok}, keyword_ok={keyword_ok}, "
                f"title_matches_keyword={title_matches_keyword}"
            )

            if post_id in seen_posts:
                continue

            if not (author_ok or keyword_ok):
                continue

            summary = entry.summary

            # Пытаемся взять нормальные картинки через JSON
            image_urls = get_images_from_reddit(link)

            # Если не получилось — fallback к превью из RSS
            if not image_urls:
                fallback = extract_first_image_from_html(summary)
                if fallback:
                    image_urls = [fallback]

            if author_ok and keyword_ok:
                source_label = "tracked user + keyword match"
            elif author_ok:
                source_label = "tracked user"
            else:
                matched = [kw for kw in KEYWORDS if kw in title_lower]
                source_label = f"keyword match: {','.join(matched) or 'unknown'}"

            author_html = escape_html(author_norm or "unknown")
            title_html = escape_html(title)
            source_html = escape_html(source_label)

            message = (
                f"🕵️ New post ({source_html})\n\n"
                f"<b>Author:</b> {author_html}\n\n"
                f"<b>{title_html}</b>\n"
                f'<a href="{link}">Open post</a>'
            )

            # Отправка: одна картинка или альбом
            if image_urls:
                if len(image_urls) == 1:
                    bot.send_photo(
                        chat_id=CHAT_ID,
                        photo=image_urls[0],
                        caption=message,
                        parse_mode="HTML",
                    )
                else:
                    media = [InputMediaPhoto(media=url) for url in image_urls]
                    media[0].caption = message
                    media[0].parse_mode = "HTML"
                    bot.send_media_group(
                        chat_id=CHAT_ID,
                        media=media,
                    )
            else:
                bot.send_message(
                    chat_id=CHAT_ID,
                    text=message,
                    parse_mode="HTML",
                )

            log.info(
                f"Sent post {post_id} from {author_norm} "
                f"(author_ok={author_ok}, keyword_ok={keyword_ok}, images={len(image_urls)})"
            )

            seen_posts.add(post_id)
            save_seen(seen_posts)

    except Exception as e:
        log.error(f"Error in main loop: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)