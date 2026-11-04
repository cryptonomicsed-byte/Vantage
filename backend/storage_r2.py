"""Cloudflare R2 object storage (S3-compatible).

Split of responsibilities with Pinata:
  - R2 holds the *bytes* (token images, media). 10 GB free vs Pinata's 1 GB.
  - Pinata still pins the metadata JSON, because pump.fun needs an IPFS CID
    that a contract can resolve. R2 has no CID, so it cannot replace that leg.

Reading credentials: env first, then /opt/litellm/provider_keys.env so the
single key vault stays the one source of truth.
"""
from __future__ import annotations

import logging
import os
import uuid

log = logging.getLogger(__name__)

VAULT = "/opt/litellm/provider_keys.env"

_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
}


def _load_env() -> dict:
    vals = {}
    try:
        with open(VAULT) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    except Exception:
        pass
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def configured() -> bool:
    e = _load_env()
    return bool(e.get("R2_ACCESS_KEY_ID") and e.get("R2_SECRET_ACCESS_KEY") and e.get("R2_ENDPOINT"))


def public_url(key: str, bucket: str = "") -> str:
    e = _load_env()
    bucket = bucket or e.get("R2_BUCKET", "vantage-assets")
    return f"{e['R2_ENDPOINT']}/{bucket}/{key}"


def upload_bytes(data: bytes, filename: str, content_type: str, prefix: str = "tokens") -> dict:
    """Upload and return {key, url, size}. Raises RuntimeError on failure."""
    import boto3
    from botocore.config import Config

    e = _load_env()
    bucket = e.get("R2_BUCKET", "vantage-assets")
    client = boto3.client(
        "s3",
        endpoint_url=e["R2_ENDPOINT"],
        aws_access_key_id=e["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="auto",
    )
    ext = _EXT.get(content_type or "", "")
    if not ext and "." in (filename or ""):
        ext = "." + filename.rsplit(".", 1)[-1].lower()[:5]
    key = f"{prefix}/{uuid.uuid4().hex}{ext}"
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type or "application/octet-stream")
    return {"key": key, "url": public_url(key, bucket), "size": len(data)}
