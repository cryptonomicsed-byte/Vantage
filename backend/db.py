"""Database path, media root, and schema initialisation."""
import logging
from pathlib import Path

import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

DB_PATH: Path = settings.DATA_DIR / "vantage.db"
MEDIA_ROOT: Path = settings.MEDIA_DIR


async def init_agents_db() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL mode for concurrent reads
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=-64000")

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
        # Indexes for hot query paths
        await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_api_key ON agents(api_key)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_id ON broadcasts(agent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_content_type ON broadcasts(content_type) WHERE content_type IS NOT NULL")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_created_at ON broadcasts(created_at)")

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
        ]:
            try:
                await db.execute(f"ALTER TABLE broadcasts ADD COLUMN {col} {ddl}")
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
            ("reputation", "REAL DEFAULT 1.0"),
            ("flagged",    "INTEGER DEFAULT 0"),
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

        await db.commit()
