from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.supermemory_client import SupermemoryClient


@pytest.mark.asyncio
async def test_add_document_noop_when_unconfigured():
    client = SupermemoryClient(base_url="")
    result = await client.add_document(content="hello")
    assert result == {}


@pytest.mark.asyncio
async def test_add_document_posts_expected_payload():
    client = SupermemoryClient(base_url="http://localhost:3002", api_key="sm_test")

    captured = {}

    async def fake_post(self, url, json=None, headers=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"id": "doc_1", "status": "queued"})
        return resp

    with patch("httpx.AsyncClient.post", new=fake_post):
        result = await client.add_document(
            content="scan summary", container_tag="vantage:o/n", metadata={"k": "v"}, custom_id="cid",
        )

    assert result == {"id": "doc_1", "status": "queued"}
    assert captured["url"] == "http://localhost:3002/v3/documents"
    assert captured["json"]["content"] == "scan summary"
    assert captured["json"]["containerTag"] == "vantage:o/n"
    assert captured["json"]["metadata"] == {"k": "v"}
    assert captured["json"]["customId"] == "cid"
    assert captured["headers"]["Authorization"] == "Bearer sm_test"


@pytest.mark.asyncio
async def test_add_document_swallows_errors():
    client = SupermemoryClient(base_url="http://localhost:3002")

    async def failing_post(self, url, **kwargs):
        raise ConnectionError("refused")

    with patch("httpx.AsyncClient.post", new=failing_post):
        result = await client.add_document(content="x")
    assert result == {}
