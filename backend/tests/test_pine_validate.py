import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

import backend.pine_validate as pv


def _clear_cache():
    pv._CACHE.clear()


@pytest.mark.asyncio
async def test_empty_script_returns_invalid():
    _clear_cache()
    result = await pv.validate_pine("")
    assert result["valid"] is False
    assert result["errors"][0]["message"] == "empty script"


@pytest.mark.asyncio
async def test_whitespace_only_script_returns_invalid():
    _clear_cache()
    result = await pv.validate_pine("   \n  ")
    assert result["valid"] is False
    assert result["errors"][0]["message"] == "empty script"


@pytest.mark.asyncio
async def test_network_error_returns_fail_open():
    _clear_cache()
    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        mock_client_cls.return_value = instance

        result = await pv.validate_pine('//@version=5\nindicator("x")')
        assert result["valid"] is True
        assert result.get("network_error") is True


@pytest.mark.asyncio
async def test_cached_result_on_second_call():
    _clear_cache()
    script = '//@version=5\nindicator("cache test")'
    fake_response = MagicMock()
    fake_response.json.return_value = {"result": {"ok": True}}

    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return fake_response

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post = fake_post
        mock_client_cls.return_value = instance

        first = await pv.validate_pine(script)
        second = await pv.validate_pine(script)

    assert first == {"valid": True}
    assert second == {"valid": True}
    assert call_count == 1


@pytest.mark.asyncio
async def test_compile_errors_are_1_indexed():
    _clear_cache()
    script = '//@version=5\nbad syntax here'
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "result": {
            "ok": False,
            "errors": [
                {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 3}, "message": "syntax error"},
            ],
        }
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=fake_response)
        mock_client_cls.return_value = instance

        result = await pv.validate_pine(script)

    assert result["valid"] is False
    err = result["errors"][0]
    assert err["line"] == 2
    assert err["column"] == 1
    assert err["message"] == "syntax error"


def test_annotate_inserts_comment_on_correct_line():
    script = "//@version=5\nindicator('x')\nbad line here"
    errors = [{"line": 3, "column": 1, "message": "unexpected token"}]
    annotated = pv.annotate(script, errors)
    lines = annotated.splitlines()
    comment_idx = lines.index("// \u26a0 unexpected token")
    assert lines[comment_idx + 1] == "bad line here"


def test_annotate_multiple_errors_on_same_line():
    script = "line1\nline2\nline3"
    errors = [
        {"line": 2, "column": 1, "message": "err A"},
        {"line": 2, "column": 5, "message": "err B"},
    ]
    annotated = pv.annotate(script, errors)
    lines = annotated.splitlines()
    comments = [l for l in lines if l.startswith("// \u26a0")]
    assert len(comments) == 2
    line2_idx = lines.index("line2")
    assert lines[line2_idx - 1].startswith("// \u26a0")
    assert lines[line2_idx - 2].startswith("// \u26a0")


def test_annotate_empty_errors_returns_original():
    script = "//@version=5\nindicator('x')"
    assert pv.annotate(script, []) == script
