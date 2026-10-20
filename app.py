import os
import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import psycopg2
from psycopg2.extras import RealDictCursor, Json
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from scraper import (
    fetch_movie_list,
    fetch_movie_details,
    infer_series_coverage,
    clean_title_for_tmdb,
)

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TAMILMV_URL = (
    os.getenv("TAMILMV_URL") or "https://www.1tamilmv.solar/"
).strip().rstrip("/") + "/"
MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "8")))
INITIAL_LIMIT = max(1, int(os.getenv("INITIAL_LIMIT", "30")))
CHECK_INTERVAL_SECONDS = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "60")))

fetch_lock = threading.Lock()
status_lock = threading.Lock()
status = {
    "is_loading": False,
    "last_update": None,
    "last_error": None,
    "last_new_count": 0,
}

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


@contextmanager
def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set it in your .env file.")
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create exactly three application tables, all using the required prefix."""
    ddl = """
    CREATE TABLE IF NOT EXISTS tamilmv_tmdb_movies (
        id BIGSERIAL PRIMARY KEY,
        tmdb_id BIGINT NOT NULL,
        media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
        title TEXT NOT NULL,
        original_title TEXT,
        overview TEXT,
        poster_path TEXT,
        backdrop_path TEXT,
        release_date DATE,
        vote_average NUMERIC(4,2),
        vote_count INTEGER,
        genres JSONB NOT NULL DEFAULT '[]'::jsonb,
        raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (tmdb_id, media_type)
    );

    CREATE TABLE IF NOT EXISTS tamilmv_torrent_items (
        id BIGSERIAL PRIMARY KEY,
        source_id TEXT,
        source_url TEXT NOT NULL UNIQUE,
        torrent_title TEXT NOT NULL,
        media_type TEXT NOT NULL DEFAULT 'unknown'
            CHECK (media_type IN ('movie', 'tv', 'unknown')),
        tmdb_item_id BIGINT REFERENCES tamilmv_tmdb_movies(id) ON DELETE SET NULL,
        title_matches_tmdb BOOLEAN NOT NULL DEFAULT FALSE,
        torrent_poster_url TEXT,
        screenshots JSONB NOT NULL DEFAULT '[]'::jsonb,
        release_kind TEXT NOT NULL DEFAULT 'unknown'
            CHECK (release_kind IN (
                'movie', 'single_episode', 'episode_pack',
                'season_pack', 'multi_season_pack', 'unknown'
            )),
        season_start INTEGER,
        season_end INTEGER,
        episode_start INTEGER,
        episode_end INTEGER,
        scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (season_start IS NULL OR season_start >= 0),
        CHECK (season_end IS NULL OR season_end >= season_start),
        CHECK (episode_start IS NULL OR episode_start >= 0),
        CHECK (episode_end IS NULL OR episode_end >= episode_start)
    );

    CREATE TABLE IF NOT EXISTS tamilmv_magnet_links (
        id BIGSERIAL PRIMARY KEY,
        torrent_item_id BIGINT NOT NULL
            REFERENCES tamilmv_torrent_items(id) ON DELETE CASCADE,
        magnet_url TEXT NOT NULL,
        quality TEXT,
        file_size_bytes BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (torrent_item_id, magnet_url)
    );

    CREATE INDEX IF NOT EXISTS idx_tamilmv_torrent_tmdb
        ON tamilmv_torrent_items(tmdb_item_id);
    CREATE INDEX IF NOT EXISTS idx_tamilmv_torrent_type
        ON tamilmv_torrent_items(media_type);
    CREATE INDEX IF NOT EXISTS idx_tamilmv_torrent_series_coverage
        ON tamilmv_torrent_items(season_start, season_end, episode_start, episode_end);
    CREATE INDEX IF NOT EXISTS idx_tamilmv_magnets_item
        ON tamilmv_magnet_links(torrent_item_id);
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    logger.info("PostgreSQL schema initialized (3 tamilmv_ tables).")


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def extract_tmdb_title_year(torrent_title):
    """Extract the core movie/show name and release year from a torrent title."""
    title = " ".join((torrent_title or "").split())
    if not title:
        return "", None

    year_match = re.search(r"[\[(]?\b((?:19|20)\d{2})\b[\])] ?", title)
    year = int(year_match.group(1)) if year_match else None

    # Remove year and all release details before cleaning. This works whether
    # year appears directly after the name or after a season/episode marker.
    if year_match:
        before_year = title[:year_match.start()]
        after_year = title[year_match.end():]
        # If year follows S01E01 / Season 1, keep only the text before that tag.
        main_title = re.split(
            r"\b(?:S(?:EASON)?\s*0?\d{1,2}(?:\s*E(?:P(?:ISODE)?)?\s*0?\d{1,3})?|SEASON\s*0?\d{1,2}|EP(?:ISODE)?\s*0?\d{1,3})\b",
            before_year, maxsplit=1, flags=re.I,
        )[0]
        # Normally title comes before year; if not, use the leading title text.
        if not main_title.strip(" -_()[]{}|:"):
            main_title = after_year
    else:
        main_title = title

    # Release details commonly begin at parentheses/brackets, quality/audio tags,
    # season/episode tags, or a spaced hyphen separator.
    main_title = re.split(
        r"\s+-\s+|\s*[\[(]|\b(?:S(?:EASON)?\s*0?\d{1,2}|EP(?:ISODE)?\s*0?\d{1,3}|2160p|1080p|780p|720p|480p|360p|BluRay|WEB[- ]?DL|WEBRip)\b",
        main_title, maxsplit=1, flags=re.I,
    )[0]
    main_title = clean_title_for_tmdb(main_title.strip(" -_()[]{}|:"))
    return main_title, year


def tmdb_search(title, media_type):
    """Return the FIRST TMDB search result for the extracted title only."""
    if not TMDB_API_KEY or not title:
        logger.warning("TMDB search skipped: API key or title missing (title=%r)", title)
        return None

    endpoint_type = "tv" if media_type == "tv" else "movie"
    params = {
        "api_key": TMDB_API_KEY,
        "query": title,
        "include_adult": "false",
    }
    logger.info("TMDB SEARCH REQUEST: title=%r type=%s", title, endpoint_type)
    try:
        response = requests.get(
            f"{TMDB_BASE}/search/{endpoint_type}",
            params=params,
            timeout=(5, 12),
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        logger.info(
            "TMDB RESPONSE: status=%s result_count=%s title=%r type=%s",
            response.status_code, len(results), title, endpoint_type,
        )
        if not results:
            return None

        # User requested the first result exactly as TMDB returns it:
        # no year filter, similarity score, or 0.88 threshold.
        first = results[0]
        first["media_type"] = endpoint_type
        logger.info(
            "TMDB FIRST RESULT: query=%r result=%r id=%s type=%s",
            title, first.get("name") or first.get("title"), first.get("id"), endpoint_type,
        )
        return first
    except Exception:
        logger.exception("TMDB search failed for title %r", title)
        return None


def save_tmdb_item(candidate):
    title = candidate.get("name") or candidate.get("title") or "Unknown"
    original_title = candidate.get("original_name") or candidate.get("original_title")
    release_date = parse_date(
        candidate.get("first_air_date") or candidate.get("release_date")
    )
    genres = candidate.get("genre_ids") or []
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO tamilmv_tmdb_movies
                    (tmdb_id, media_type, title, original_title, overview,
                     poster_path, backdrop_path, release_date, vote_average,
                     vote_count, genres, raw_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tmdb_id, media_type) DO UPDATE SET
                    title = EXCLUDED.title,
                    original_title = EXCLUDED.original_title,
                    overview = EXCLUDED.overview,
                    poster_path = EXCLUDED.poster_path,
                    backdrop_path = EXCLUDED.backdrop_path,
                    release_date = EXCLUDED.release_date,
                    vote_average = EXCLUDED.vote_average,
                    vote_count = EXCLUDED.vote_count,
                    genres = EXCLUDED.genres,
                    raw_json = EXCLUDED.raw_json,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    candidate["id"],
                    candidate["media_type"],
                    title,
                    original_title,
                    candidate.get("overview"),
                    candidate.get("poster_path"),
                    candidate.get("backdrop_path"),
                    release_date,
                    candidate.get("vote_average"),
                    candidate.get("vote_count"),
                    Json(genres),
                    Json(candidate),
                ),
            )
            item_id = cur.fetchone()["id"]
        conn.commit()
    return item_id, title


def save_torrent_item(item, details):
    magnets = details.get("magnets") or []
    # Critical rule: never save a listing that has no usable magnet link.
    if not magnets:
        logger.info("Skipping %s: no magnet links found", item.get("title"))
        return False

    torrent_title = (details.get("title") or item.get("title") or "").strip()
    if not torrent_title:
        return False

    inferred = infer_series_coverage(torrent_title)
    detected_type = inferred["media_type"]

    # Search TMDB using only the extracted main title (e.g. Colony or Farzi).
    # Ignore year, episode/season, quality, audio, and file-size details.
    search_title, _release_year = extract_tmdb_title_year(torrent_title)
    logger.info(
        "TORRENT TITLE PARSED: original=%r search_title=%r type=%s",
        torrent_title, search_title, detected_type,
    )
    candidate = tmdb_search(search_title, detected_type)
    # If the inferred type has no results, try the other TMDB category.
    if candidate is None and search_title:
        alternate_type = "tv" if detected_type == "movie" else "movie"
        logger.info("No TMDB result for %r as %s; trying %s", search_title, detected_type, alternate_type)
        candidate = tmdb_search(search_title, alternate_type)
        if candidate:
            detected_type = candidate["media_type"]
            inferred["media_type"] = detected_type
    tmdb_id = None
    tmdb_title = None
    title_matches = False
    if candidate:
        tmdb_id, tmdb_title = save_tmdb_item(candidate)
        # We intentionally trust the first TMDB result returned for this query.
        title_matches = bool(candidate.get("poster_path"))

    # On a confident TMDB title match, the TMDB poster is authoritative.
    # Torrent poster is intentionally not saved; screenshot is still retained.
    # Always preserve the poster extracted from the TamilMV torrent post.
    # TMDB metadata matching must not replace or suppress the source poster.
    torrent_poster = details.get("poster_url")
    source_id = item.get("source_id") or item.get("movie_id")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO tamilmv_torrent_items
                    (source_id, source_url, torrent_title, media_type, tmdb_item_id,
                     title_matches_tmdb, torrent_poster_url, screenshots,
                     release_kind, season_start, season_end, episode_start, episode_end)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_url) DO UPDATE SET
                    torrent_title = EXCLUDED.torrent_title,
                    media_type = EXCLUDED.media_type,
                    tmdb_item_id = COALESCE(EXCLUDED.tmdb_item_id, tamilmv_torrent_items.tmdb_item_id),
                    title_matches_tmdb = EXCLUDED.title_matches_tmdb,
                    torrent_poster_url = EXCLUDED.torrent_poster_url,
                    screenshots = EXCLUDED.screenshots,
                    release_kind = EXCLUDED.release_kind,
                    season_start = EXCLUDED.season_start,
                    season_end = EXCLUDED.season_end,
                    episode_start = EXCLUDED.episode_start,
                    episode_end = EXCLUDED.episode_end,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    source_id,
                    item["url"],
                    torrent_title,
                    detected_type,
                    tmdb_id,
                    title_matches,
                    torrent_poster,
                    Json(details.get("screenshots", [])),
                    inferred["release_kind"],
                    inferred["season_start"],
                    inferred["season_end"],
                    inferred["episode_start"],
                    inferred["episode_end"],
                ),
            )
            torrent_id = cur.fetchone()["id"]

            # Save every distinct magnet URI separately (480p/720p/1080p/4K).
            for magnet in magnets:
                url = (magnet.get("url") or "").strip()

                if not url.lower().startswith("magnet:?"):
                    continue

                query = parse_qs(urlparse(url).query)
                xt = (query.get("xt") or [""])[0]

                if not re.fullmatch(
                    r"urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})",
                    xt,
                    re.IGNORECASE,
                ):
                    continue

                cur.execute(
                    """
                    INSERT INTO tamilmv_magnet_links
                        (torrent_item_id, magnet_url, quality, file_size_bytes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (torrent_item_id, magnet_url) DO NOTHING
                    """,
                    (
                        torrent_id,
                        url,
                        magnet.get("quality"),
                        magnet.get("file_size_bytes"),
                    ),
                )
        conn.commit()


def scrape_and_store():
    if not fetch_lock.acquire(blocking=False):
        return
    with status_lock:
        status["is_loading"] = True
        status["last_error"] = None
    try:
        listings = fetch_movie_list(TAMILMV_URL, limit=INITIAL_LIMIT)
        new_count = 0
        for listing in listings:
            try:
                details = fetch_movie_details(listing, TAMILMV_URL)
                if save_torrent_item(listing, details):
                    new_count += 1
            except Exception:
                logger.exception("Could not process listing %s", listing.get("title"))
        with status_lock:
            status["last_update"] = datetime.now(timezone.utc).isoformat()
            status["last_new_count"] = new_count
        logger.info(
            "Scrape cycle complete. Saved/updated %s magnet-bearing items.", new_count
        )
    except Exception as exc:
        logger.exception("Scrape cycle failed")
        with status_lock:
            status["last_error"] = str(exc)
    finally:
        with status_lock:
            status["is_loading"] = False
        fetch_lock.release()


def background_loop():
    # Initial fetch immediately, then one cycle per configured interval.
    while True:
        scrape_and_store()
        import time

        time.sleep(CHECK_INTERVAL_SECONDS)


def list_saved_items():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT t.id, t.torrent_title AS title, t.source_url AS url,
                       t.media_type, t.torrent_poster_url AS poster,
                       t.screenshots, t.release_kind, t.season_start, t.season_end,
                       t.episode_start, t.episode_end, t.title_matches_tmdb,
                       tm.title AS tmdb_title, tm.poster_path AS tmdb_poster_path,
                       tm.media_type AS tmdb_media_type,
                       COUNT(m.id)::int AS magnet_count
                FROM tamilmv_torrent_items t
                LEFT JOIN tamilmv_tmdb_movies tm ON tm.id = t.tmdb_item_id
                LEFT JOIN tamilmv_magnet_links m ON m.torrent_item_id = t.id
                GROUP BY t.id, tm.id
                ORDER BY t.created_at DESC
                """)
            rows = cur.fetchall()
            for row in rows:
                row["screenshots"] = row["screenshots"] or []
                tmdb_path = row.get("tmdb_poster_path")
                torrent_poster = row.get("poster")

                # Prefer the original poster scraped from the torrent post.
                # Use the TMDB poster only as a fallback when the source has none.
                if torrent_poster:
                    row["poster"] = torrent_poster
                elif tmdb_path:
                    row["poster"] = TMDB_IMAGE_BASE + tmdb_path
                else:
                    row["poster"] = None
            return rows


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["GET", "POST"])
def start_movies():
    threading.Thread(target=scrape_and_store, daemon=True).start()
    return jsonify({"status": "fetching", "message": "Scrape cycle started."}), 202


@app.route("/api/movies")
def get_movies():
    try:
        movies = list_saved_items()
        return jsonify(
            {
                "movies": movies,
                "total": len(movies),
                "last_update": status["last_update"],
            }
        )
    except Exception as exc:
        logger.exception("Unable to list saved items")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/movie/<int:item_id>")
def get_movie_details(item_id):
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT t.*, tm.title AS tmdb_title, tm.poster_path AS tmdb_poster_path,
                           tm.overview AS tmdb_overview, tm.vote_average AS tmdb_vote_average,
                           tm.release_date AS tmdb_release_date
                    FROM tamilmv_torrent_items t
                    LEFT JOIN tamilmv_tmdb_movies tm ON tm.id = t.tmdb_item_id
                    WHERE t.id = %s
                    """,
                    (item_id,),
                )
                item = cur.fetchone()
                if not item:
                    return jsonify({"error": "Item not found"}), 404
                cur.execute(
                    """SELECT magnet_url AS magnet, quality, file_size_bytes
                       FROM tamilmv_magnet_links WHERE torrent_item_id = %s ORDER BY id""",
                    (item_id,),
                )
                item["details"] = cur.fetchall()
                item["screenshots"] = item.get("screenshots") or []
                if item.get("tmdb_poster_path"):
                    item["tmdb_poster_url"] = TMDB_IMAGE_BASE + item["tmdb_poster_path"]
                return jsonify(item)
    except Exception as exc:
        logger.exception("Unable to retrieve item")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/status")
def get_status():
    with status_lock:
        current = dict(status)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tamilmv_torrent_items")
                current["movies_count"] = cur.fetchone()[0]
    except Exception as exc:
        current["database_error"] = str(exc)
    return jsonify(current)


@app.route("/api/refresh", methods=["GET", "POST"])
def refresh_movies():
    threading.Thread(target=scrape_and_store, daemon=True).start()
    return (
        jsonify({"status": "fetching", "message": "A fresh scrape has been queued."}),
        202,
    )


def start_background_worker():
    worker = threading.Thread(
        target=background_loop, daemon=True, name="tamilmv-scraper"
    )
    worker.start()


if __name__ == "__main__":
    init_db()
    start_background_worker()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
