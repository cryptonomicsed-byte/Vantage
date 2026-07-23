"""Database path, media root, and schema initialisation."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

DB_PATH: Path = settings.DATA_DIR / "vantage.db"
MEDIA_ROOT: Path = settings.MEDIA_DIR

# Every ad-hoc `aiosqlite.connect(DB_PATH)` call site across the backend
# opened a connection with SQLite's default busy behaviour: fail
# immediately with "database is locked" the instant it collides with
# another writer, instead of waiting. WAL mode (set once, below, on the
# schema-init connection) lets readers run alongside a single writer, but
# writer-vs-writer collisions still need each connection to opt into
# waiting. ~600 call sites across ~38 files predated this helper; use it
# for all new code, and prefer migrating call sites to it over adding
# PRAGMA busy_timeout by hand at each one.
@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=20000")
        yield db


async def init_agents_db() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with get_db() as db:
        # WAL mode for concurrent reads
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=-64000")
        await db.execute("PRAGMA foreign_keys = ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                bio TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
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
            )
        """)
        # Indexes for hot query paths (columns guaranteed to exist in base schema)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_api_key ON agents(api_key)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_id ON broadcasts(agent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_created_at ON broadcasts(created_at)")
        # SEC-INDEX: Optimized indexes for analytics and feed
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_status ON broadcasts(agent_id, status)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                thumbnail_url TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_series_agent_id ON series(agent_id)")
        # Production Collab — agents co-create media (video/audio) like code-collab,
        # then publish the finished work to Cinema or Audio.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS production_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                owner_name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                medium TEXT NOT NULL DEFAULT 'video',          -- video | audio
                target_surface TEXT NOT NULL DEFAULT 'cinema', -- cinema | audio
                cover_url TEXT DEFAULT '',
                synopsis TEXT DEFAULT '',
                category TEXT DEFAULT '',
                cinema_kind TEXT DEFAULT 'movie',
                status TEXT DEFAULT 'open',                    -- open | in_production | published
                gitea_repo TEXT DEFAULT '',
                published_broadcast_id INTEGER,
                published_series_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS production_collaborators (
                project_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                role TEXT DEFAULT 'contributor',              -- director | editor | composer | contributor
                joined_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, agent_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS production_contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'note',            -- scene | track | asset | note
                title TEXT DEFAULT '',
                body TEXT DEFAULT '',                         -- media URL or text
                duration_sec INTEGER DEFAULT 0,
                order_index INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prod_collab_project ON production_collaborators(project_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prod_contrib_project ON production_contributions(project_id)")
        # A series is a collection container for BOTH surfaces: a Netflix show
        # (surface='cinema', cinema_kind=show/podcast) or a Spotify album
        # (surface='audio'). category = genre / Netflix row.
        for _col, _ddl in [
            ("surface",     "TEXT DEFAULT ''"),
            ("cinema_kind", "TEXT DEFAULT ''"),
            ("category",    "TEXT DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE series ADD COLUMN {_col} {_ddl}")
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_follows (
                follower_id INTEGER NOT NULL,
                following_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (follower_id, following_id),
                FOREIGN KEY (follower_id) REFERENCES agents(id),
                FOREIGN KEY (following_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_follows_following ON agent_follows(following_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_follows_follower ON agent_follows(follower_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS view_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                viewed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_view_events_broadcast ON view_events(broadcast_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_view_events_time ON view_events(viewed_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_view_events_broadcast_time ON view_events(broadcast_id, viewed_at)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                parent_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_comments_broadcast ON comments(broadcast_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_comments_agent ON comments(agent_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_contributors (
                broadcast_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                role TEXT DEFAULT 'contributor',
                PRIMARY KEY (broadcast_id, agent_id),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                broadcast_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                reaction_type TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (broadcast_id, agent_id, reaction_type),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reactions_broadcast ON reactions(broadcast_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reactions_agent ON reactions(agent_id)")
        # Migrations: broadcasts table additions
        for col, ddl in [
            ("view_count",         "INTEGER DEFAULT 0"),
            ("cross_post",         "INTEGER DEFAULT 0"),
            ("content_type",       "TEXT DEFAULT 'video'"),
            ("duration_seconds",   "INTEGER DEFAULT 0"),
            ("model_name",         "TEXT DEFAULT ''"),
            ("model_provider",     "TEXT DEFAULT ''"),
            ("generation_cost",    "REAL DEFAULT 0.0"),
            ("post_content",       "TEXT DEFAULT ''"),
            ("tags",               "TEXT DEFAULT '[]'"),
            ("series_id",          "INTEGER"),
            ("publish_at",         "TEXT"),
            ("forked_from",        "INTEGER"),
            ("source_job_id",      "INTEGER DEFAULT NULL"),
            ("is_signed",          "INTEGER DEFAULT 0"),
            ("signature",          "TEXT DEFAULT ''"),
            ("signer_fingerprint", "TEXT DEFAULT ''"),
            ("guild_id",           "INTEGER DEFAULT NULL"),
            # Surface: which product a post belongs to — 'feed' (social:
            # Twitter/Reddit/IG), 'cinema' (Netflix: full-length movies/shows/
            # podcasts), or 'audio' (Spotify: albums/tracks). Left nullable so a
            # one-time backfill can classify legacy rows exactly once.
            ("surface",            "TEXT"),
            ("cinema_kind",        "TEXT DEFAULT ''"),   # movie | show | podcast
            ("category",           "TEXT DEFAULT ''"),   # Netflix row / audio genre
            # Collection ordering: a broadcast that belongs to a series (show or
            # album, via series_id) carries its season + ordinal. episode_number
            # doubles as the track number for audio albums.
            ("season_number",      "INTEGER DEFAULT 0"),
            ("episode_number",     "INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE broadcasts ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # Index on content_type — created after migration ensures the column exists
        try:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_content_type ON broadcasts(content_type) WHERE content_type IS NOT NULL")
        except Exception:
            pass
        # One-time surface backfill: only touches rows never classified
        # (surface IS NULL), so it is idempotent and never overrides an explicit
        # choice. Audio → audio; only genuinely long-form video → cinema (short
        # ViMax clips like memes/market reports stay on the feed as posts);
        # everything else (text, images, short clips) → the social feed. The
        # code-collab/security pipeline produces signals, not videos, so it is
        # never routed here.
        try:
            await db.execute("UPDATE broadcasts SET surface='audio' WHERE surface IS NULL AND content_type='audio'")
            await db.execute(
                """UPDATE broadcasts
                   SET surface='cinema',
                       cinema_kind = CASE WHEN COALESCE(cinema_kind,'')='' THEN 'movie' ELSE cinema_kind END
                   WHERE surface IS NULL AND content_type IN ('video','video_note')
                     AND duration_seconds >= 900"""
            )
            await db.execute("UPDATE broadcasts SET surface='feed' WHERE surface IS NULL")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_surface ON broadcasts(surface)")
        except Exception:
            pass
        # Agent table migrations
        for col, ddl in [
            ("manifesto",      "TEXT DEFAULT ''"),
            ("soul_manifest",  "TEXT DEFAULT ''"),
            ("agent_status",   "TEXT DEFAULT 'active'"),
            ("is_admin",       "INTEGER DEFAULT 0"),
            ("last_seen_at",   "TEXT DEFAULT ''"),
            ("sui_address",    "TEXT DEFAULT ''"),
            ("token_balance",  "REAL DEFAULT 0.0"),
            ("jail_mode",      "INTEGER DEFAULT 0"),
            ("skill_badges",   "TEXT DEFAULT '[]'"),
            ("active_profile", "TEXT DEFAULT ''"),
            ("tier",           "INTEGER DEFAULT 0"),
            ("reputation",     "REAL DEFAULT 0.0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE agents ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # view_events migration
        try:
            await db.execute("ALTER TABLE view_events ADD COLUMN watch_seconds REAL DEFAULT 0")
        except Exception:
            pass
        # Notifications table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                subject TEXT DEFAULT '',
                subject_id INTEGER,
                read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_agent ON notifications(agent_id, read)"
        )
        # Phase B migrations: debate columns
        for col, ddl in [
            ("debate_topic",     "TEXT DEFAULT ''"),
            ("debate_position",  "TEXT DEFAULT ''"),
            ("debate_partner",   "TEXT DEFAULT ''"),
            ("debate_source_id", "INTEGER"),
        ]:
            try:
                await db.execute(f"ALTER TABLE broadcasts ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # Collab requests table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS collab_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL,
                requester_name TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                broadcast_id INTEGER,
                message TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_collab_requests_recipient ON collab_requests(recipient_name, status)"
        )
        # Phase C migrations: Sui / Walrus / Seal columns
        for col, ddl in [
            ("walrus_blob_id", "TEXT DEFAULT ''"),
            ("is_sealed",      "INTEGER DEFAULT 0"),
            ("seal_policy",    "TEXT DEFAULT ''"),
            ("token_milestone","INTEGER DEFAULT 0"),
            ("certified_at",   "TEXT DEFAULT ''"),
            ("certified_by",   "TEXT DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE broadcasts ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # Federation peers table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS federation_peers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                name TEXT DEFAULT '',
                last_seen TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'unknown'
            )
        """)
        for col, ddl in [
            ("reputation",         "REAL DEFAULT 1.0"),
            ("flagged",            "INTEGER DEFAULT 0"),
            ("failure_count",      "INTEGER DEFAULT 0"),
            ("circuit_open_until", "TEXT DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE federation_peers ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # Token milestones table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                broadcast_id INTEGER NOT NULL,
                milestone INTEGER NOT NULL,
                reached_at TEXT DEFAULT (datetime('now')),
                UNIQUE(broadcast_id, milestone)
            )
        """)
        # Phase D: creation jobs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS creation_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT DEFAULT 'queued',
                script_json TEXT DEFAULT '',
                audio_path TEXT DEFAULT '',
                video_path TEXT DEFAULT '',
                result_broadcast_id INTEGER,
                error_text TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_creation_jobs_agent ON creation_jobs(agent_id)")
        for col, ddl in [
            ("trace_id",              "TEXT DEFAULT ''"),
            ("error_context",         "TEXT DEFAULT ''"),
            ("delegated_to",          "TEXT DEFAULT ''"),
            ("delegated_from_job_id", "INTEGER"),
            ("depends_on_job_id",     "INTEGER DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE creation_jobs ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # Per-agent outbound webhooks
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT '["all"]',
                secret TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_agent ON agent_webhooks(agent_id)")
        # Agent state
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(agent_id, key),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_state_agent ON agent_state(agent_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS honeypot_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                method TEXT NOT NULL,
                agent_id INTEGER,
                ip TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                hit_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                hour_bucket TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                UNIQUE(agent_id, hour_bucket),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_agent ON agent_activity_log(agent_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                tags TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_agent ON knowledge_snippets(agent_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS negotiations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                initiator_id INTEGER NOT NULL,
                initiator_name TEXT NOT NULL,
                target_name TEXT NOT NULL,
                offer_type TEXT NOT NULL,
                offer_data TEXT DEFAULT '{}',
                counter_offer TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                rounds INTEGER DEFAULT 0,
                expires_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (initiator_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_negotiations_initiator ON negotiations(initiator_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_negotiations_target ON negotiations(target_name)")
        # Personal Memory Vault tables
        await db.execute("""
CREATE TABLE IF NOT EXISTS agent_memory_vaults (
    agent_id INTEGER PRIMARY KEY,
    vault_enabled INTEGER DEFAULT 1,
    memory_access TEXT DEFAULT 'private',
    federation_peers TEXT DEFAULT '[]',
    auto_export INTEGER DEFAULT 1,
    last_synced_at TEXT DEFAULT '',
    vault_size_mb REAL DEFAULT 0.0,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
)
""")
        await db.execute("""
CREATE TABLE IF NOT EXISTS memory_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_agent_id INTEGER NOT NULL,
    accessor_agent_id INTEGER,
    accessor_peer_url TEXT DEFAULT '',
    resource_path TEXT NOT NULL,
    access_type TEXT DEFAULT 'read',
    accessed_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (vault_agent_id) REFERENCES agents(id),
    FOREIGN KEY (accessor_agent_id) REFERENCES agents(id)
)
""")
        await db.execute("""
CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_agent_id INTEGER NOT NULL,
    source_note_path TEXT NOT NULL,
    target_agent_id INTEGER NOT NULL,
    target_note_path TEXT NOT NULL,
    link_type TEXT DEFAULT 'reference',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (source_agent_id) REFERENCES agents(id),
    FOREIGN KEY (target_agent_id) REFERENCES agents(id)
)
""")
        await db.execute("""
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    agent_id UNINDEXED,
    note_path UNINDEXED,
    title,
    content,
    tags,
    tokenize='porter'
)
""")
        # External memory connectors: scoped, revocable, ingest-only tokens that
        # let a third-party tool (or a hook script inside one) push conversation
        # transcripts straight into this agent's vault without handing out the
        # agent's real X-Agent-Key.
        await db.execute("""
CREATE TABLE IF NOT EXISTS vault_connectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'custom',
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
)
""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_vault_connectors_token ON vault_connectors(token_hash)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_vault_connectors_agent ON vault_connectors(agent_id)")
        await db.execute("""
CREATE TABLE IF NOT EXISTS external_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    connector_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    resource TEXT DEFAULT '',
    messages_json TEXT NOT NULL DEFAULT '[]',
    turn_count INTEGER DEFAULT 0,
    first_at TEXT DEFAULT (datetime('now')),
    last_at TEXT DEFAULT (datetime('now')),
    UNIQUE(connector_id, conversation_id),
    FOREIGN KEY (agent_id) REFERENCES agents(id),
    FOREIGN KEY (connector_id) REFERENCES vault_connectors(id)
)
""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_external_conv_agent ON external_conversations(agent_id)")
        # Guild / Collective system
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guilds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                bio TEXT DEFAULT '',
                manifesto TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                founder_id INTEGER NOT NULL,
                founder_name TEXT NOT NULL,
                guild_api_key TEXT NOT NULL UNIQUE,
                is_accepting_tros INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (founder_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_guilds_slug ON guilds(slug)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_members (
                guild_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, agent_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_members_guild ON guild_members(guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_guild_members_agent ON guild_members(agent_id)")
        # Debate challenges
        await db.execute("""
            CREATE TABLE IF NOT EXISTS debate_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                challenger_id INTEGER NOT NULL,
                challenger_name TEXT NOT NULL,
                target_name TEXT NOT NULL,
                topic TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                accepted_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (challenger_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_debate_challenges_target ON debate_challenges(target_name, status)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                proposed_by TEXT NOT NULL,
                approvals TEXT DEFAULT '[]',
                required_approvals INTEGER DEFAULT 2,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT
            )
        """)
        for stmt in [
            """CREATE TABLE IF NOT EXISTS job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                artifact_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                content TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (job_id) REFERENCES creation_jobs(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_job_artifacts_job ON job_artifacts(job_id)",
            """CREATE TABLE IF NOT EXISTS task_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poster_id INTEGER NOT NULL,
                poster_name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                required_capability TEXT DEFAULT '',
                reward_usdc REAL DEFAULT 0.0,
                status TEXT DEFAULT 'open',
                awarded_to TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT DEFAULT '',
                FOREIGN KEY (poster_id) REFERENCES agents(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_listings_status ON task_listings(status)",
            """CREATE TABLE IF NOT EXISTS task_bids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                bidder_id INTEGER NOT NULL,
                bidder_name TEXT NOT NULL,
                approach TEXT DEFAULT '',
                estimated_hours REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES task_listings(id),
                FOREIGN KEY (bidder_id) REFERENCES agents(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_bids_task ON task_bids(task_id)",
            """CREATE TABLE IF NOT EXISTS task_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                result_broadcast_id INTEGER DEFAULT 0,
                result_description TEXT DEFAULT '',
                status TEXT DEFAULT 'pending_review',
                submitted_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES task_listings(id)
            )""",
            """CREATE TABLE IF NOT EXISTS task_dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                prompt TEXT DEFAULT '',
                error_text TEXT DEFAULT '',
                error_context TEXT DEFAULT '',
                failure_count INTEGER DEFAULT 3,
                last_failed_at TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'dead',
                recovery_task_id INTEGER DEFAULT NULL,
                FOREIGN KEY (job_id) REFERENCES creation_jobs(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_dead_letter_agent ON task_dead_letter(agent_id)",
            """CREATE TABLE IF NOT EXISTS broadcast_locks (
                broadcast_id INTEGER PRIMARY KEY,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                locked_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )""",
            """CREATE TABLE IF NOT EXISTS workspace_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                label TEXT DEFAULT '',
                snapshot_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_agent ON workspace_snapshots(agent_id)",
            """CREATE TABLE IF NOT EXISTS agent_vibes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                vibe TEXT NOT NULL,
                status_code TEXT DEFAULT 'ok',
                published_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_vibes_agent ON agent_vibes(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_vibes_time ON agent_vibes(published_at)",
        ]:
            try:
                await db.execute(stmt)
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE task_listings ADD COLUMN depends_on_task_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS skill_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    task_id INTEGER NOT NULL,
                    capability TEXT NOT NULL,
                    proof_artifact TEXT NOT NULL DEFAULT '',
                    proof_type TEXT DEFAULT 'artifact',
                    status TEXT DEFAULT 'pending',
                    verified_by TEXT DEFAULT '',
                    verified_at TEXT DEFAULT '',
                    score REAL DEFAULT NULL,
                    submitted_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id),
                    FOREIGN KEY (task_id) REFERENCES task_listings(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_skill_ver_agent ON skill_verifications(agent_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_skill_ver_task ON skill_verifications(task_id)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS swarm_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT DEFAULT '',
                    is_default INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_sidecars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    module_name TEXT NOT NULL,
                    module_type TEXT DEFAULT 'logic',
                    payload TEXT NOT NULL DEFAULT '',
                    version TEXT DEFAULT '1.0',
                    is_distributed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sidecars_agent ON agent_sidecars(agent_id)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    artifacts_json TEXT DEFAULT '[]',
                    error_text TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    committed_at TEXT DEFAULT '',
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tx_agent ON broadcast_transactions(agent_id)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS capability_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    capability_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    changelog TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_capver_agent ON capability_versions(agent_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_capver_cap ON capability_versions(capability_name)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS platform_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT DEFAULT '',
                    created_by TEXT DEFAULT '',
                    tables_list TEXT DEFAULT '[]',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
        except Exception:
            pass
        # Agent personas (capability aliases)
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_personas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    capabilities TEXT DEFAULT '[]',
                    description TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(agent_id, alias),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_personas_agent ON agent_personas(agent_id)")
        except Exception:
            pass
        # task_bids feedback columns
        try:
            await db.execute("ALTER TABLE task_bids ADD COLUMN feedback TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE task_bids ADD COLUMN feedback_at TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gossip_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    event_type TEXT DEFAULT 'custom',
                    payload_json TEXT DEFAULT '{}',
                    published_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gossip_channel ON gossip_events(channel, published_at)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sentinel_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT 'broadcasts',
                    condition_json TEXT NOT NULL DEFAULT '{}',
                    action TEXT NOT NULL DEFAULT 'archive',
                    enabled INTEGER DEFAULT 1,
                    created_by TEXT DEFAULT '',
                    last_run_at TEXT DEFAULT '',
                    matches_last_run INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    template_json TEXT NOT NULL DEFAULT '[]',
                    content_type TEXT DEFAULT 'video',
                    fork_count INTEGER DEFAULT 0,
                    forked_from INTEGER DEFAULT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_templates_agent ON broadcast_templates(agent_id)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_handshakes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiator_id INTEGER NOT NULL,
                    initiator_name TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    terms_json TEXT NOT NULL DEFAULT '{}',
                    message TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    result_task_id INTEGER DEFAULT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    expires_at TEXT DEFAULT (datetime('now', '+24 hours')),
                    FOREIGN KEY (initiator_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_handshakes_recipient ON agent_handshakes(recipient_name)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_handshakes_initiator ON agent_handshakes(initiator_id)")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_error_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    error_type TEXT NOT NULL DEFAULT 'pipeline',
                    error_code TEXT DEFAULT '',
                    message TEXT NOT NULL,
                    stack_trace TEXT DEFAULT '',
                    context_json TEXT DEFAULT '{}',
                    job_id INTEGER DEFAULT NULL,
                    resolved INTEGER DEFAULT 0,
                    reported_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_errors_agent ON agent_error_reports(agent_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_errors_type ON agent_error_reports(error_type, reported_at)")
        except Exception:
            pass
        # P0: Hash-chained audit receipt log
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS receipts (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp      REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
                    agent_id       TEXT    NOT NULL,
                    action         TEXT    NOT NULL,
                    payload_hash   TEXT    NOT NULL,
                    previous_hash  TEXT    NOT NULL,
                    receipt_hash   TEXT    NOT NULL UNIQUE,
                    tier           INTEGER NOT NULL DEFAULT 0,
                    severity       TEXT    NOT NULL DEFAULT 'Advisory'
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_receipts_agent ON receipts(agent_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_receipts_action ON receipts(action)")
        except Exception:
            pass

        # Feature: TRO (Task Request Objects) — intent-based A2A routing
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tro_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    service_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    parameters TEXT DEFAULT '{}',
                    budget_usdc REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'open',
                    matched_agent TEXT DEFAULT '',
                    result_broadcast_id INTEGER,
                    expires_at TEXT DEFAULT (datetime('now', '+1 hour')),
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tro_status ON tro_requests(status, expires_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tro_agent ON tro_requests(agent_id)")
        except Exception:
            pass
        # TRO migrations
        for col, ddl in [
            ("poster_id",      "INTEGER DEFAULT NULL"),
            ("poster_name",    "TEXT DEFAULT ''"),
            ("reward_tokens",  "REAL DEFAULT 0.0"),
            ("guild_slug",     "TEXT DEFAULT ''"),
            ("updated_at",     "TEXT DEFAULT (datetime('now'))"),
        ]:
            try:
                await db.execute(f"ALTER TABLE tro_requests ADD COLUMN {col} {ddl}")
            except Exception:
                pass

        # Feature: Platform event subscriptions (environment awareness)
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS platform_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    condition_json TEXT DEFAULT '{}',
                    delivery TEXT DEFAULT 'sse',
                    webhook_url TEXT DEFAULT '',
                    last_fired_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_psub_agent ON platform_subscriptions(agent_id)")
        except Exception:
            pass

        # Feature: Proof-of-Skill challenges
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS skill_challenges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    capability TEXT NOT NULL,
                    challenge_type TEXT DEFAULT 'summary',
                    challenge_prompt TEXT NOT NULL,
                    reference_content TEXT DEFAULT '',
                    agent_response TEXT DEFAULT '',
                    auto_score REAL DEFAULT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now')),
                    submitted_at TEXT DEFAULT '',
                    scored_at TEXT DEFAULT '',
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_challenges_agent ON skill_challenges(agent_id)")
        except Exception:
            pass

        # Feature: Multi-agent rooms (ephemeral workspaces)
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    host_id INTEGER NOT NULL,
                    host_name TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    result_broadcast_id INTEGER,
                    max_members INTEGER DEFAULT 10,
                    created_at TEXT DEFAULT (datetime('now')),
                    expires_at TEXT DEFAULT (datetime('now', '+24 hours')),
                    FOREIGN KEY (host_id) REFERENCES agents(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS room_members (
                    room_id TEXT NOT NULL,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    joined_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (room_id, agent_id)
                )
            """)
        except Exception:
            pass

        # TRO response ledger (all bids, first wins)
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tro_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tro_id INTEGER NOT NULL,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    approach TEXT DEFAULT '',
                    won INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(tro_id, agent_id),
                    FOREIGN KEY (tro_id) REFERENCES tro_requests(id),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tro_resp ON tro_responses(tro_id)")
        except Exception:
            pass

        # Ghost Mode: agent thought traces
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    trace_type TEXT DEFAULT 'thought',
                    message TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_traces_agent ON agent_traces(agent_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_traces_time ON agent_traces(created_at)")
        except Exception:
            pass

        await db.commit()

    # ── Trading tables ────────────────────────────────────────
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                label TEXT NOT NULL,
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                encrypted_private_key TEXT DEFAULT '',
                exchange TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                last_synced_at TEXT,
                UNIQUE(agent_id, label)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_id INTEGER NOT NULL REFERENCES trading_wallets(id),
                token TEXT NOT NULL,
                token_address TEXT DEFAULT '',
                balance REAL NOT NULL DEFAULT 0,
                value_usd REAL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(wallet_id, token)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                wallet_id INTEGER REFERENCES trading_wallets(id),
                order_type TEXT NOT NULL DEFAULT 'market',
                side TEXT NOT NULL,
                symbol TEXT NOT NULL,
                chain TEXT NOT NULL,
                quantity REAL,
                price REAL,
                filled_quantity REAL DEFAULT 0,
                avg_fill_price REAL,
                status TEXT DEFAULT 'pending',
                trigger_reason TEXT DEFAULT '',
                signal_id INTEGER,
                strategy_id INTEGER,
                tx_hash TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                executed_at TEXT,
                settled_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_agent ON trading_orders(agent_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_strategy ON trading_orders(strategy_id)")
        # notes existed on production (added out-of-band at some point) but was
        # never captured here — a fresh install's trading_orders table was
        # missing it entirely, so POST /api/trading/orders (which always
        # inserts data.notes) crashed with "no column named notes" on any new
        # database. Caught writing a test against a fresh temp DB.
        try:
            await db.execute("ALTER TABLE trading_orders ADD COLUMN notes TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE trading_orders ADD COLUMN slippage_bps INTEGER")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                strategy_type TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                target_chain TEXT DEFAULT '',
                target_symbols TEXT DEFAULT '',
                max_position_size_usd REAL DEFAULT 0,
                max_concurrent_trades INTEGER DEFAULT 1,
                risk_per_trade_pct REAL DEFAULT 2.0,
                stop_loss_pct REAL,
                take_profit_pct REAL,
                enabled BOOLEAN DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_strategy_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL REFERENCES trading_strategies(id),
                started_at TEXT DEFAULT (datetime('now')),
                ended_at TEXT,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                pnl_usd REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                status TEXT DEFAULT 'running',
                error TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                snapshot_date DATE NOT NULL,
                portfolio_value_usd REAL NOT NULL,
                daily_pnl_usd REAL NOT NULL,
                daily_pnl_pct REAL NOT NULL,
                total_deposits_usd REAL DEFAULT 0,
                total_withdrawals_usd REAL DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(agent_id, snapshot_date)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trading_trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES trading_orders(id),
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                entry_reasoning TEXT DEFAULT '',
                exit_reasoning TEXT DEFAULT '',
                conviction_score REAL DEFAULT 0,
                lessons_learned TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                debate_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS code_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                engine TEXT NOT NULL DEFAULT 'regex',
                runner_run_id TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                findings_json TEXT NOT NULL DEFAULT '[]',
                started_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poster_id INTEGER NOT NULL REFERENCES agents(id),
                job_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                guild_slug TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS job_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                required_capability TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                claimed_by_id INTEGER REFERENCES agents(id),
                claimed_by_name TEXT DEFAULT '',
                claim_expires_at TEXT,
                result_broadcast_id INTEGER,
                result_description TEXT DEFAULT '',
                fail_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_job_tasks_job ON job_tasks(job_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_job_tasks_status ON job_tasks(status)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS security_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER REFERENCES agents(id),
                artifact_type TEXT NOT NULL,
                artifact_ref TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                normalized INTEGER NOT NULL DEFAULT 0,
                findings_json TEXT NOT NULL DEFAULT '[]',
                started_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_security_scans_agent ON security_scans(agent_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                label TEXT DEFAULT '',
                address_type TEXT NOT NULL DEFAULT 'wallet',
                notes TEXT DEFAULT '',
                added_by_agent_id INTEGER REFERENCES agents(id),
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(chain, address)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tracked_wallets_agent ON tracked_wallets(added_by_agent_id)")
        try:
            await db.execute("ALTER TABLE tracked_wallets ADD COLUMN address_type TEXT NOT NULL DEFAULT 'wallet'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE tracked_wallets ADD COLUMN notes TEXT DEFAULT ''")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                address_a TEXT NOT NULL,
                address_b TEXT NOT NULL,
                role TEXT NOT NULL,
                tx_count INTEGER NOT NULL DEFAULT 0,
                total_value REAL NOT NULL DEFAULT 0,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                UNIQUE(chain, address_a, address_b, role)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_wallet_edges_a ON wallet_edges(chain, address_a)")
        await db.commit()

    # ── Pump.fun pre-migration tier scanner ─────────────────────
    # Live-tracked bonding-curve tokens (pumpfun_tier_scanner.py), not the
    # post-migration GeckoTerminal-trending tokens degen_alpha_fusion.py/
    # ogun_multiscan.py track — those can only ever see tokens that already
    # have a DEX pool, i.e. already migrated, despite both files' own
    # comments claiming "pre-migration." This table is upstream of that gap:
    # real-time via PumpPortal's WebSocket, updated on every trade, tiered
    # by live USD market cap (5-10k / 10-20k / .../ 50-60k) with eviction
    # rules tuned to how fast pump.fun actually moves (60s of no trades once
    # a token clears 10k mcap means it's dead; a brand-new sub-10k launch
    # gets a longer 5-10min grace window since it may not have traded yet).
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pumpfun_premigration_tokens (
                mint TEXT PRIMARY KEY,
                symbol TEXT DEFAULT '',
                name TEXT DEFAULT '',
                deployer TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                last_trade_at TEXT DEFAULT (datetime('now')),
                v_tokens_in_curve REAL DEFAULT 0,
                v_sol_in_curve REAL DEFAULT 0,
                market_cap_sol REAL DEFAULT 0,
                market_cap_usd REAL DEFAULT 0,
                tier TEXT DEFAULT '',
                buy_count INTEGER DEFAULT 0,
                sell_count INTEGER DEFAULT 0,
                unique_buyers TEXT DEFAULT '[]',
                unique_sellers TEXT DEFAULT '[]',
                volume_sol_total REAL DEFAULT 0,
                score REAL DEFAULT 0,
                manipulation_flags TEXT DEFAULT '[]',
                migrated INTEGER DEFAULT 0,
                evicted INTEGER DEFAULT 0
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pumpfun_tier ON pumpfun_premigration_tokens(tier, evicted, migrated)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pumpfun_last_trade ON pumpfun_premigration_tokens(last_trade_at)")
        await db.commit()

    # ── pump.fun scalp exit-strategy positions ───────────────
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pumpfun_scalp_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                symbol TEXT DEFAULT '',
                wallet_id INTEGER NOT NULL,
                status TEXT DEFAULT 'open',
                entry_mcap_usd REAL,
                entry_sol_spent REAL,
                entry_token_base_units INTEGER,
                decimals INTEGER DEFAULT 6,
                tranche1_done INTEGER DEFAULT 0,
                tranche2_done INTEGER DEFAULT 0,
                tranche3_done INTEGER DEFAULT 0,
                stopped_out INTEGER DEFAULT 0,
                buy_order_id INTEGER,
                buy_tx_hash TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                opened_at TEXT DEFAULT (datetime('now')),
                closed_at TEXT,
                last_checked_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_scalp_status ON pumpfun_scalp_positions(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_scalp_mint ON pumpfun_scalp_positions(mint)")
        await db.commit()

    # ── Pine Script library ─────────────────────────────────
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pine_scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                name TEXT NOT NULL,
                code TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'custom',
                is_public INTEGER DEFAULT 0,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pine_agent ON pine_scripts(agent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pine_public ON pine_scripts(is_public)")
        await db.commit()

    # One-time migration: hash any plaintext API keys still stored as "vantage_..." (idempotent)
    import hashlib as _hlib_key
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, api_key FROM agents WHERE api_key LIKE 'vantage_%'"
        )).fetchall()
        for row_id, raw_key in rows:
            hashed = _hlib_key.sha256(raw_key.encode()).hexdigest()
            await db.execute("UPDATE agents SET api_key=? WHERE id=?", (hashed, row_id))
        if rows:
            await db.commit()
