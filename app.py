import os
import json
import time
import logging
import sqlite3
import threading
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TAMILMV_URL = os.getenv("TAMILMV_URL", "https://www.1tamilmv.lease/")
DATABASE_PATH = os.getenv("DATABASE_PATH", "movies.db")
MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "8")))
CHECK_INTERVAL_SECONDS = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "60")))
INITIAL_MOVIE_LIMIT = max(1, int(os.getenv("INITIAL_MOVIE_LIMIT", "30")))
PORT = int(os.getenv("PORT", "5000"))

app = Flask(__name__)
db_lock = threading.Lock()
fetch_lock = threading.Lock()
status_lock = threading.Lock()
status = {
    "is_loading": False,
    "last_update": None,
    "last_error": None,
    "last_new_count": 0,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.7,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
session.mount("https://", adapter)
session.mount("http://", adapter)


def get_connection():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db_lock:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS movies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL UNIQUE,
                    poster_url TEXT,
                    screenshots_json TEXT NOT NULL DEFAULT '[]',
                    details_json TEXT NOT NULL DEFAULT '[]',
                    magnet_key TEXT UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_movies_created_at ON movies(created_at DESC)")
            # Each quality/torrent is stored as its own database row.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS movie_magnets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    movie_id INTEGER NOT NULL,
                    magnet_url TEXT NOT NULL,
                    magnet_key TEXT NOT NULL UNIQUE,
                    quality TEXT NOT NULL DEFAULT 'Unknown',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_movie_magnets_movie_id ON movie_magnets(movie_id)")
    logger.info("Database ready: %s", DATABASE_PATH)


def normalize_magnet(magnet):
    if not magnet:
        return None
    return unquote(magnet.strip())


def magnet_dedupe_key(magnet):
    """Use a BitTorrent info hash when available, otherwise the full URI."""
    if not magnet:
        return None
    import re
    from urllib.parse import parse_qs
    parsed = urlparse(normalize_magnet(magnet))
    params = parse_qs(parsed.query)
    for value in params.get("xt", []):
        match = re.search(r"urn:btih:([A-Za-z0-9]+)", value, re.I)
        if match:
            return "btih:" + match.group(1).lower()
    return normalize_magnet(magnet)


def absolute_url(url, base):
    if not url:
        return None
    return urljoin(base.rstrip("/") + "/", url)


def extract_image_url(container, base_url):
    if not container:
        return None
    for img in container.find_all("img"):
        src = img.get("data-src") or img.get("src") or img.get("data-original")
        if not src:
            continue
        low = src.lower()
        if any(word in low for word in ("avatar", "icon", "emoji", ".svg")):
            continue
        return absolute_url(src, base_url)
    return None


def extract_quality(text):
    import re
    match = re.search(r"\b(2160p|1080p|720p|480p|4k|uhd|fhd|hd)\b", text or "", re.I)
    return match.group(1) if match else "Unknown"


def scrape_movie_details(title, source_url, poster_url=None):
    response = session.get(source_url, headers=HEADERS, timeout=(10, 20))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    heading = soup.find("h1")
    actual_title = heading.get_text(" ", strip=True) if heading else title

    magnets = []
    import re
    magnet_candidates = []
    for anchor in soup.find_all("a"):
        for candidate in (
            anchor.get("href", ""),
            anchor.get("data-clipboard-text", ""),
            anchor.get("data-magnet", ""),
            anchor.get("data-href", ""),
            anchor.get_text(" ", strip=True),
        ):
            if candidate:
                magnet_candidates.append(candidate.strip())
    # Some templates encode magnet links in attributes or HTML entities.
    html_text = unquote(str(soup))
    magnet_candidates.extend(re.findall(r"magnet:[^\"'<>\s]+", html_text, flags=re.I))
    for candidate in magnet_candidates:
        candidate = unquote(candidate.strip()).replace("&amp;", "&")
        match = re.search(r"(magnet:[^\"'<>\s]+)", candidate, flags=re.I)
        if match:
            magnet = match.group(1).rstrip("),;")
            if magnet.lower().startswith("magnet:") and magnet not in magnets:
                magnets.append(magnet)

    screenshots = []
    content = soup.find("div", class_="ipsContained")
    image_scope = content or soup
    for img in image_scope.find_all("img"):
        src = img.get("data-src") or img.get("src") or img.get("data-original")
        if not src:
            continue
        src_abs = absolute_url(src, source_url)
        low = src_abs.lower()
        if any(word in low for word in ("avatar", "icon", "emoji", ".svg", "logo")):
            continue
        if src_abs not in screenshots:
            screenshots.append(src_abs)
        if len(screenshots) >= 5:
            break

    if not poster_url:
        poster_url = extract_image_url(soup.find("div", class_="ipsType_break ipsContained"), source_url)
    if not poster_url:
        poster_url = extract_image_url(content, source_url)

    details = []
    if magnets:
        for magnet in magnets:
            details.append({
                "title": actual_title,
                "magnet": magnet,
                "screenshot": screenshots[0] if screenshots else None,
                "screenshots": screenshots,
                "type": "magnet",
                "quality": extract_quality(actual_title + " " + magnet),
            })
    else:
        details.append({
            "title": actual_title,
            "magnet": None,
            "screenshot": screenshots[0] if screenshots else None,
            "screenshots": screenshots,
            "type": "none",
            "message": "No magnet links available",
        })

    return {
        "title": actual_title,
        "source_url": source_url,
        "poster_url": poster_url,
        "screenshots": screenshots,
        "details": details,
        "magnets": magnets,
    }


def scrape_latest_movies(limit):
    if not TAMILMV_URL:
        raise RuntimeError("TAMILMV_URL is not configured")

    response = session.get(TAMILMV_URL, headers=HEADERS, timeout=(10, 25))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    containers = soup.find_all("div", class_="ipsType_break ipsContained")
    if not containers:
        # A fallback for minor layout changes.
        containers = soup.select("h2 a[href], h3 a[href], .ipsDataItem_title a[href]")

    candidates = []
    seen_urls = set()
    for container in containers:
        anchor = container if getattr(container, "name", None) == "a" else container.find("a", href=True)
        if not anchor:
            continue
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href")
        if not title or not href:
            continue
        source_url = absolute_url(href, TAMILMV_URL)
        if source_url in seen_urls or source_url.rstrip("/") == TAMILMV_URL.rstrip("/"):
            continue
        seen_urls.add(source_url)
        poster = extract_image_url(container, TAMILMV_URL)
        candidates.append((title, source_url, poster))
        if len(candidates) >= limit:
            break

    if not candidates:
        logger.warning("No movie links found. The source website may have changed its layout.")
        return []

    records = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(scrape_movie_details, title, url, poster): (title, url, poster)
            for title, url, poster in candidates
        }
        for future in as_completed(futures):
            title, url, poster = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:
                logger.warning("Could not fetch details for %s: %s", title, exc)
                records.append({
                    "title": title,
                    "source_url": url,
                    "poster_url": poster,
                    "screenshots": [],
                    "magnets": [],
                    "details": [{
                        "title": title, "magnet": None, "screenshot": None,
                        "screenshots": [], "type": "error",
                        "message": f"Could not load details: {str(exc)[:120]}",
                    }],
                })

    # Preserve the source page's order rather than thread completion order.
    order = {url: index for index, (_, url, _) in enumerate(candidates)}
    records.sort(key=lambda record: order.get(record["source_url"], 999999))
    return records


def save_movies(records):
    """Save movie metadata and every quality-specific magnet link without duplicates."""
    inserted_movies = 0
    inserted_magnets = 0
    with db_lock:
        with get_connection() as conn:
            for movie in records:
                title = (movie.get("title") or "Untitled").strip()
                source_url = movie.get("source_url")
                if not source_url:
                    continue

                details = movie.get("details") or []
                magnets = []
                for item in details:
                    magnet = normalize_magnet(item.get("magnet"))
                    if magnet and magnet not in magnets:
                        magnets.append(magnet)
                for raw_magnet in movie.get("magnets") or []:
                    magnet = normalize_magnet(raw_magnet)
                    if magnet and magnet not in magnets:
                        magnets.append(magnet)

                # Keep the legacy single-magnet column NULL. All links are in movie_magnets.
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO movies
                    (title, source_url, poster_url, screenshots_json, details_json, magnet_key, updated_at)
                    VALUES (?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
                """, (
                    title, source_url, movie.get("poster_url"),
                    json.dumps(movie.get("screenshots") or [], ensure_ascii=False),
                    json.dumps(details, ensure_ascii=False),
                ))
                inserted_movies += cursor.rowcount
                row = conn.execute("SELECT id FROM movies WHERE source_url = ?", (source_url,)).fetchone()
                if not row:
                    continue
                movie_id = row["id"]

                conn.execute("""
                    UPDATE movies SET title = ?, poster_url = COALESCE(?, poster_url),
                        screenshots_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """, (
                    title, movie.get("poster_url"),
                    json.dumps(movie.get("screenshots") or [], ensure_ascii=False), movie_id,
                ))

                for magnet in magnets:
                    key = magnet_dedupe_key(magnet)
                    if not key:
                        continue
                    matched = next((d for d in details if normalize_magnet(d.get("magnet")) == magnet), {})
                    quality = matched.get("quality") or extract_quality(title + " " + magnet)
                    cursor = conn.execute("""
                        INSERT OR IGNORE INTO movie_magnets (movie_id, magnet_url, magnet_key, quality)
                        VALUES (?, ?, ?, ?)
                    """, (movie_id, magnet, key, quality))
                    inserted_magnets += cursor.rowcount

                # Sync details_json from all magnet rows, including older stored quality links.
                stored = conn.execute(
                    "SELECT magnet_url, quality FROM movie_magnets WHERE movie_id = ? ORDER BY id",
                    (movie_id,),
                ).fetchall()
                screenshots = movie.get("screenshots") or []
                if stored:
                    full_details = [{
                        "title": title,
                        "magnet": item["magnet_url"],
                        "quality": item["quality"],
                        "screenshot": screenshots[0] if screenshots else None,
                        "screenshots": screenshots,
                        "type": "magnet",
                    } for item in stored]
                    conn.execute("UPDATE movies SET details_json = ? WHERE id = ?",
                                 (json.dumps(full_details, ensure_ascii=False), movie_id))
    logger.info("Saved %s new movies and %s new magnet links", inserted_movies, inserted_magnets)
    return inserted_movies


def run_fetch_cycle():
    if not fetch_lock.acquire(blocking=False):
        logger.info("Previous fetch cycle is still running; skipping this cycle.")
        return
    with status_lock:
        status["is_loading"] = True
        status["last_error"] = None
    try:
        # The first run stores up to the latest 30. Later cycles also scan the latest
        # 30, and SQLite uniqueness prevents already-seen movies from being inserted.
        records = scrape_latest_movies(INITIAL_MOVIE_LIMIT)
        added = save_movies(records)
        with status_lock:
            status["last_update"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            status["last_new_count"] = added
        logger.info("Fetch cycle complete: %s inspected, %s new movies stored", len(records), added)
    except Exception as exc:
        logger.exception("Movie fetch cycle failed: %s", exc)
        with status_lock:
            status["last_error"] = str(exc)[:300]
    finally:
        with status_lock:
            status["is_loading"] = False
        fetch_lock.release()


def background_worker():
    logger.info("Automatic movie checker started; interval=%s seconds", CHECK_INTERVAL_SECONDS)
    while True:
        run_fetch_cycle()
        time.sleep(CHECK_INTERVAL_SECONDS)


def get_movie_rows():
    with get_connection() as conn:
        return conn.execute("""
            SELECT id, title, source_url, poster_url, screenshots_json, details_json, created_at
            FROM movies ORDER BY id DESC
        """).fetchall()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/movies")
def api_movies():
    rows = get_movie_rows()
    return jsonify({
        "movies": [row["title"] for row in rows],
        "items": [{
            "id": row["id"],
            "title": row["title"],
            "source_url": row["source_url"],
            "poster_url": row["poster_url"],
            "screenshots": json.loads(row["screenshots_json"] or "[]"),
            "created_at": row["created_at"],
        } for row in rows],
        "total": len(rows),
        "last_update": status["last_update"],
    })


@app.route("/api/movie/<int:index>")
def api_movie_details(index):
    rows = get_movie_rows()
    if index < 0 or index >= len(rows):
        return jsonify({"error": "Movie not found"}), 404
    row = rows[index]
    screenshots = json.loads(row["screenshots_json"] or "[]")
    with get_connection() as conn:
        magnet_rows = conn.execute(
            "SELECT magnet_url, quality FROM movie_magnets WHERE movie_id = ? ORDER BY id",
            (row["id"],),
        ).fetchall()
    if magnet_rows:
        details = [{
            "title": row["title"], "magnet": item["magnet_url"], "quality": item["quality"],
            "screenshot": screenshots[0] if screenshots else None,
            "screenshots": screenshots, "type": "magnet",
        } for item in magnet_rows]
    else:
        details = json.loads(row["details_json"] or "[]")
    return jsonify({
        "title": row["title"], "details": details, "poster_url": row["poster_url"],
        "screenshots": screenshots, "source_url": row["source_url"],
    })


@app.route("/api/status")
def api_status():
    rows = get_movie_rows()
    with status_lock:
        current = dict(status)
    current["movies_count"] = len(rows)
    current["interval_seconds"] = CHECK_INTERVAL_SECONDS
    current["database"] = DATABASE_PATH
    return jsonify(current)


if __name__ == "__main__":
    init_db()
    # Start one worker process. Do not enable Flask's reloader, which would create
    # duplicate background workers.
    worker = threading.Thread(target=background_worker, name="movie-auto-fetch", daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
