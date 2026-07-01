from cryptography.fernet import Fernet
import os
from pathlib import Path

_KEY = None

def _load_key():
    global _KEY
    # 1. Try environment variable
    _KEY = os.getenv("VANTAGE_LLM_KEY", "")
    if _KEY:
        return
    # 2. Try .env file
    for candidate in [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / ".env",
        Path("/opt/ares/Vantage/backend/.env"),
    ]:
        if candidate.exists():
            for line in open(candidate):
                if line.startswith("VANTAGE_LLM_KEY="):
                    _KEY = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    return

_fernet = None

def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    if not _KEY:
        _load_key()
    if not _KEY:
        raise ValueError("VANTAGE_LLM_KEY not set in environment or .env")
    _fernet = Fernet(_KEY.encode())
    return _fernet

def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
