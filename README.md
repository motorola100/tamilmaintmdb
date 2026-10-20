# torpl — PostgreSQL schema update

## What changed
- Replaces SQLite with PostgreSQL (`DATABASE_URL`).
- Creates exactly three application tables: `tamilmv_tmdb_movies`, `tamilmv_torrent_items`, `tamilmv_magnet_links`.
- Stores every distinct magnet URI separately with its inferred quality, info hash, and optional file size.
- Does not store a listing if its detail page contains no magnet links.
- If a confident TMDB title match is found, the torrent poster field is set to NULL; the UI can use the TMDB poster. The torrent title and screenshots remain on `tamilmv_torrent_items`.
- If the title does not match TMDB, the torrent poster URL can be saved on the torrent row.
- Series releases store `release_kind`, season range, and episode range. For a season pack where episode count is not explicit, episode range remains NULL instead of guessing.

## Setup (Windows PowerShell)
1. Install PostgreSQL and create a database, for example:
   `CREATE DATABASE tamilmv_db;`
2. Copy `.env.example` to `.env` and set `DATABASE_URL` and `TMDB_API_KEY`.
3. Install dependencies:
   `python -m pip install -r requirements.txt`
4. Start the app:
   `python app.py`
5. Open `http://127.0.0.1:5000`.

Tables are initialized on app start. You can also run `schema.sql` against the database manually.

## Series coverage examples
- `Show S01E02` → `single_episode`, season 1, episode 2–2.
- `Show S01E01-E04` → `episode_pack`, season 1, episode 1–4.
- `Show Season 1 Complete` → `season_pack`, season 1, episode range unknown.
- `Show S01-S03 Complete` → `multi_season_pack`, seasons 1–3.

## Notes
- Title matching needs a TMDB API key. Without it, no TMDB match can be established and torrent posters remain eligible for storage.
- The scraper only extracts magnets actually present in page HTML; it does not invent/expand short magnet URIs.
- This package is syntax-checked but has not been live-tested against the current third-party site's markup or your local PostgreSQL credentials.
