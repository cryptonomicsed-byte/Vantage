from unittest.mock import MagicMock, patch

import pytest

from backend.parrot_client import ParrotClient


@pytest.mark.asyncio
async def test_scan_noop_when_unconfigured():
    client = ParrotClient(base_url="")
    result = await client.scan(b"hello", "file.jpg", "image")
    assert result == {"configured": False, "clean": True, "findings": [], "risk_score": 0.0}


@pytest.mark.asyncio
async def test_scan_posts_expected_payload_and_passes_through_clean_verdict():
    client = ParrotClient(base_url="http://localhost:9878")
    captured = {}

    async def fake_post(self, url, files=None, data=None, **kwargs):
        captured["url"] = url
        captured["files"] = files
        captured["data"] = data
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"clean": True, "findings": [], "risk_score": 0.0})
        return resp

    with patch("httpx.AsyncClient.post", new=fake_post):
        result = await client.scan(b"bytes", "avatar.png", "image")

    assert result == {"configured": True, "clean": True, "findings": [], "risk_score": 0.0}
    assert captured["url"] == "http://localhost:9878/scan"
    assert captured["files"]["file"] == ("avatar.png", b"bytes")
    assert captured["data"]["kind"] == "image"


@pytest.mark.asyncio
async def test_scan_passes_through_dirty_verdict():
    client = ParrotClient(base_url="http://localhost:9878")

    async def fake_post(self, url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "clean": False, "findings": [{"tool": "clamav", "detail": "EICAR"}], "risk_score": 1.0,
        })
        return resp

    with patch("httpx.AsyncClient.post", new=fake_post):
        result = await client.scan(b"bytes", "evil.mp4", "video")

    assert result["clean"] is False
    assert result["findings"] == [{"tool": "clamav", "detail": "EICAR"}]


@pytest.mark.asyncio
async def test_scan_fails_closed_when_configured_but_unreachable():
    """Unlike the enrichment sidecars (supermemory, ViMax), a scanner outage
    must not be silently treated as 'clean' — this is the actual security
    control, not an advisory enrichment."""
    client = ParrotClient(base_url="http://localhost:9878")

    async def failing_post(self, url, **kwargs):
        raise ConnectionError("refused")

    with patch("httpx.AsyncClient.post", new=failing_post):
        result = await client.scan(b"bytes", "file.jpg", "image")

    assert result["configured"] is True
    assert result["clean"] is False
