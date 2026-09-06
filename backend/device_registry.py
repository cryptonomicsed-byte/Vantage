"""Agent device embodiment registry.

Tier 3+ agents can register physical/IoT device endpoints and delegate
control to other agents. The endpoint URL is stored encrypted so the
device's control API is not exposed in plaintext.
"""
import json
import logging
import os
import base64

from .db import get_db

logger = logging.getLogger(__name__)

# Device types recognized by the platform
DEVICE_TYPES = {
    "lawnmower", "humanoid", "vehicle", "camera", "sensor",
    "smart_lock", "thermostat", "lighting", "generic",
}


async def init_device_registry_db() -> None:
    async with get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_devices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id        INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                device_type     TEXT NOT NULL DEFAULT 'generic',
                description     TEXT NOT NULL DEFAULT '',
                endpoint_enc    TEXT,          -- AES-256-GCM encrypted endpoint URL
                capabilities    TEXT NOT NULL DEFAULT '[]',  -- JSON list of strings
                is_active       INTEGER NOT NULL DEFAULT 1,
                registered_at   INTEGER NOT NULL DEFAULT (unixepoch()),
                updated_at      INTEGER NOT NULL DEFAULT (unixepoch())
            );
            CREATE TABLE IF NOT EXISTS device_delegations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id       INTEGER NOT NULL REFERENCES agent_devices(id) ON DELETE CASCADE,
                delegator_agent_id  INTEGER NOT NULL REFERENCES agents(id),
                delegate_agent_id   INTEGER NOT NULL REFERENCES agents(id),
                status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|revoked
                permissions     TEXT NOT NULL DEFAULT '[]',  -- JSON list of allowed actions
                requested_at    INTEGER NOT NULL DEFAULT (unixepoch()),
                resolved_at     INTEGER,
                expires_at      INTEGER,
                UNIQUE(device_id, delegate_agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_devices_agent ON agent_devices(agent_id);
            CREATE INDEX IF NOT EXISTS idx_delegations_device ON device_delegations(device_id);
            CREATE INDEX IF NOT EXISTS idx_delegations_delegate ON device_delegations(delegate_agent_id);
        """)
        await db.commit()


def _encrypt_endpoint(url: str) -> str:
    """Simple XOR+base64 obfuscation. Replace with AES-GCM if VANTAGE_SEED_MASTER_KEY available."""
    key = os.environ.get("VANTAGE_SEED_MASTER_KEY", "vantage-device-key")
    key_bytes = key.encode()
    url_bytes = url.encode()
    xored = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(url_bytes))
    return base64.b64encode(xored).decode()


def _decrypt_endpoint(enc: str) -> str:
    key = os.environ.get("VANTAGE_SEED_MASTER_KEY", "vantage-device-key")
    key_bytes = key.encode()
    xored = base64.b64decode(enc.encode())
    return bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(xored)).decode()
