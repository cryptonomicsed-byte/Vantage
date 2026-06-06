from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    ALLOWED_ORIGINS: List[str] = ["*"]
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

    ADMIN_KEY: str = ""  # Set VANTAGE_ADMIN_KEY to enable the admin/sentinel API

    class Config:
        env_file = ".env"
        env_prefix = "VANTAGE_"


settings = Settings()
