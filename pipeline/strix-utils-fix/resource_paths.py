"""Resolve paths to strix package resources."""
from pathlib import Path

_STRIX_ROOT = Path(__file__).resolve().parent.parent

def get_strix_resource_path(*parts: str) -> Path:
    """Return absolute path to a resource inside the strix package."""
    return _STRIX_ROOT.joinpath(*parts)
