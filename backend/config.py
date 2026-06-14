import hashlib as _hashlib
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
    VERSION: str = "0.2.0"
    DEBUG: bool = False

    DATA_DIR: Path = Path("data")
    MEDIA_DIR: Path = Path("media/agents")
    WEBUI_DIR: Path = Path("frontend/dist")

    HOST: str = "0.0.0.0"
    PORT: int = 8001
    PUBLIC_URL: str = "http://localhost:8001"

    # Optional: POST publish events to any external webhook URL.
    # Leave empty to disable. No external service required.
    OUTBOUND_WEBHOOK_URL: str = ""

    ALLOWED_ORIGINS: List[str] = ["http://localhost:8001"]
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
    FEDERATION_ENABLED: bool = False

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


settings = Settings()
