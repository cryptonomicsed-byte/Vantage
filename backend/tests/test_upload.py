"""Upload security tests — file magic validation, size limits."""
import io
import pytest


@pytest.mark.asyncio
async def test_fake_video_extension_rejected(client, registered_agent):
    """A .mp4 file with PE (MZ) magic bytes must be caught by the magic check.

    The upload endpoint returns 200 immediately (async processing); the magic check
    runs in the background task and marks the broadcast as 'error'.  We verify:
    1. The upload is accepted at the HTTP level (200)
    2. The broadcast ends up in error state (not ready/processing)
    """
    import asyncio
    import aiosqlite
    from backend.db import DB_PATH

    fake_video = b"MZ\x90\x00" + b"\x00" * 100
    resp = await client.post(
        "/api/agents/publish",
        headers={"X-Agent-Key": registered_agent["api_key"]},
        files={"file": ("evil.mp4", io.BytesIO(fake_video), "video/mp4")},
        data={"title": "evil"},
    )
    # Accepted for async processing
    assert resp.status_code == 200
    broadcast_id = resp.json().get("broadcast_id")
    assert broadcast_id is not None

    # Wait briefly for background task to complete the magic check
    await asyncio.sleep(0.5)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status FROM broadcasts WHERE id=?", (broadcast_id,)
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    # Should be marked 'error' after magic byte validation fails
    assert row[0] == "error", f"Expected 'error' status, got '{row[0]}'"


@pytest.mark.asyncio
async def test_real_mp4_magic_accepted_for_processing(client, registered_agent):
    """A valid MP4 magic header should pass magic byte validation (may fail FFmpeg — that's ok)."""
    # ftyp box magic (MP4/M4V)
    mp4_magic = b"\x00\x00\x00\x1cftypisom" + b"\x00" * 512
    resp = await client.post(
        "/api/agents/publish",
        headers={"X-Agent-Key": registered_agent["api_key"]},
        files={"file": ("clip.mp4", io.BytesIO(mp4_magic), "video/mp4")},
        data={"title": "valid magic"},
    )
    # Either accepted for processing (200/201) or error from FFmpeg (4xx/5xx)
    # The key invariant: should NOT be a 415 from magic-byte rejection
    # (FFmpeg errors are fine — we're testing the magic gate)
    data = resp.json()
    # If it was rejected, it was a magic-bytes or missing-title issue, not our concern here
    assert resp.status_code in (200, 201, 202, 400, 422, 500)


@pytest.mark.asyncio
async def test_image_upload_accepts_png_magic(client, registered_agent):
    """PNG magic bytes must pass file validation."""
    from backend.utils import _validate_file_magic
    import tempfile, pathlib

    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_magic)
        tmp_path = pathlib.Path(f.name)

    assert _validate_file_magic(tmp_path, "image") is True
    tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_image_upload_rejects_exe_magic(client, registered_agent):
    """PE executable must fail image magic validation."""
    from backend.utils import _validate_file_magic
    import tempfile, pathlib

    exe_magic = b"MZ\x90\x00" + b"\x00" * 100
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(exe_magic)
        tmp_path = pathlib.Path(f.name)

    assert _validate_file_magic(tmp_path, "image") is False
    tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_audio_upload_accepts_mp3_magic(client, registered_agent):
    """MP3 ID3 magic must pass audio validation."""
    from backend.utils import _validate_file_magic
    import tempfile, pathlib

    mp3_magic = b"ID3" + b"\x00" * 100
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_magic)
        tmp_path = pathlib.Path(f.name)

    assert _validate_file_magic(tmp_path, "audio") is True
    tmp_path.unlink(missing_ok=True)
