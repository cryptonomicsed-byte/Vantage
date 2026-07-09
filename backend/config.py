import hashlib as _hashlib
import logging
from pathlib import Path
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Check backend/.env first, then project root .env, then cwd .env
_candidates = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
    Path(".env"),
]
_ENV_FILE = next((str(p) for p in _candidates if p.exists()), str(_candidates[0]))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_prefix="VANTAGE_",
        env_file_encoding="utf-8",
    )

    APP_NAME: str = "Vantage"
    VERSION: str = "0.2.1"
    DEBUG: bool = False

    DATA_DIR: Path = Path("data")
    MEDIA_DIR: Path = Path("media/agents")
    WEBUI_DIR: Path = Path("/opt/ares/Vantage/frontend/dist")

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    PUBLIC_URL: str = "http://localhost:8000"

    # Optional: POST publish events to any external webhook URL.
    # Leave empty to disable. No external service required.
    OUTBOUND_WEBHOOK_URL: str = ""

    ALLOWED_ORIGINS: List[str] = ["http://localhost:8000"]
    MAX_UPLOAD_MB: int = 500

    # Walrus decentralized storage (optional, set WALRUS_ENABLED=true to activate)
    WALRUS_ENABLED: bool = False
    WALRUS_PUBLISHER_URL: str = ""
    WALRUS_AGGREGATOR_URL: str = ""

    # Sui blockchain integration (optional)
    SUI_ENABLED: bool = False
    SUI_CONTRACT_ADDRESS: str = ""
    SUI_NODE_URL: str = "https://fullnode.mainnet.sui.io"

    # Seal encryption (optional)
    SEAL_ENABLED: bool = False

    # Cross-instance federation (optional)
    FEDERATION_ENABLED: bool = True

    # Creation pipeline: Vantage only tracks job state — agents drive generation
    # using their own LLM, TTS, and image/video tools, then publish via standard endpoints.

    ADMIN_KEY: str = ""  # set via VANTAGE_ADMIN_KEY env var — no hardcoded default

    @field_validator("ADMIN_KEY")
    @classmethod
    def validate_admin_key(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("VANTAGE_ADMIN_KEY must be at least 32 characters")
        return v

    @property
    def ADMIN_KEY_HASH(self) -> Optional[str]:
        """SHA-256 of admin key, computed once. None if admin key is not set."""
        if not self.ADMIN_KEY:
            return None
        return _hashlib.sha256(self.ADMIN_KEY.encode()).hexdigest()

    # Federation signing key — HMAC-SHA256 key for peer manifest verification (optional)
    FEDERATION_KEY: str = ""

    # Optional: OpenRouter API key — enables true vector semantic search in memory vault.
    # Falls back to wildcard FTS5 if not set. Set via VANTAGE_OPENROUTER_KEY env var.
    OPENROUTER_KEY: str = ""

    # Ọmọ Kọ́dà integration (Block Mesh)
    # STEWARD_URL: Vantage can push mesh events back to the Ọmọ Kọ́dà steward (optional).
    # MESH_KEY: shared secret for Ọmọ Kọ́dà→Vantage mesh calls (optional; falls back to X-Agent-Key).
    STEWARD_URL: str = ""
    MESH_KEY: str = ""
    LLM_KEY: str = ""  # Fernet key for agent LLM API key encryption

    # Code pipeline: Gitea hosting for agent-pushed repos (optional; push/scan
    # endpoints 503 if unset).
    GITEA_URL: str = ""
    GITEA_TOKEN: str = ""

    # Strix security scanning (github.com/usestrix/strix) runs on a small
    # standalone runner on the VPS host, not inside this container (no Docker
    # access here by design). Empty URL disables the strix scan engine — the
    # existing fast regex scan keeps working either way.
    STRIX_RUNNER_URL: str = ""

    # supermemory (self-hosted, optional) — memory/context ingestion for the
    # code pipeline. Empty URL makes the memory-ingest endpoint a no-op.
    SUPERMEMORY_URL: str = ""
    SUPERMEMORY_API_KEY: str = ""

    # Parrot security scan gate (ClamAV/YARA/binwalk container) for uploaded
    # artifacts. Unlike the enrichment sidecars above, this one fails CLOSED:
    # if set but unreachable, uploads are rejected rather than silently passed
    # through. Empty URL disables the gate entirely (pre-existing behavior).
    PARROT_SECURITY_URL: str = ""

    # Ọmọ Kọ́dà sovereign-agent kernel (Rust). When set, Vantage can birth
    # Omo-Koda agents (POST /api/agents/birth-omokoda proxies to its /v1/birth)
    # and push published broadcasts into its knowledge vault. Empty = disabled
    # (the broadcast-push in agents.py becomes a no-op). Referenced as
    # settings.OMOKODA_URL — must exist here so that access never AttributeErrors.
    OMOKODA_URL: str = ""


settings = Settings()

# Runtime guard: warn and override any Phase C feature flags that are not yet implemented.
# Setting these to True would cause crashes; this guard prevents silent misconfig.
_logger = logging.getLogger(__name__)
_UNIMPLEMENTED_FLAGS = {
    "WALRUS_ENABLED": settings.WALRUS_ENABLED,
    "SUI_ENABLED": settings.SUI_ENABLED,
    "SEAL_ENABLED": settings.SEAL_ENABLED,
    "FEDERATION_ENABLED": settings.FEDERATION_ENABLED,
}
for _flag, _val in _UNIMPLEMENTED_FLAGS.items():
    if _val:
        _logger.warning(
            "%s=True but this feature is not yet implemented — setting to False",
            _flag,
        )
        object.__setattr__(settings, _flag, False)
