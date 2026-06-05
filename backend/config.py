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

    class Config:
        env_file = ".env"
        env_prefix = "VANTAGE_"


settings = Settings()
