"""Agent public profile endpoint (GET /api/agents/profile/{name}) — regression
coverage for a real crash: the response never included a `series` key at all,
so the frontend's `profile.series.length` (AgentProfile.tsx's Series tab)
threw `Cannot read properties of undefined (reading 'length')` on every visit.
"""
import pytest


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_agent_profile_includes_empty_series_list(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.get(f"/api/agents/profile/{agent['name']}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["series"] == []


@pytest.mark.asyncio
async def test_agent_profile_includes_created_series(client, fresh_agent):
    agent = await fresh_agent()
    created = await client.post(
        "/api/agents/me/series", headers=_h(agent),
        json={"title": "Season One", "description": "The first arc"},
    )
    assert created.status_code == 200, created.text
    series_id = created.json()["id"]

    r = await client.get(f"/api/agents/profile/{agent['name']}")
    assert r.status_code == 200, r.text
    series_list = r.json()["series"]
    assert len(series_list) == 1
    s = series_list[0]
    assert s["id"] == series_id
    assert s["title"] == "Season One"
    assert s["description"] == "The first arc"
    assert s["episode_count"] == 0
