-- Exactly three application tables; every table starts with tamilmv_.
-- This file matches the schema created by app.py:init_db().
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
        CHECK (release_kind IN ('movie','single_episode','episode_pack','season_pack','multi_season_pack','unknown')),
    season_start INTEGER,
    season_end INTEGER,
    episode_start INTEGER,
    episode_end INTEGER,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS tamilmv_magnet_links (
    id BIGSERIAL PRIMARY KEY,
    torrent_item_id BIGINT NOT NULL REFERENCES tamilmv_torrent_items(id) ON DELETE CASCADE,
    magnet_url TEXT NOT NULL,
    quality TEXT,
    file_size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (torrent_item_id, magnet_url)
);
CREATE INDEX IF NOT EXISTS idx_tamilmv_torrent_tmdb ON tamilmv_torrent_items(tmdb_item_id);
CREATE INDEX IF NOT EXISTS idx_tamilmv_torrent_type ON tamilmv_torrent_items(media_type);
CREATE INDEX IF NOT EXISTS idx_tamilmv_magnets_item ON tamilmv_magnet_links(torrent_item_id);
