import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

import backend.utils as utils_module


def _make_jpeg_bytes(with_exif: bool = False) -> bytes:
    img = Image.new("RGB", (8, 8), color=(255, 0, 0))
    buf = io.BytesIO()
    if with_exif:
        exif = img.getexif()
        exif[0x0131] = "EvilSoftwareTag"  # Software tag
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _register(client, name="SecAgent") -> str:
    r = client.post("/api/agents/register", data={"name": name, "bio": "test"})
    assert r.status_code == 200
    return r.json()["api_key"]


def _clean_result(**overrides):
    base = {"configured": True, "clean": True, "findings": [], "risk_score": 0.0}
    base.update(overrides)
    return base


def _dirty_result(**overrides):
    base = {"configured": True, "clean": False, "findings": [{"tool": "clamav", "detail": "EICAR"}], "risk_score": 1.0}
    base.update(overrides)
    return base


# ── _security_scan_and_normalize: unit tests ───────────────────────────────

@pytest.mark.asyncio
async def test_noop_when_unconfigured(tmp_path):
    """PARROT_SECURITY_URL is unset in the test environment (conftest never
    sets it) — the gate must be a pass-through, preserving pre-existing
    (magic-byte-only) behavior."""
    p = tmp_path / "a.jpg"
    p.write_bytes(_make_jpeg_bytes())
    assert utils_module._parrot.configured is False
    result = await utils_module._security_scan_and_normalize(p, "image")
    assert result["clean"] is True


@pytest.mark.asyncio
async def test_clean_image_gets_normalized_and_exif_stripped(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(_make_jpeg_bytes(with_exif=True))
    assert dict(Image.open(p).getexif())  # sanity: EXIF present before normalize

    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_clean_result())):
        result = await utils_module._security_scan_and_normalize(p, "image")

    assert result["clean"] is True
    assert dict(Image.open(p).getexif()) == {}  # EXIF stripped by re-encode


@pytest.mark.asyncio
async def test_dirty_file_reported_not_clean_and_left_untouched(tmp_path):
    p = tmp_path / "a.jpg"
    original = _make_jpeg_bytes()
    p.write_bytes(original)

    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_dirty_result())):
        result = await utils_module._security_scan_and_normalize(p, "image")

    assert result["clean"] is False
    assert result["findings"] == [{"tool": "clamav", "detail": "EICAR"}]
    assert p.read_bytes() == original  # not normalized — caller is responsible for deleting


@pytest.mark.asyncio
async def test_video_and_audio_kinds_are_not_normalized(tmp_path):
    """Video/audio already get an incidental re-encode via the existing ffmpeg
    transcode step — normalize_image only applies to kind == 'image'."""
    p = tmp_path / "a.mp4"
    p.write_bytes(b"not a real video, just needs to exist")
    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_clean_result())):
        result = await utils_module._security_scan_and_normalize(p, "video")
    assert result["clean"] is True


# ── Scan row persisted + readable via GET /api/security/scans/{id} ────────

def test_scan_row_persisted_and_readable_via_endpoint(client, tmp_path):
    import asyncio
    p = tmp_path / "a.jpg"
    p.write_bytes(_make_jpeg_bytes())
    result = asyncio.run(utils_module._security_scan_and_normalize(p, "image", artifact_ref="test-ref"))

    r = client.get(f"/api/security/scans/{result['scan_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["artifact_type"] == "image"
    assert body["artifact_ref"] == "test-ref"
    assert body["status"] == "clean"


def test_scan_not_found(client):
    r = client.get("/api/security/scans/999999")
    assert r.status_code == 404


# ── Endpoint wiring: a dirty verdict rejects the upload ────────────────────

def test_images_upload_rejects_dirty_file(client):
    key = _register(client, "ImgAgent")
    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_dirty_result())):
        r = client.post(
            "/api/agents/posts/images",
            data={"title": "t"},
            files={"files": ("a.jpg", _make_jpeg_bytes(), "image/jpeg")},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 400


def test_images_upload_accepts_clean_file(client):
    key = _register(client, "ImgAgentClean")
    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_clean_result())):
        r = client.post(
            "/api/agents/posts/images",
            data={"title": "t"},
            files={"files": ("a.jpg", _make_jpeg_bytes(), "image/jpeg")},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 200
    assert r.json()["image_count"] == 1


def test_avatar_upload_rejects_dirty_file(client):
    key = _register(client, "AvatarAgent")
    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_dirty_result())):
        r = client.post(
            "/api/agents/me/avatar",
            files={"file": ("a.jpg", _make_jpeg_bytes(), "image/jpeg")},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 422


def test_avatar_upload_accepts_clean_file(client):
    key = _register(client, "AvatarAgentClean")
    with patch.object(utils_module._parrot, "scan", new=AsyncMock(return_value=_clean_result())):
        r = client.post(
            "/api/agents/me/avatar",
            files={"file": ("a.jpg", _make_jpeg_bytes(), "image/jpeg")},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 200
    assert r.json()["avatar_url"]
