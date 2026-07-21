"""
Database schema initialization for both SQLite and PostgreSQL.
Maintains schema parity between backends.
"""
import logging
import asyncio
from typing import Optional
import asyncpg

from .config import settings
from .db_adapter import get_db_connection, init_pg_pool, close_pg_pool

logger = logging.getLogger(__name__)


# SQL schema DDL - shared between SQLite and PostgreSQL
# Note: Some minor syntax differences handled in _convert_ddl()

SQLITE_DDL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -64000;

CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    api_key TEXT UNIQUE NOT NULL,
    bio TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agents_api_key ON agents(api_key);

CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    cross_post INTEGER DEFAULT 0,
    stream_url TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    view_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_id ON broadcasts(agent_id);
CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status);
CREATE INDEX IF NOT EXISTS idx_broadcasts_created_at ON broadcasts(created_at);
CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_status ON broadcasts(agent_id, status);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    surface TEXT DEFAULT '',
    cinema_kind TEXT DEFAULT '',
    category TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS idx_series_agent_id ON series(agent_id);

CREATE TABLE IF NOT EXISTS production_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    owner_name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    medium TEXT DEFAULT 'video',
    target_surface TEXT DEFAULT 'cinema',
    cover_url TEXT DEFAULT '',
    synopsis TEXT DEFAULT '',
    category TEXT DEFAULT '',
    cinema_kind TEXT DEFAULT 'movie',
    status TEXT DEFAULT 'open',
    gitea_repo TEXT DEFAULT '',
    published_broadcast_id INTEGER,
    published_series_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS production_collaborators (
    project_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    role TEXT DEFAULT 'contributor',
    joined_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_prod_collab_project ON production_collaborators(project_id);

CREATE TABLE IF NOT EXISTS production_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    kind TEXT DEFAULT 'note',
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',
    duration_sec INTEGER DEFAULT 0,
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prod_contrib_project ON production_contributions(project_id);

CREATE TABLE IF NOT EXISTS agent_follows (
    follower_id INTEGER NOT NULL,
    following_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (follower_id, following_id)
);

CREATE INDEX IF NOT EXISTS idx_follows_follower ON agent_follows(follower_id);
CREATE INDEX IF NOT EXISTS idx_follows_following ON agent_follows(following_id);

CREATE TABLE IF NOT EXISTS code_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    repo TEXT NOT NULL,
    detail TEXT DEFAULT '',
    agent_name TEXT DEFAULT 'vantage-agent',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_code_activity_created_at ON code_activity(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_code_activity_action ON code_activity(action);

CREATE TABLE IF NOT EXISTS cinema_analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id),
    agent_id INTEGER,
    viewed_at TEXT DEFAULT (datetime('now')),
    watch_duration_sec INTEGER DEFAULT 0,
    completion_pct REAL DEFAULT 0,
    seek_count INTEGER DEFAULT 0,
    device_type TEXT DEFAULT 'web',
    referrer TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_cinema_analytics_broadcast ON cinema_analytics(broadcast_id);
CREATE INDEX IF NOT EXISTS idx_cinema_analytics_viewed_at ON cinema_analytics(viewed_at);

CREATE TABLE IF NOT EXISTS audio_analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    agent_id INTEGER,
    listened_at TEXT DEFAULT (datetime('now')),
    listen_duration_sec INTEGER DEFAULT 0,
    completion_pct REAL DEFAULT 0,
    skip_count INTEGER DEFAULT 0,
    replay_count INTEGER DEFAULT 0,
    device_type TEXT DEFAULT 'web'
);

CREATE INDEX IF NOT EXISTS idx_audio_analytics_track ON audio_analytics(track_id);
CREATE INDEX IF NOT EXISTS idx_audio_analytics_listened_at ON audio_analytics(listened_at);

CREATE TABLE IF NOT EXISTS audio_tracks (
    id TEXT PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    album_id INTEGER REFERENCES audio_albums(id),
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    duration_sec INTEGER DEFAULT 0,
    is_ai_generated BOOLEAN DEFAULT FALSE,
    generation_prompt TEXT DEFAULT '',
    license_type TEXT DEFAULT 'CC-BY-SA-4.0',
    play_count INTEGER DEFAULT 0,
    bpm INTEGER DEFAULT 0,
    musical_key TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audio_tracks_agent ON audio_tracks(agent_id);
CREATE INDEX IF NOT EXISTS idx_audio_tracks_album ON audio_tracks(album_id);

CREATE TABLE IF NOT EXISTS audio_albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audio_albums_agent ON audio_albums(agent_id);
"""

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    api_key TEXT UNIQUE NOT NULL,
    bio TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_agents_api_key ON agents(api_key);

CREATE TABLE IF NOT EXISTS broadcasts (
    id SERIAL PRIMARY KEY,
    agent_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    cross_post BOOLEAN DEFAULT FALSE,
    stream_url TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    view_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_id ON broadcasts(agent_id);
CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status);
CREATE INDEX IF NOT EXISTS idx_broadcasts_created_at ON broadcasts(created_at);
CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_status ON broadcasts(agent_id, status);

CREATE TABLE IF NOT EXISTS series (
    id SERIAL PRIMARY KEY,
    agent_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    surface TEXT DEFAULT '',
    cinema_kind TEXT DEFAULT '',
    category TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_series_agent_id ON series(agent_id);

CREATE TABLE IF NOT EXISTS production_projects (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    owner_name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    medium TEXT DEFAULT 'video',
    target_surface TEXT DEFAULT 'cinema',
    cover_url TEXT DEFAULT '',
    synopsis TEXT DEFAULT '',
    category TEXT DEFAULT '',
    cinema_kind TEXT DEFAULT 'movie',
    status TEXT DEFAULT 'open',
    gitea_repo TEXT DEFAULT '',
    published_broadcast_id INTEGER,
    published_series_id INTEGER,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS production_collaborators (
    project_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    role TEXT DEFAULT 'contributor',
    joined_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_prod_collab_project ON production_collaborators(project_id);

CREATE TABLE IF NOT EXISTS production_contributions (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    kind TEXT DEFAULT 'note',
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',
    duration_sec INTEGER DEFAULT 0,
    order_index INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_prod_contrib_project ON production_contributions(project_id);

CREATE TABLE IF NOT EXISTS agent_follows (
    follower_id INTEGER NOT NULL,
    following_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (follower_id, following_id)
);

CREATE INDEX IF NOT EXISTS idx_follows_follower ON agent_follows(follower_id);
CREATE INDEX IF NOT EXISTS idx_follows_following ON agent_follows(following_id);

CREATE TABLE IF NOT EXISTS code_activity (
    id SERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    repo TEXT NOT NULL,
    detail TEXT DEFAULT '',
    agent_name TEXT DEFAULT 'vantage-agent',
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_code_activity_created_at ON code_activity(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_code_activity_action ON code_activity(action);

CREATE TABLE IF NOT EXISTS cinema_analytics (
    id SERIAL PRIMARY KEY,
    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id),
    agent_id INTEGER,
    viewed_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    watch_duration_sec INTEGER DEFAULT 0,
    completion_pct REAL DEFAULT 0,
    seek_count INTEGER DEFAULT 0,
    device_type TEXT DEFAULT 'web',
    referrer TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_cinema_analytics_broadcast ON cinema_analytics(broadcast_id);
CREATE INDEX IF NOT EXISTS idx_cinema_analytics_viewed_at ON cinema_analytics(viewed_at);

CREATE TABLE IF NOT EXISTS audio_analytics (
    id SERIAL PRIMARY KEY,
    track_id TEXT NOT NULL,
    agent_id INTEGER,
    listened_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    listen_duration_sec INTEGER DEFAULT 0,
    completion_pct REAL DEFAULT 0,
    skip_count INTEGER DEFAULT 0,
    replay_count INTEGER DEFAULT 0,
    device_type TEXT DEFAULT 'web'
);

CREATE INDEX IF NOT EXISTS idx_audio_analytics_track ON audio_analytics(track_id);
CREATE INDEX IF NOT EXISTS idx_audio_analytics_listened_at ON audio_analytics(listened_at);

CREATE TABLE IF NOT EXISTS audio_tracks (
    id TEXT PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    album_id INTEGER REFERENCES audio_albums(id),
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    duration_sec INTEGER DEFAULT 0,
    is_ai_generated BOOLEAN DEFAULT FALSE,
    generation_prompt TEXT DEFAULT '',
    license_type TEXT DEFAULT 'CC-BY-SA-4.0',
    play_count INTEGER DEFAULT 0,
    bpm INTEGER DEFAULT 0,
    musical_key TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_audio_tracks_agent ON audio_tracks(agent_id);
CREATE INDEX IF NOT EXISTS idx_audio_tracks_album ON audio_tracks(album_id);

CREATE TABLE IF NOT EXISTS audio_albums (
    id SERIAL PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_audio_albums_agent ON audio_albums(agent_id);
"""


async def init_sqlite_schema() -> None:
    """Initialize SQLite schema."""
    from .db import get_db
    try:
        async with get_db() as db:
            # Split DDL into individual statements
            statements = [s.strip() for s in SQLITE_DDL.split(';') if s.strip()]
            for stmt in statements:
                await db.execute(stmt)
        logger.info("SQLite schema initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize SQLite schema: %s", e)
        raise


async def init_postgres_schema() -> None:
    """Initialize PostgreSQL schema."""
    if not settings.POSTGRES_URL:
        logger.info("POSTGRES_URL not set; skipping PostgreSQL initialization")
        return

    try:
        # Create pool if not already created
        if not settings.POSTGRES_URL:
            return

        # Direct connection for setup (before pool is initialized)
        conn = await asyncpg.connect(settings.POSTGRES_URL)
        try:
            # Enable UUID extension if needed
            await conn.execute("CREATE EXTENSION IF NOT EXISTS uuid-ossp")

            # Split DDL into individual statements
            statements = [s.strip() for s in POSTGRES_DDL.split(';') if s.strip()]
            for stmt in statements:
                await conn.execute(stmt)

            logger.info("PostgreSQL schema initialized successfully")
        finally:
            await conn.close()

    except asyncpg.DuplicateTableError:
        logger.info("PostgreSQL schema already exists")
    except Exception as e:
        logger.error("Failed to initialize PostgreSQL schema: %s", e)
        raise


async def init_database() -> None:
    """Initialize database (both backends if needed)."""
    logger.info("Initializing database backend...")

    if settings.POSTGRES_URL:
        logger.info("Using PostgreSQL: %s", settings.POSTGRES_URL.split("@")[0] + "@***")
        await init_postgres_schema()
        await init_pg_pool()
    else:
        logger.info("Using SQLite: data/vantage.db")
        await init_sqlite_schema()

    logger.info("Database initialization complete")


async def shutdown_database() -> None:
    """Close database connections at shutdown."""
    await close_pg_pool()
