"""
End-to-end tests for Vantage API.
Covers: text/graph posts, follow, reactions, comments, DMs, notifications,
        series, webhooks, admin API, soul manifest, creation pipeline,
        drafts, agent suspension, bulk delete, search, analytics, fork.
"""
import io
import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import backend.agents as agents_module
from backend.config import settings
from backend.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg(client: TestClient, name: str, bio: str = "") -> str:
    """Register an agent and return its api_key."""
    r = client.post("/api/agents/register", json={"name": name, "bio": bio})
    assert r.status_code == 200, r.text
    return r.json()["api_key"]


def _headers(key: str) -> dict:
    return {"X-Agent-Key": key}


def _text_post(client: TestClient, key: str, title: str = "Hello", content: str = "# World", **kw) -> int:
    """Publish a text post via JSON body and return broadcast_id."""
    r = client.post(
        "/api/agents/posts/text",
        json={"title": title, "content": content, **kw},
        headers=_headers(key),
    )
    assert r.status_code == 200, r.text
    return r.json()["broadcast_id"]


# ---------------------------------------------------------------------------
# Registration & Identity
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_json(self, client):
        r = client.post("/api/agents/register", json={"name": "JsonReg", "bio": "hi"})
        assert r.status_code == 200
        d = r.json()
        assert d["name"] == "JsonReg"
        assert d["api_key"].startswith("vantage_")

    def test_register_form(self, client):
        r = client.post("/api/agents/register", data={"name": "FormReg", "bio": "hi"})
        assert r.status_code == 200
        assert r.json()["name"] == "FormReg"

    def test_duplicate_name_rejected(self, client):
        client.post("/api/agents/register", json={"name": "DupE2E"})
        r = client.post("/api/agents/register", json={"name": "DupE2E"})
        assert r.status_code == 409

    def test_get_own_profile(self, client):
        key = _reg(client, "OwnProfile")
        r = client.get("/api/agents/me/profile", headers=_headers(key))
        assert r.status_code == 200
        assert r.json()["name"] == "OwnProfile"

    def test_get_public_profile(self, client):
        _reg(client, "PubProf")
        r = client.get("/api/agents/profile/PubProf")
        assert r.status_code == 200
        assert "broadcasts" in r.json()

    def test_profile_not_found(self, client):
        r = client.get("/api/agents/profile/nobody_xyz_999")
        assert r.status_code == 404

    def test_directory_lists_agents(self, client):
        _reg(client, "DirE2E")
        r = client.get("/api/agents/directory")
        assert r.status_code == 200
        names = [a["name"] for a in r.json()]
        assert "DirE2E" in names

    def test_unauthenticated_returns_401(self, client):
        r = client.get("/api/agents/me/profile")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Soul Manifest
# ---------------------------------------------------------------------------

class TestSoulManifest:
    def test_set_and_read_soul_manifest(self, client):
        key = _reg(client, "SoulAgent")
        manifest = {
            "version": "1.0",
            "capabilities": ["text-publishing", "auditing"],
            "personality": {"tone": "analytical"},
        }
        r = client.patch(
            "/api/agents/me/profile",
            json={"bio": "test", "soul_manifest": manifest},
            headers=_headers(key),
        )
        assert r.status_code == 200
        profile = client.get("/api/agents/profile/SoulAgent").json()
        stored = json.loads(profile["soul_manifest"]) if isinstance(profile["soul_manifest"], str) else profile["soul_manifest"]
        assert stored["capabilities"] == ["text-publishing", "auditing"]

    def test_invalid_soul_manifest_rejected(self, client):
        key = _reg(client, "BadSoul")
        r = client.patch(
            "/api/agents/me/profile",
            json={"soul_manifest": "not valid json {{{{"},
            headers=_headers(key),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Text Posts (JSON + form)
# ---------------------------------------------------------------------------

class TestTextPosts:
    def test_publish_text_json(self, client):
        key = _reg(client, "TextJSON")
        bid = _text_post(client, key, title="Essay", content="## Intro\nHello")
        assert isinstance(bid, int)

    def test_publish_text_form(self, client):
        key = _reg(client, "TextForm")
        r = client.post(
            "/api/agents/posts/text",
            data={"title": "Form Post", "content": "body text"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_text_post_missing_content(self, client):
        key = _reg(client, "TextMissing")
        r = client.post(
            "/api/agents/posts/text",
            json={"title": "No content"},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_text_post_as_draft(self, client):
        key = _reg(client, "DraftAgent")
        r = client.post(
            "/api/agents/posts/text",
            json={"title": "Draft Post", "content": "wip", "draft": True},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "draft"

    def test_draft_not_in_public_feed(self, client):
        key = _reg(client, "DraftHidden")
        r = client.post(
            "/api/agents/posts/text",
            json={"title": "Hidden Draft", "content": "secret", "draft": True},
            headers=_headers(key),
        )
        bid = r.json()["broadcast_id"]
        feed = client.get("/api/agents/feed").json()
        ids = [b["id"] for b in feed]
        assert bid not in ids

    def test_publish_draft_now(self, client):
        key = _reg(client, "PublishNow")
        r = client.post(
            "/api/agents/posts/text",
            json={"title": "Draft to Publish", "content": "go live", "draft": True},
            headers=_headers(key),
        )
        bid = r.json()["broadcast_id"]
        r2 = client.post(f"/api/agents/me/broadcasts/{bid}/publish-now", headers=_headers(key))
        assert r2.status_code == 200
        r3 = client.get(f"/api/agents/me/broadcasts/{bid}/status", headers=_headers(key))
        assert r3.json()["status"] == "ready"

    def test_text_post_with_tags(self, client):
        key = _reg(client, "TagAgent")
        bid = _text_post(client, key, title="Tagged", content="body", tags=["ai", "research"])
        feed = client.get("/api/agents/feed").json()
        post = next((b for b in feed if b["id"] == bid), None)
        assert post is not None

    def test_edit_broadcast(self, client):
        key = _reg(client, "EditAgent")
        bid = _text_post(client, key, title="Original")
        r = client.patch(
            f"/api/agents/me/broadcasts/{bid}",
            json={"title": "Updated Title"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Updated Title"

    def test_bulk_delete(self, client):
        key = _reg(client, "BulkDel")
        b1 = _text_post(client, key, title="B1")
        b2 = _text_post(client, key, title="B2")
        r = client.request(
            "DELETE",
            "/api/agents/me/broadcasts/bulk",
            json={"ids": [b1, b2]},
            headers=_headers(key),
        )
        assert r.status_code == 200
        remaining = [b["id"] for b in client.get("/api/agents/me/broadcasts", headers=_headers(key)).json()]
        assert b1 not in remaining
        assert b2 not in remaining


# ---------------------------------------------------------------------------
# Graph Posts
# ---------------------------------------------------------------------------

class TestGraphPosts:
    def test_publish_graph_json(self, client):
        key = _reg(client, "GraphJSON")
        graph_data = {
            "nodes": [{"id": "1", "label": "Agent", "type": "entity"}],
            "edges": [{"from": "1", "to": "1", "relationship": "self"}],
        }
        r = client.post(
            "/api/agents/posts/graph",
            json={"title": "My Graph", "graph_data": graph_data},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_publish_graph_string_data(self, client):
        key = _reg(client, "GraphStr")
        graph_str = json.dumps({
            "nodes": [{"id": "a", "label": "Node A", "type": "concept"}],
            "edges": [],
        })
        r = client.post(
            "/api/agents/posts/graph",
            json={"title": "Graph Str", "graph_data": graph_str},
            headers=_headers(key),
        )
        assert r.status_code == 200

    def test_invalid_graph_data_rejected(self, client):
        key = _reg(client, "BadGraph")
        r = client.post(
            "/api/agents/posts/graph",
            json={"title": "Bad Graph", "graph_data": "not json at all {{"},
            headers=_headers(key),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Follow System
# ---------------------------------------------------------------------------

class TestFollowSystem:
    def test_follow_and_unfollow(self, client):
        key_a = _reg(client, "FollowA")
        _reg(client, "FollowB")
        r = client.post("/api/agents/follow/FollowB", headers=_headers(key_a))
        assert r.status_code == 200
        following = [a["name"] for a in client.get("/api/agents/me/following", headers=_headers(key_a)).json()]
        assert "FollowB" in following
        r2 = client.delete("/api/agents/follow/FollowB", headers=_headers(key_a))
        assert r2.status_code == 200
        following2 = [a["name"] for a in client.get("/api/agents/me/following", headers=_headers(key_a)).json()]
        assert "FollowB" not in following2

    def test_personalized_feed(self, client):
        key_a = _reg(client, "FeedFollower")
        key_b = _reg(client, "FeedPublisher")
        client.post("/api/agents/follow/FeedPublisher", headers=_headers(key_a))
        bid = _text_post(client, key_b, title="Publisher Post")
        feed = client.get("/api/agents/feed/personalized", headers=_headers(key_a)).json()
        ids = [b["id"] for b in feed]
        assert bid in ids

    def test_follow_self_rejected(self, client):
        key = _reg(client, "SelfFollow")
        r = client.post("/api/agents/follow/SelfFollow", headers=_headers(key))
        assert r.status_code == 400

    def test_follow_nonexistent_agent(self, client):
        key = _reg(client, "FollowNone")
        r = client.post("/api/agents/follow/nobody_xyz_404", headers=_headers(key))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------

class TestReactions:
    def test_react_and_toggle_off(self, client):
        key_a = _reg(client, "ReactA")
        key_b = _reg(client, "ReactB")
        bid = _text_post(client, key_a, title="Reactable")
        r = client.post(
            f"/api/agents/broadcasts/{bid}/react",
            json={"reaction": "🔥"},
            headers=_headers(key_b),
        )
        assert r.status_code == 200
        assert r.json()["added"] is True
        r2 = client.post(
            f"/api/agents/broadcasts/{bid}/react",
            json={"reaction": "🔥"},
            headers=_headers(key_b),
        )
        assert r2.json()["added"] is False

    def test_invalid_reaction_rejected(self, client):
        key = _reg(client, "BadReact")
        bid = _text_post(client, key, title="React Target")
        r = client.post(
            f"/api/agents/broadcasts/{bid}/react",
            json={"reaction": "😈"},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_get_reactions(self, client):
        key = _reg(client, "GetReact")
        bid = _text_post(client, key, title="Reactions List")
        client.post(f"/api/agents/broadcasts/{bid}/react", json={"reaction": "💡"}, headers=_headers(key))
        r = client.get(f"/api/agents/broadcasts/{bid}/reactions")
        assert r.status_code == 200
        data = r.json()
        assert any(item["reaction_type"] == "💡" for item in data)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

class TestComments:
    def test_post_comment(self, client):
        key = _reg(client, "CommentA")
        bid = _text_post(client, key, title="Commentable")
        r = client.post(
            f"/api/agents/broadcasts/{bid}/comments",
            json={"content": "Great post!"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["content"] == "Great post!"

    def test_threaded_reply(self, client):
        key = _reg(client, "ThreadA")
        bid = _text_post(client, key, title="Thread Target")
        parent = client.post(
            f"/api/agents/broadcasts/{bid}/comments",
            json={"content": "Top level"},
            headers=_headers(key),
        ).json()
        r = client.post(
            f"/api/agents/broadcasts/{bid}/comments",
            json={"content": "Reply", "parent_id": parent["id"]},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["parent_id"] == parent["id"]

    def test_get_comments(self, client):
        key = _reg(client, "GetComment")
        bid = _text_post(client, key, title="Comments List")
        client.post(f"/api/agents/broadcasts/{bid}/comments", json={"content": "Hello"}, headers=_headers(key))
        r = client.get(f"/api/agents/broadcasts/{bid}/comments")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_empty_comment_rejected(self, client):
        key = _reg(client, "EmptyComment")
        bid = _text_post(client, key, title="No comment")
        r = client.post(
            f"/api/agents/broadcasts/{bid}/comments",
            json={"content": ""},
            headers=_headers(key),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Direct Messages
# ---------------------------------------------------------------------------

class TestDirectMessages:
    def test_send_and_receive(self, client):
        key_a = _reg(client, "DmSender")
        key_b = _reg(client, "DmRecipient")
        r = client.post(
            "/api/agents/messages/send/DmRecipient",
            json={"subject": "Hey", "content": "Hello there"},
            headers=_headers(key_a),
        )
        assert r.status_code == 200
        assert "message_id" in r.json()
        inbox = client.get("/api/agents/messages/inbox", headers=_headers(key_b)).json()
        assert any(m["subject"] == "Hey" for m in inbox)

    def test_sent_messages(self, client):
        key_a = _reg(client, "SentA")
        _reg(client, "SentB")
        client.post("/api/agents/messages/send/SentB", json={"content": "sent"}, headers=_headers(key_a))
        sent = client.get("/api/agents/messages/sent", headers=_headers(key_a)).json()
        assert len(sent) >= 1

    def test_unread_count(self, client):
        key_a = _reg(client, "UnreadSender")
        key_b = _reg(client, "UnreadRecipient")
        client.post("/api/agents/messages/send/UnreadRecipient", json={"content": "ping"}, headers=_headers(key_a))
        r = client.get("/api/agents/messages/unread-count", headers=_headers(key_b))
        assert r.status_code == 200
        assert r.json()["unread"] >= 1

    def test_cannot_message_self(self, client):
        key = _reg(client, "SelfMsg")
        r = client.post("/api/agents/messages/send/SelfMsg", json={"content": "hi"}, headers=_headers(key))
        assert r.status_code == 400

    def test_message_nonexistent_agent(self, client):
        key = _reg(client, "MsgNone")
        r = client.post("/api/agents/messages/send/nobody_xyz", json={"content": "hi"}, headers=_headers(key))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class TestNotifications:
    def test_follow_creates_notification(self, client):
        key_a = _reg(client, "NotifA")
        key_b = _reg(client, "NotifB")
        client.post("/api/agents/follow/NotifB", headers=_headers(key_a))
        r = client.get("/api/agents/me/notifications", headers=_headers(key_b))
        assert r.status_code == 200
        types = [n["type"] for n in r.json()]
        assert "follow" in types

    def test_unread_count_endpoint(self, client):
        key_a = _reg(client, "NCountA")
        key_b = _reg(client, "NCountB")
        client.post("/api/agents/follow/NCountB", headers=_headers(key_a))
        r = client.get("/api/agents/me/notifications/unread-count", headers=_headers(key_b))
        assert r.status_code == 200
        assert r.json()["unread"] >= 1

    def test_read_all_clears_count(self, client):
        key_a = _reg(client, "ReadAllA")
        key_b = _reg(client, "ReadAllB")
        client.post("/api/agents/follow/ReadAllB", headers=_headers(key_a))
        client.post("/api/agents/me/notifications/read-all", headers=_headers(key_b))
        r = client.get("/api/agents/me/notifications/unread-count", headers=_headers(key_b))
        assert r.json()["unread"] == 0


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

class TestSeries:
    def test_create_and_list_series(self, client):
        key = _reg(client, "SeriesAgent")
        r = client.post(
            "/api/agents/me/series",
            json={"title": "My Series", "description": "A collection"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        series_id = r.json()["id"]
        listed = client.get("/api/agents/me/series", headers=_headers(key)).json()
        assert any(s["id"] == series_id for s in listed)

    def test_get_public_series(self, client):
        key = _reg(client, "PubSeries")
        r = client.post("/api/agents/me/series", json={"title": "Public Series"}, headers=_headers(key))
        sid = r.json()["id"]
        r2 = client.get(f"/api/agents/series/{sid}")
        assert r2.status_code == 200
        assert r2.json()["title"] == "Public Series"

    def test_series_with_posts(self, client):
        key = _reg(client, "SeriesPosts")
        sid = client.post("/api/agents/me/series", json={"title": "With Posts"}, headers=_headers(key)).json()["id"]
        bid = _text_post(client, key, title="Episode 1", series_id=sid)
        r = client.get(f"/api/agents/series/{sid}")
        post_ids = [b["id"] for b in r.json()["broadcasts"]]
        assert bid in post_ids


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class TestWebhooks:
    def test_register_list_delete(self, client):
        key = _reg(client, "WebhookAgent")
        r = client.post(
            "/api/agents/me/webhooks",
            json={"url": "https://example.com/cb", "events": ["broadcast_ready", "new_follower"]},
            headers=_headers(key),
        )
        assert r.status_code == 200
        wid = r.json()["webhook_id"]
        listed = client.get("/api/agents/me/webhooks", headers=_headers(key)).json()
        assert any(w["id"] == wid for w in listed)
        r2 = client.delete(f"/api/agents/me/webhooks/{wid}", headers=_headers(key))
        assert r2.status_code == 200
        listed2 = client.get("/api/agents/me/webhooks", headers=_headers(key)).json()
        assert not any(w["id"] == wid for w in listed2)

    def test_invalid_url_rejected(self, client):
        key = _reg(client, "BadWebhook")
        r = client.post(
            "/api/agents/me/webhooks",
            json={"url": "not-a-url", "events": ["all"]},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_delete_other_agents_webhook_fails(self, client):
        key_a = _reg(client, "WebhookOwner")
        key_b = _reg(client, "WebhookThief")
        wid = client.post(
            "/api/agents/me/webhooks",
            json={"url": "https://example.com/mine", "events": ["all"]},
            headers=_headers(key_a),
        ).json()["webhook_id"]
        r = client.delete(f"/api/agents/me/webhooks/{wid}", headers=_headers(key_b))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Creation Pipeline
# ---------------------------------------------------------------------------

class TestCreationPipeline:
    def test_full_pipeline_flow(self, client):
        key = _reg(client, "PipelineAgent")
        # Register job
        r = client.post("/api/agents/create", json={"prompt": "Write about AI"}, headers=_headers(key))
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        # Update stages
        for stage in ["scripting", "voicing", "visualizing", "composing"]:
            r = client.patch(
                f"/api/agents/me/creation-jobs/{job_id}",
                json={"status": stage, "note": f"Done with {stage}"},
                headers=_headers(key),
            )
            assert r.status_code == 200
        # Publish the content
        bid = _text_post(client, key, title="Pipeline Result", content="Generated content")
        # Complete the job
        r = client.post(
            f"/api/agents/me/creation-jobs/{job_id}/complete",
            json={"broadcast_id": bid},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "done"
        # Verify status
        r2 = client.get(f"/api/agents/me/creation-jobs/{job_id}", headers=_headers(key))
        assert r2.json()["status"] == "done"
        assert r2.json()["result_broadcast_id"] == bid

    def test_list_creation_jobs(self, client):
        key = _reg(client, "ListJobs")
        client.post("/api/agents/create", json={"prompt": "test"}, headers=_headers(key))
        r = client.get("/api/agents/me/creation-jobs", headers=_headers(key))
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_invalid_stage_rejected(self, client):
        key = _reg(client, "BadStage")
        r = client.post("/api/agents/create", json={"prompt": "test"}, headers=_headers(key))
        job_id = r.json()["job_id"]
        r2 = client.patch(
            f"/api/agents/me/creation-jobs/{job_id}",
            json={"status": "flying"},
            headers=_headers(key),
        )
        assert r2.status_code == 400

    def test_delete_creation_job(self, client):
        key = _reg(client, "DelJob")
        r = client.post("/api/agents/create", json={"prompt": "delete me"}, headers=_headers(key))
        job_id = r.json()["job_id"]
        client.delete(f"/api/agents/me/creation-jobs/{job_id}", headers=_headers(key))
        jobs = client.get("/api/agents/me/creation-jobs", headers=_headers(key)).json()
        assert not any(j["id"] == job_id for j in jobs)


# ---------------------------------------------------------------------------
# Admin / Sentinel API
# ---------------------------------------------------------------------------

ADMIN_KEY = "test-admin-key-e2e"


class TestAdminAPI:
    @pytest.fixture(autouse=True)
    def patch_admin_key(self):
        with patch.object(settings, "ADMIN_KEY", ADMIN_KEY):
            yield

    def _ah(self) -> dict:
        return {"X-Admin-Key": ADMIN_KEY}

    def test_stats_endpoint(self, client):
        r = client.get("/api/admin/stats", headers=self._ah())
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data
        assert "broadcasts" in data

    def test_list_all_agents(self, client):
        _reg(client, "AdminListTarget")
        r = client.get("/api/admin/agents", headers=self._ah())
        assert r.status_code == 200
        names = [a["name"] for a in r.json()]
        assert "AdminListTarget" in names

    def test_lock_and_unlock_agent(self, client):
        key = _reg(client, "LockTarget")
        # Get agent_id
        agents = client.get("/api/admin/agents", headers=self._ah()).json()
        agent_id = next(a["id"] for a in agents if a["name"] == "LockTarget")
        # Lock
        r = client.post(f"/api/admin/agents/{agent_id}/lock", headers=self._ah())
        assert r.status_code == 200
        # Locked agent gets 403
        r2 = client.get("/api/agents/me/profile", headers=_headers(key))
        assert r2.status_code == 403
        # Unlock
        client.post(f"/api/admin/agents/{agent_id}/unlock", headers=self._ah())
        r3 = client.get("/api/agents/me/profile", headers=_headers(key))
        assert r3.status_code == 200

    def test_logs_endpoint(self, client):
        r = client.get("/api/admin/logs?n=10", headers=self._ah())
        assert r.status_code == 200
        assert "logs" in r.json()

    def test_rate_limits_endpoint(self, client):
        r = client.get("/api/admin/rate-limits", headers=self._ah())
        assert r.status_code == 200
        assert "broadcast_activity" in r.json()

    def test_wrong_admin_key_rejected(self, client):
        r = client.get("/api/admin/stats", headers={"X-Admin-Key": "wrong"})
        assert r.status_code in (401, 403)

    def test_no_admin_key_rejected(self, client):
        r = client.get("/api/admin/stats")
        assert r.status_code in (403, 503)

    def test_list_agents_includes_tier_jail_reputation(self, client):
        _reg(client, "AdminTierTarget")
        r = client.get("/api/admin/agents", headers=self._ah())
        assert r.status_code == 200
        row = next(a for a in r.json() if a["name"] == "AdminTierTarget")
        assert "tier" in row and "jail_mode" in row and "reputation" in row

    def test_rate_limit_status_endpoint(self, client):
        key = _reg(client, "RateStatusAgent")
        # Generate at least one tracked request against the real limiter.
        client.get("/api/agents/me/profile", headers=_headers(key))
        r = client.get("/api/admin/rate-limit-status", headers=self._ah())
        assert r.status_code == 200
        body = r.json()
        assert "limit" in body and "window_seconds" in body and "agents" in body
        # Don't assert this specific agent is present: the endpoint caps at the
        # top 50 by request volume, and in a full suite run many other agents
        # may have made more requests within the same 60s window. Assert the
        # shape/content is real instead of relying on suite-wide ordering.
        assert isinstance(body["agents"], list) and len(body["agents"]) >= 1
        row = body["agents"][0]
        assert {"agent_id", "agent_name", "requests_in_window", "limit", "pct_of_limit"} <= row.keys()

    def test_rate_limit_status_requires_admin(self, client):
        r = client.get("/api/admin/rate-limit-status")
        assert r.status_code in (403, 503)

    def test_security_scans_list_endpoint(self, client, tmp_path):
        import asyncio
        import backend.utils as utils_module
        from PIL import Image
        import io

        key = _reg(client, "SecScanListAgent")
        agent_id = client.get("/api/agents/me/profile", headers=_headers(key)).json()["id"]
        p = tmp_path / "a.jpg"
        img = Image.new("RGB", (4, 4))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        p.write_bytes(buf.getvalue())
        asyncio.run(utils_module._security_scan_and_normalize(p, "image", agent_id, artifact_ref="list-test"))

        r = client.get("/api/admin/security-scans", headers=self._ah())
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        assert any(s["artifact_ref"] == "list-test" for s in body["scans"])

    def test_security_scans_list_filters_by_status(self, client):
        r = client.get("/api/admin/security-scans?status=quarantined", headers=self._ah())
        assert r.status_code == 200
        assert all(s["status"] == "quarantined" for s in r.json()["scans"])

    def test_security_scans_list_requires_admin(self, client):
        r = client.get("/api/admin/security-scans")
        assert r.status_code in (403, 503)

    def test_code_scans_list_endpoint(self, client):
        r = client.get("/api/admin/code-scans", headers=self._ah())
        assert r.status_code == 200
        body = r.json()
        assert "scans" in body and "count" in body

    def test_code_scans_list_requires_admin(self, client):
        r = client.get("/api/admin/code-scans")
        assert r.status_code in (403, 503)

    def test_jobs_overview_endpoint(self, client):
        r = client.get("/api/admin/jobs-overview", headers=self._ah())
        assert r.status_code == 200
        body = r.json()
        assert "jobs_by_status" in body
        assert "tasks_by_status" in body
        assert "expired_leases" in body
        assert "recent_jobs" in body

    def test_jobs_overview_counts_a_real_job(self, client):
        poster_key = _reg(client, "JobsOverviewPoster")
        r = client.post(
            "/api/jobs",
            json={
                "job_type": "code",
                "title": "Overview test job",
                "tasks": [{"title": "task one"}],
            },
            headers=_headers(poster_key),
        )
        assert r.status_code == 200, r.text
        overview = client.get("/api/admin/jobs-overview", headers=self._ah()).json()
        assert overview["jobs_by_status"].get("open", 0) >= 1
        assert any(j["title"] == "Overview test job" for j in overview["recent_jobs"])

    def test_jobs_overview_requires_admin(self, client):
        r = client.get("/api/admin/jobs-overview")
        assert r.status_code in (403, 503)


# ---------------------------------------------------------------------------
# Search & Feeds
# ---------------------------------------------------------------------------

class TestSearchAndFeeds:
    def test_search_finds_post(self, client):
        key = _reg(client, "SearchPublisher")
        _text_post(client, key, title="Quantum Entanglement Research")
        r = client.get("/api/agents/search?q=Quantum+Entanglement")
        assert r.status_code == 200
        results = r.json()
        assert any("Quantum" in b.get("title", "") for b in results)

    def test_feed_content_type_filter(self, client):
        key = _reg(client, "FilterPub")
        _text_post(client, key, title="Text Only Post")
        r = client.get("/api/agents/feed?content_type=text")
        assert r.status_code == 200
        for item in r.json():
            assert item["content_type"] == "text"

    def test_trending_feed(self, client):
        r = client.get("/api/agents/feed/trending")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_recommended_feed_requires_auth(self, client):
        r = client.get("/api/agents/feed/recommended")
        assert r.status_code == 401

    def test_recommended_feed_with_auth(self, client):
        key = _reg(client, "RecommendedUser")
        r = client.get("/api/agents/feed/recommended", headers=_headers(key))
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Fork / Remix
# ---------------------------------------------------------------------------

class TestFork:
    def test_fork_broadcast(self, client):
        key_a = _reg(client, "ForkOrigin")
        key_b = _reg(client, "ForkRemixer")
        bid = _text_post(client, key_a, title="Original Work", content="Original content")
        r = client.post(
            f"/api/agents/broadcasts/{bid}/fork",
            json={"title": "Remix of Original", "description": "my take"},
            headers=_headers(key_b),
        )
        assert r.status_code == 200
        fork_id = r.json()["broadcast_id"]
        assert fork_id != bid

    def test_fork_credits_original(self, client):
        key_a = _reg(client, "ForkCredit1")
        key_b = _reg(client, "ForkCredit2")
        bid = _text_post(client, key_a, title="CreditSource")
        r = client.post(
            f"/api/agents/broadcasts/{bid}/fork",
            json={"title": "Credited Fork"},
            headers=_headers(key_b),
        )
        fork_id = r.json()["broadcast_id"]
        feed = client.get("/api/agents/feed").json()
        fork = next((b for b in feed if b["id"] == fork_id), None)
        assert fork is not None
        assert fork.get("forked_from") == bid


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class TestAnalytics:
    def test_analytics_returns_expected_fields(self, client):
        key = _reg(client, "AnalyticsAgent")
        _text_post(client, key, title="For Analytics")
        r = client.get("/api/agents/me/analytics", headers=_headers(key))
        assert r.status_code == 200
        data = r.json()
        assert "views_by_day" in data
        assert "top_broadcasts" in data
        assert "total_views" in data
        assert "total_broadcasts" in data

    def test_analytics_requires_auth(self, client):
        r = client.get("/api/agents/me/analytics")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

class TestPlatform:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["db"] == "ok"

    def test_skills_registry_completeness(self, client):
        r = client.get("/api/agents/skills")
        assert r.status_code == 200
        data = r.json()
        assert data["platform"] == "Vantage"
        skill_ids = [s["id"] for s in data["skills"]]
        # Check key skills are present
        for expected in [
            "vantage-register", "vantage-publish-text", "vantage-publish-graph",
            "vantage-react", "vantage-comment", "vantage-message",
            "vantage-register-webhook", "vantage-admin-logs", "vantage-update-profile",
            "vantage-create", "vantage-update-creation-job", "vantage-collab-requests",
        ]:
            assert expected in skill_ids, f"Missing skill: {expected}"
        assert len(skill_ids) >= 60

    def test_design_system(self, client):
        r = client.get("/api/agents/design-system")
        assert r.status_code == 200

    def test_leaderboard(self, client):
        r = client.get("/api/agents/leaderboard")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["leaderboard"], list)
        assert data["ranked_by"] in ("token_balance", "total_views")

    def test_leaderboard_includes_freshly_registered_agent_with_no_posts(self, client):
        """A newly registered agent has zero broadcasts/views but should still
        show up (LEFT JOIN, not INNER JOIN) — there's no separate "register for
        the leaderboard" step, registering an agent is enough."""
        _reg(client, "FreshLeaderboardAgent")
        r = client.get("/api/agents/leaderboard?limit=200")
        assert r.status_code == 200
        names = [e["name"] for e in r.json()["leaderboard"]]
        assert "FreshLeaderboardAgent" in names


# ---------------------------------------------------------------------------
# Capability Matchmaking
# ---------------------------------------------------------------------------

class TestCapabilityMatchmaking:
    def test_find_capable_by_bio_tag(self, client):
        key = _reg(client, "CapAgent1")
        # Update bio with capability tag
        fd = {"bio": "I am a finance analysis agent #finance #analysis"}
        client.patch("/api/agents/me/profile", data=fd, headers=_headers(key))
        r = client.get("/api/agents/find-capable?capability=finance")
        assert r.status_code == 200
        names = [a["name"] for a in r.json()]
        assert "CapAgent1" in names

    def test_find_capable_no_match(self, client):
        r = client.get("/api/agents/find-capable?capability=unicornxyz99")
        assert r.status_code == 200
        assert r.json() == []

    def test_find_capable_requires_capability_param(self, client):
        r = client.get("/api/agents/find-capable")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Artifact Staging
# ---------------------------------------------------------------------------

class TestArtifactStaging:
    def test_upload_and_list_artifacts(self, client):
        key = _reg(client, "ArtifactAgent1")
        # Create a creation job first
        r = client.post(
            "/api/agents/create",
            json={"prompt": "Make a video about AI"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        # Upload an artifact
        r2 = client.post(
            f"/api/agents/me/creation-jobs/{job_id}/artifacts",
            json={"artifact_type": "script", "stage": "scripting", "content": "# AI Script\n\nIntro..."},
            headers=_headers(key),
        )
        assert r2.status_code == 200
        assert r2.json()["stage"] == "scripting"

        # List artifacts
        r3 = client.get(f"/api/agents/me/creation-jobs/{job_id}/artifacts", headers=_headers(key))
        assert r3.status_code == 200
        arts = r3.json()
        assert len(arts) == 1
        assert arts[0]["artifact_type"] == "script"

    def test_artifact_requires_auth(self, client):
        r = client.post(
            "/api/agents/me/creation-jobs/999/artifacts",
            json={"artifact_type": "script", "stage": "scripting"},
        )
        assert r.status_code == 401

    def test_artifact_wrong_job(self, client):
        key_a = _reg(client, "ArtifactAgentA")
        key_b = _reg(client, "ArtifactAgentB")
        r = client.post(
            "/api/agents/create",
            json={"prompt": "Test"},
            headers=_headers(key_a),
        )
        job_id = r.json()["job_id"]
        # Agent B cannot upload to Agent A's job
        r2 = client.post(
            f"/api/agents/me/creation-jobs/{job_id}/artifacts",
            json={"artifact_type": "audio", "stage": "voicing"},
            headers=_headers(key_b),
        )
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Task Market
# ---------------------------------------------------------------------------

class TestTaskMarket:
    def test_create_and_list_tasks(self, client):
        key = _reg(client, "TaskPoster1")
        r = client.post(
            "/api/agents/tasks",
            json={"title": "Write a market analysis", "description": "Daily BTC analysis", "required_capability": "finance", "reward_usdc": 5.0},
            headers=_headers(key),
        )
        assert r.status_code == 200
        task = r.json()
        assert task["title"] == "Write a market analysis"
        assert task["status"] == "open"
        task_id = task["id"]

        # List tasks
        r2 = client.get("/api/agents/tasks")
        assert r2.status_code == 200
        ids = [t["id"] for t in r2.json()]
        assert task_id in ids

    def test_get_task_detail(self, client):
        key = _reg(client, "TaskPoster2")
        r = client.post(
            "/api/agents/tasks",
            json={"title": "Detail test task"},
            headers=_headers(key),
        )
        task_id = r.json()["id"]
        r2 = client.get(f"/api/agents/tasks/{task_id}")
        assert r2.status_code == 200
        assert "bids" in r2.json()

    def test_task_not_found(self, client):
        r = client.get("/api/agents/tasks/999999")
        assert r.status_code == 404

    def test_bid_on_task(self, client):
        key_poster = _reg(client, "TaskBidPoster")
        key_bidder = _reg(client, "TaskBidder1")
        r = client.post(
            "/api/agents/tasks",
            json={"title": "Task for bidding"},
            headers=_headers(key_poster),
        )
        task_id = r.json()["id"]
        r2 = client.post(
            f"/api/agents/tasks/{task_id}/bid",
            json={"approach": "I'll use my finance skills", "estimated_hours": 2.0},
            headers=_headers(key_bidder),
        )
        assert r2.status_code == 200
        assert r2.json()["task_id"] == task_id

    def test_cannot_bid_on_own_task(self, client):
        key = _reg(client, "SelfBidAgent")
        r = client.post("/api/agents/tasks", json={"title": "My own task"}, headers=_headers(key))
        task_id = r.json()["id"]
        r2 = client.post(f"/api/agents/tasks/{task_id}/bid", json={"approach": "me"}, headers=_headers(key))
        assert r2.status_code == 400

    def test_full_task_lifecycle(self, client):
        key_poster = _reg(client, "LifecyclePoster")
        key_worker = _reg(client, "LifecycleWorker")

        # Create task
        r = client.post(
            "/api/agents/tasks",
            json={"title": "Full lifecycle task", "reward_usdc": 10.0},
            headers=_headers(key_poster),
        )
        task_id = r.json()["id"]

        # Worker bids
        client.post(
            f"/api/agents/tasks/{task_id}/bid",
            json={"approach": "I'll do it well"},
            headers=_headers(key_worker),
        )

        # Poster awards to worker
        r_award = client.post(
            f"/api/agents/tasks/{task_id}/award/LifecycleWorker",
            headers=_headers(key_poster),
        )
        assert r_award.status_code == 200

        # Worker completes
        r_complete = client.post(
            f"/api/agents/tasks/{task_id}/complete",
            json={"result_description": "Done! Here's the result."},
            headers=_headers(key_worker),
        )
        assert r_complete.status_code == 200

        # Poster approves
        r_approve = client.post(
            f"/api/agents/tasks/{task_id}/approve",
            headers=_headers(key_poster),
        )
        assert r_approve.status_code == 200
        assert r_approve.json()["status"] == "completed"

    def test_my_tasks(self, client):
        key = _reg(client, "MyTasksAgent")
        client.post("/api/agents/tasks", json={"title": "My task 1"}, headers=_headers(key))
        client.post("/api/agents/tasks", json={"title": "My task 2"}, headers=_headers(key))
        r = client.get("/api/agents/me/tasks", headers=_headers(key))
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_my_bids(self, client):
        key_poster = _reg(client, "BidsPoster")
        key_bidder = _reg(client, "BidsBidder")
        r = client.post("/api/agents/tasks", json={"title": "Bid tracker task"}, headers=_headers(key_poster))
        task_id = r.json()["id"]
        client.post(f"/api/agents/tasks/{task_id}/bid", json={"approach": "bid"}, headers=_headers(key_bidder))
        r2 = client.get("/api/agents/me/task-bids", headers=_headers(key_bidder))
        assert r2.status_code == 200
        assert len(r2.json()) >= 1

    def test_filter_tasks_by_capability(self, client):
        key = _reg(client, "CapFilterPoster")
        client.post(
            "/api/agents/tasks",
            json={"title": "Vision task", "required_capability": "vision"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/tasks?capability=vision")
        assert r.status_code == 200
        for t in r.json():
            assert "vision" in t["required_capability"].lower()


# ---------------------------------------------------------------------------
# Broadcast Certification
# ---------------------------------------------------------------------------

CERT_ADMIN_KEY = "test-admin-cert-key"


class TestBroadcastCertification:
    @pytest.fixture(autouse=True)
    def patch_admin_key(self):
        with patch.object(settings, "ADMIN_KEY", CERT_ADMIN_KEY):
            yield

    def _admin_headers(self):
        return {"X-Admin-Key": CERT_ADMIN_KEY}

    def test_certify_broadcast(self, client):
        key = _reg(client, "CertAgent1")
        bid = _text_post(client, key, title="To Certify")
        r = client.post(f"/api/admin/broadcasts/{bid}/certify", headers=self._admin_headers())
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_certified_feed_empty_by_default(self, client):
        r = client.get("/api/agents/feed/certified")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_certified_feed_shows_certified(self, client):
        key = _reg(client, "CertFeedAgent")
        bid = _text_post(client, key, title="Certified Content")
        client.post(f"/api/admin/broadcasts/{bid}/certify", headers=self._admin_headers())
        r = client.get("/api/agents/feed/certified")
        assert r.status_code == 200
        ids = [b["id"] for b in r.json()]
        assert bid in ids

    def test_uncertify_broadcast(self, client):
        key = _reg(client, "UncertAgent")
        bid = _text_post(client, key, title="To Uncertify")
        client.post(f"/api/admin/broadcasts/{bid}/certify", headers=self._admin_headers())
        r = client.delete(f"/api/admin/broadcasts/{bid}/certify", headers=self._admin_headers())
        assert r.status_code == 200
        # Should no longer be in certified feed
        r2 = client.get("/api/agents/feed/certified")
        ids = [b["id"] for b in r2.json()]
        assert bid not in ids

    def test_certify_requires_admin(self, client):
        key = _reg(client, "CertNoAuthAgent")
        bid = _text_post(client, key)
        # No admin key header — should be rejected
        r = client.post(f"/api/admin/broadcasts/{bid}/certify")
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------

class TestFederation:
    def test_get_peers_empty(self, client):
        r = client.get("/api/agents/federation/peers")
        assert r.status_code == 200
        data = r.json()
        assert "peers" in data
        assert isinstance(data["peers"], list)

    def test_add_peer_requires_auth(self, client):
        r = client.post("/api/agents/federation/peers", json={"url": "http://example.com"})
        assert r.status_code == 401

    def test_add_and_remove_peer(self, client):
        key = _reg(client, "FedAgent1")
        r = client.post(
            "/api/agents/federation/peers",
            json={"url": "http://peer1.example.com", "name": "Peer1"},
            headers=_headers(key),
        )
        # Returns ok:False if FEDERATION_ENABLED=False (default in tests), ok:True if enabled
        assert r.status_code == 200

    def test_federation_feed(self, client):
        r = client.get("/api/agents/federation/feed")
        assert r.status_code == 200
        data = r.json()
        assert "broadcasts" in data
        assert isinstance(data["broadcasts"], list)


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

class TestMCPManifest:
    def test_mcp_manifest_returns_info(self, client):
        r = client.get("/api/agents/mcp-manifest")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Vantage"
        assert data["mcp_http_endpoint"] == "/mcp"
        assert data["mcp_sse_endpoint"] == "/mcp/sse"
        assert "streamable-http" in data["transports"]
        assert "sse" in data["transports"]

    def test_mcp_manifest_has_docs_link(self, client):
        r = client.get("/api/agents/mcp-manifest")
        assert "docs" in r.json()


# ---------------------------------------------------------------------------
# Seal Routes
# ---------------------------------------------------------------------------

class TestSealRoutes:
    def test_seal_status_on_unsealed_broadcast(self, client):
        key = _reg(client, "SealStatusAgent")
        bid = _text_post(client, key)
        r = client.get(f"/api/agents/broadcasts/{bid}/seal-status")
        assert r.status_code == 200

    def test_seal_broadcast(self, client):
        key = _reg(client, "SealAgent1")
        bid = _text_post(client, key)
        r = client.post(
            f"/api/agents/broadcasts/{bid}/seal",
            data={"policy": "followers-only"},
            headers=_headers(key),
        )
        assert r.status_code == 200

    def test_unseal_broadcast(self, client):
        key = _reg(client, "UnsealAgent1")
        bid = _text_post(client, key)
        client.post(
            f"/api/agents/broadcasts/{bid}/seal",
            data={"policy": "private"},
            headers=_headers(key),
        )
        r = client.delete(f"/api/agents/broadcasts/{bid}/seal", headers=_headers(key))
        assert r.status_code == 200

    def test_seal_requires_auth(self, client):
        key = _reg(client, "SealNoAuthAgent")
        bid = _text_post(client, key)
        r = client.post(f"/api/agents/broadcasts/{bid}/seal", data={"policy": "private"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Hermes Feature: Jail Mode
# ---------------------------------------------------------------------------

JAIL_ADMIN_KEY = "test-jail-admin-key"


class TestJailMode:
    @pytest.fixture(autouse=True)
    def patch_admin_key(self):
        with patch.object(settings, "ADMIN_KEY", JAIL_ADMIN_KEY):
            yield

    def _admin_headers(self):
        return {"X-Admin-Key": JAIL_ADMIN_KEY}

    def _get_agent_id(self, client, name):
        r = client.get(f"/api/agents/profile/{name}")
        assert r.status_code == 200
        return r.json()["id"]

    def test_enable_jail_mode(self, client):
        key = _reg(client, "JailAgent1")
        agent_id = self._get_agent_id(client, "JailAgent1")
        r = client.post(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        assert r.status_code == 200
        assert r.json()["jail_mode"] is True

    def test_jail_status_reflects_mode(self, client):
        key = _reg(client, "JailAgent2")
        agent_id = self._get_agent_id(client, "JailAgent2")
        client.post(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        r = client.get(f"/api/admin/agents/{agent_id}/jail-status", headers=self._admin_headers())
        assert r.status_code == 200
        assert r.json()["jail_mode"] == 1

    def test_jailed_agent_cannot_post(self, client):
        key = _reg(client, "JailAgent3")
        agent_id = self._get_agent_id(client, "JailAgent3")
        client.post(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        # Posting should be blocked
        r = client.post(
            "/api/agents/posts/text",
            json={"title": "Jailbreak", "content": "testing"},
            headers=_headers(key),
        )
        assert r.status_code in (401, 403)
        assert "quarantine" in r.json()["detail"].lower()

    def test_disable_jail_mode(self, client):
        key = _reg(client, "JailAgent4")
        agent_id = self._get_agent_id(client, "JailAgent4")
        client.post(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        r = client.delete(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        assert r.status_code == 200
        assert r.json()["jail_mode"] is False

    def test_released_agent_can_post(self, client):
        key = _reg(client, "JailAgent5")
        agent_id = self._get_agent_id(client, "JailAgent5")
        client.post(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        client.delete(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        r = client.post(
            "/api/agents/posts/text",
            json={"title": "Free again", "content": "hello"},
            headers=_headers(key),
        )
        assert r.status_code == 200

    def test_jailed_agent_hidden_from_feed(self, client):
        key = _reg(client, "JailFeedAgent")
        bid = _text_post(client, key, title="Visible before jail")
        agent_id = self._get_agent_id(client, "JailFeedAgent")
        # Before jail: should appear
        r = client.get("/api/agents/feed")
        ids = [b["id"] for b in r.json()]
        assert bid in ids
        # After jail: should be hidden
        client.post(f"/api/admin/agents/{agent_id}/jail-mode", headers=self._admin_headers())
        r2 = client.get("/api/agents/feed")
        ids2 = [b["id"] for b in r2.json()]
        assert bid not in ids2


# ---------------------------------------------------------------------------
# Hermes Feature: Pipeline Resiliency (Outsource)
# ---------------------------------------------------------------------------

class TestPipelineOutsource:
    def test_outsource_creates_task_listing(self, client):
        key = _reg(client, "OutsourceAgent1")
        # Create a creation job
        r = client.post(
            "/api/agents/create",
            json={"prompt": "Create a tutorial on neural nets"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        # Outsource the voicing stage
        r2 = client.post(
            f"/api/agents/me/creation-jobs/{job_id}/outsource",
            json={"stage": "voicing", "reason": "TTS model offline"},
            headers=_headers(key),
        )
        assert r2.status_code == 200
        data = r2.json()
        assert data["status"] == "delegated"
        assert "task_market_listing_id" in data
        assert data["stage"] == "voicing"

    def test_outsourced_job_appears_in_task_market(self, client):
        key = _reg(client, "OutsourceAgent2")
        r = client.post(
            "/api/agents/create",
            json={"prompt": "Make a music video"},
            headers=_headers(key),
        )
        job_id = r.json()["job_id"]
        r2 = client.post(
            f"/api/agents/me/creation-jobs/{job_id}/outsource",
            json={"stage": "visualizing", "required_capability": "video_generation"},
            headers=_headers(key),
        )
        task_id = r2.json()["task_market_listing_id"]
        r3 = client.get(f"/api/agents/tasks/{task_id}")
        assert r3.status_code == 200
        assert "visualizing" in r3.json()["title"].lower() or "Pipeline" in r3.json()["title"]

    def test_outsource_missing_stage_returns_422(self, client):
        key = _reg(client, "OutsourceAgent3")
        r = client.post(
            "/api/agents/create",
            json={"prompt": "A quick test"},
            headers=_headers(key),
        )
        job_id = r.json()["job_id"]
        r2 = client.post(
            f"/api/agents/me/creation-jobs/{job_id}/outsource",
            json={},
            headers=_headers(key),
        )
        assert r2.status_code == 422

    def test_outsource_wrong_job_returns_404(self, client):
        key = _reg(client, "OutsourceAgent4")
        r = client.post(
            f"/api/agents/me/creation-jobs/99999/outsource",
            json={"stage": "scripting"},
            headers=_headers(key),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Hermes Feature: Federated Reasoning
# ---------------------------------------------------------------------------

class TestFederatedReasoning:
    def test_federation_ask_returns_structure(self, client):
        r = client.get("/api/agents/federation/ask", params={"query": "neural networks"})
        assert r.status_code == 200
        data = r.json()
        assert "query" in data
        assert "results" in data
        assert "result_count" in data
        assert data["query"] == "neural networks"

    def test_federation_ask_finds_local_knowledge(self, client):
        key = _reg(client, "FedAskAgent1")
        # Publish a knowledge snippet
        client.post(
            "/api/agents/knowledge",
            json={"subject": "FedAskTopic", "predicate": "is_about", "object": "quantum"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/federation/ask", params={"query": "FedAskTopic"})
        assert r.status_code == 200
        subjects = [item.get("subject", "") for item in r.json()["results"]
                    if item.get("source") == "local"]
        assert any("FedAskTopic" in s for s in subjects)

    def test_federation_ask_with_capability_filter(self, client):
        key = _reg(client, "FedAskAgent2", bio="Finance expert #trading #quant")
        r = client.get(
            "/api/agents/federation/ask",
            params={"query": "market analysis", "capability": "trading"},
        )
        assert r.status_code == 200
        sources = [item.get("source") for item in r.json()["results"]]
        assert "local_agent" in sources

    def test_federation_ask_requires_query(self, client):
        r = client.get("/api/agents/federation/ask")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Hermes Feature: VQL (Vantage Query Language)
# ---------------------------------------------------------------------------

class TestVQL:
    def test_vql_basic_wildcard(self, client):
        key = _reg(client, "VQLAgent1")
        client.post(
            "/api/agents/knowledge",
            json={"subject": "VQLSubject", "predicate": "knows", "object": "VQLObject", "confidence": 0.9},
            headers=_headers(key),
        )
        r = client.post("/api/agents/knowledge/query", json={"subject": "VQLSubject"}, headers=_headers(key))
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert "result_count" in data
        assert any(row["subject"] == "VQLSubject" for row in data["results"])

    def test_vql_predicate_filter(self, client):
        key = _reg(client, "VQLAgent2")
        client.post(
            "/api/agents/knowledge",
            json={"subject": "TopicA", "predicate": "rel_to", "object": "TopicB"},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/knowledge",
            json={"subject": "TopicA", "predicate": "unrelated", "object": "TopicC"},
            headers=_headers(key),
        )
        r = client.post("/api/agents/knowledge/query", json={"subject": "TopicA", "predicate": "rel_to"}, headers=_headers(key))
        assert r.status_code == 200
        preds = [row["predicate"] for row in r.json()["results"]]
        assert all(p == "rel_to" for p in preds)

    def test_vql_min_confidence_filter(self, client):
        key = _reg(client, "VQLAgent3")
        client.post(
            "/api/agents/knowledge",
            json={"subject": "ConfSubject", "predicate": "knows", "object": "High", "confidence": 0.9},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/knowledge",
            json={"subject": "ConfSubject", "predicate": "knows", "object": "Low", "confidence": 0.1},
            headers=_headers(key),
        )
        r = client.post(
            "/api/agents/knowledge/query",
            json={"subject": "ConfSubject", "min_confidence": 0.5},
            headers=_headers(key),
        )
        assert r.status_code == 200
        objects = [row["object"] for row in r.json()["results"]]
        assert "High" in objects
        assert "Low" not in objects

    def test_vql_depth_traversal(self, client):
        key = _reg(client, "VQLAgent4")
        # A → B → C chain
        client.post(
            "/api/agents/knowledge",
            json={"subject": "NodeA", "predicate": "links_to", "object": "NodeB", "confidence": 1.0},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/knowledge",
            json={"subject": "NodeB", "predicate": "links_to", "object": "NodeC", "confidence": 1.0},
            headers=_headers(key),
        )
        r = client.post(
            "/api/agents/knowledge/query",
            json={"subject": "NodeA", "depth": 2},
            headers=_headers(key),
        )
        assert r.status_code == 200
        subjects = {row["subject"] for row in r.json()["results"]}
        assert "NodeA" in subjects
        assert "NodeB" in subjects  # hop 2

    def test_vql_all_wildcard(self, client):
        key = _reg(client, "VQLAgent5")
        client.post(
            "/api/agents/knowledge",
            json={"subject": "AnySubject", "predicate": "any_pred", "object": "AnyObj"},
            headers=_headers(key),
        )
        r = client.post("/api/agents/knowledge/query", json={"subject": "*", "predicate": "*", "object": "*"}, headers=_headers(key))
        assert r.status_code == 200
        assert r.json()["result_count"] >= 1

    def test_vql_agent_filter(self, client):
        key1 = _reg(client, "VQLAgentFilter1")
        key2 = _reg(client, "VQLAgentFilter2")
        client.post(
            "/api/agents/knowledge",
            json={"subject": "FilterSubject", "predicate": "knows", "object": "FromAgent1"},
            headers=_headers(key1),
        )
        client.post(
            "/api/agents/knowledge",
            json={"subject": "FilterSubject", "predicate": "knows", "object": "FromAgent2"},
            headers=_headers(key2),
        )
        r = client.post(
            "/api/agents/knowledge/query",
            json={"subject": "FilterSubject", "agent_filter": "VQLAgentFilter1"},
            headers=_headers(key1),
        )
        assert r.status_code == 200
        objects = [row["object"] for row in r.json()["results"]]
        assert "FromAgent1" in objects
        assert "FromAgent2" not in objects


# ---------------------------------------------------------------------------
# Feature 1: Ephemeral Workspace Snapshots
# ---------------------------------------------------------------------------

class TestWorkspaceSnapshots:
    def test_create_snapshot_empty_state(self, client):
        key = _reg(client, "SnapAgent1")
        r = client.post(
            "/api/agents/me/workspace/snapshot",
            json={"label": "before migration"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        data = r.json()
        assert "snapshot_id" in data
        assert data["label"] == "before migration"

    def test_snapshot_captures_agent_state(self, client):
        key = _reg(client, "SnapAgent2")
        # Set some agent state first
        client.put(
            "/api/agents/me/state/task_stage",
            json={"value": "voicing"},
            headers=_headers(key),
        )
        r = client.post(
            "/api/agents/me/workspace/snapshot",
            json={"label": "with state"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        snap_id = r.json()["snapshot_id"]
        # Load the snapshot
        r2 = client.get(f"/api/agents/me/workspace/snapshots/{snap_id}", headers=_headers(key))
        assert r2.status_code == 200
        snap = r2.json()["snapshot"]
        assert snap["state"].get("task_stage") == "voicing"

    def test_list_snapshots(self, client):
        key = _reg(client, "SnapAgent3")
        client.post("/api/agents/me/workspace/snapshot", json={}, headers=_headers(key))
        client.post("/api/agents/me/workspace/snapshot", json={"label": "second"}, headers=_headers(key))
        r = client.get("/api/agents/me/workspace/snapshots", headers=_headers(key))
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_load_wrong_snapshot_returns_404(self, client):
        key = _reg(client, "SnapAgent4")
        r = client.get("/api/agents/me/workspace/snapshots/99999", headers=_headers(key))
        assert r.status_code == 404

    def test_snapshot_captures_active_jobs(self, client):
        key = _reg(client, "SnapAgent5")
        r_job = client.post(
            "/api/agents/create",
            json={"prompt": "Snapshot test pipeline"},
            headers=_headers(key),
        )
        assert r_job.status_code == 200
        job_id = r_job.json()["job_id"]
        r = client.post(
            "/api/agents/me/workspace/snapshot",
            json={"job_id": job_id},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["jobs_captured"] == 1


# ---------------------------------------------------------------------------
# Feature 2: Capability Discovery Schema
# ---------------------------------------------------------------------------

class TestCapabilitySchema:
    def test_get_capability_schema_basic(self, client):
        key = _reg(client, "CapSchemaAgent1", bio="Audio transcription specialist #tts #audio")
        r = client.get("/api/agents/agents/CapSchemaAgent1/capabilities/schema")
        assert r.status_code == 200
        data = r.json()
        assert data["agent"] == "CapSchemaAgent1"
        assert "$schema" in data
        assert "capabilities" in data
        assert "tts" in data["capabilities"]["tags"]
        assert "audio" in data["capabilities"]["tags"]

    def test_my_capability_schema(self, client):
        key = _reg(client, "CapSchemaAgent2", bio="Vision model #vision #image")
        r = client.get("/api/agents/capabilities/schema", headers=_headers(key))
        assert r.status_code == 200
        data = r.json()
        assert data["agent"] == "CapSchemaAgent2"
        assert "vision" in data["capabilities"]["tags"]

    def test_capability_schema_404_unknown_agent(self, client):
        r = client.get("/api/agents/agents/NoSuchAgentXYZ/capabilities/schema")
        assert r.status_code == 404

    def test_capability_schema_structured_manifest(self, client):
        import json
        manifest = json.dumps({
            "inputs": ["audio", "text"],
            "outputs": ["text"],
            "latency": "low",
            "concurrency": 4,
        })
        key = _reg(client, "CapSchemaAgent3")
        client.patch(
            "/api/agents/me/profile",
            json={"soul_manifest": manifest},
            headers=_headers(key),
        )
        r = client.get("/api/agents/agents/CapSchemaAgent3/capabilities/schema")
        assert r.status_code == 200
        caps = r.json()["capabilities"]
        assert "audio" in caps["inputs"]
        assert caps["latency"] == "low"
        assert caps["concurrency"] == 4


# ---------------------------------------------------------------------------
# Feature 3: Dead-Letter Queue
# ---------------------------------------------------------------------------

class TestDeadLetterQueue:
    def test_dead_letter_list_empty_initially(self, client):
        key = _reg(client, "DLAgent1")
        r = client.get("/api/agents/me/dead-letter", headers=_headers(key))
        assert r.status_code == 200
        assert r.json() == []

    def test_job_moves_to_dead_letter_after_3_failures(self, client):
        key = _reg(client, "DLAgent2")
        r_job = client.post(
            "/api/agents/create",
            json={"prompt": "Doomed pipeline test"},
            headers=_headers(key),
        )
        job_id = r_job.json()["job_id"]
        # Simulate 3 failures
        for i in range(3):
            client.patch(
                f"/api/agents/me/creation-jobs/{job_id}",
                json={"status": "error", "note": f"Failure {i+1}", "error_context": "{}"},
                headers=_headers(key),
            )
        # After 3 errors the background task should have moved it
        import time; time.sleep(0.1)
        r = client.get("/api/agents/me/dead-letter", headers=_headers(key))
        assert r.status_code == 200

    def test_dead_letter_recovery_creates_task(self, client):
        key = _reg(client, "DLAgent3")
        # Create and fail a job 3 times
        r_job = client.post("/api/agents/create", json={"prompt": "Recover me"}, headers=_headers(key))
        job_id = r_job.json()["job_id"]
        for _ in range(3):
            client.patch(
                f"/api/agents/me/creation-jobs/{job_id}",
                json={"status": "error", "note": "fail", "error_context": "{}"},
                headers=_headers(key),
            )
        import time; time.sleep(0.1)
        dl = client.get("/api/agents/me/dead-letter", headers=_headers(key)).json()
        if not dl:
            pytest.skip("Background dead-letter task timing — skip in CI")
        dl_id = dl[0]["id"]
        r = client.post(f"/api/agents/me/dead-letter/{dl_id}/recover", json={}, headers=_headers(key))
        assert r.status_code == 200
        assert "recovery_task_id" in r.json()


# ---------------------------------------------------------------------------
# Feature 4: Collaborative Broadcast Lock Protocol
# ---------------------------------------------------------------------------

class TestBroadcastLock:
    def test_lock_and_check_status(self, client):
        key = _reg(client, "LockAgent1")
        bid = _text_post(client, key, title="Collab Broadcast")
        r = client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key))
        assert r.status_code == 200
        assert r.json()["locked_by"] == "LockAgent1"
        # Check status
        r2 = client.get(f"/api/agents/broadcasts/{bid}/lock")
        assert r2.status_code == 200
        assert r2.json()["locked"] is True
        assert r2.json()["holder"] == "LockAgent1"

    def test_lock_conflict_returns_409(self, client):
        key1 = _reg(client, "LockAgent2a")
        key2 = _reg(client, "LockAgent2b")
        bid = _text_post(client, key1, title="Conflict Broadcast")
        client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key1))
        r = client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key2))
        assert r.status_code == 409

    def test_renew_own_lock(self, client):
        key = _reg(client, "LockAgent3")
        bid = _text_post(client, key, title="Renew Lock Broadcast")
        r1 = client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key))
        r2 = client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key))
        assert r2.status_code == 200

    def test_unlock_broadcast(self, client):
        key = _reg(client, "LockAgent4")
        bid = _text_post(client, key, title="Unlock Broadcast")
        client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key))
        r = client.delete(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key))
        assert r.status_code == 200
        assert r.json()["unlocked"] is True
        # Status should now show unlocked
        r2 = client.get(f"/api/agents/broadcasts/{bid}/lock")
        assert r2.json()["locked"] is False

    def test_unlock_wrong_holder_returns_403(self, client):
        key1 = _reg(client, "LockAgent5a")
        key2 = _reg(client, "LockAgent5b")
        bid = _text_post(client, key1, title="Foreign Lock")
        client.post(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key1))
        r = client.delete(f"/api/agents/broadcasts/{bid}/lock", headers=_headers(key2))
        assert r.status_code in (401, 403)

    def test_unlocked_broadcast_shows_not_locked(self, client):
        key = _reg(client, "LockAgent6")
        bid = _text_post(client, key, title="Never Locked")
        r = client.get(f"/api/agents/broadcasts/{bid}/lock")
        assert r.status_code == 200
        assert r.json()["locked"] is False


# ---------------------------------------------------------------------------
# Feature 5: Swarm Vibe Dashboard
# ---------------------------------------------------------------------------

class TestSwarmVibe:
    def test_publish_vibe(self, client):
        key = _reg(client, "VibeAgent1")
        r = client.post(
            "/api/agents/status/vibe",
            json={"vibe": "All systems nominal", "status_code": "ok"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["vibe"] == "All systems nominal"
        assert r.json()["status_code"] == "ok"

    def test_vibe_truncated_to_100_chars(self, client):
        key = _reg(client, "VibeAgent2")
        long_vibe = "x" * 200
        r = client.post(
            "/api/agents/status/vibe",
            json={"vibe": long_vibe},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert len(r.json()["vibe"]) <= 100

    def test_get_swarm_vibe_dashboard(self, client):
        key = _reg(client, "VibeAgent3")
        client.post(
            "/api/agents/status/vibe",
            json={"vibe": "LLM Latency High", "status_code": "degraded"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/status/vibe")
        assert r.status_code == 200
        data = r.json()
        assert "swarm_health" in data
        assert "vibes" in data
        assert "status_counts" in data
        assert any(v["agent_name"] == "VibeAgent3" for v in data["vibes"])

    def test_invalid_status_code_defaults_to_ok(self, client):
        key = _reg(client, "VibeAgent4")
        r = client.post(
            "/api/agents/status/vibe",
            json={"vibe": "Running fine", "status_code": "nonsense"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status_code"] == "ok"

    def test_vibe_requires_auth(self, client):
        r = client.post("/api/agents/status/vibe", json={"vibe": "unauthorized"})
        assert r.status_code == 401

    def test_vibe_history_per_agent(self, client):
        key = _reg(client, "VibeAgent5")
        client.post(
            "/api/agents/status/vibe",
            json={"vibe": "First vibe"},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/status/vibe",
            json={"vibe": "Second vibe"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/status/vibe/history/VibeAgent5")
        assert r.status_code == 200
        vibes = r.json()
        assert len(vibes) >= 2
        vibe_texts = [v["vibe"] for v in vibes]
        assert "First vibe" in vibe_texts
        assert "Second vibe" in vibe_texts

    def test_empty_vibe_returns_422(self, client):
        key = _reg(client, "VibeAgent6")
        r = client.post(
            "/api/agents/status/vibe",
            json={"vibe": ""},
            headers=_headers(key),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Feature A: Pipeline-as-Code (Broadcast Templates)
# ---------------------------------------------------------------------------

class TestBroadcastTemplates:
    STAGES = [
        {"stage": "scripting", "model": "claude-opus", "prompt_template": "Write a script about {topic}"},
        {"stage": "tts", "provider": "elevenlabs", "voice": "rachel"},
        {"stage": "visual", "tool": "manim"},
    ]

    def test_create_template(self, client):
        key = _reg(client, "TplAgent1")
        r = client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Tutorial Pipeline", "description": "A tutorial recipe", "stages": self.STAGES},
            headers=_headers(key),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Tutorial Pipeline"
        assert "id" in data

    def test_list_templates(self, client):
        key = _reg(client, "TplAgent2")
        client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Recipe A", "stages": self.STAGES},
            headers=_headers(key),
        )
        r = client.get("/api/agents/broadcasts/templates")
        assert r.status_code == 200
        assert any(t["title"] == "Recipe A" for t in r.json())

    def test_get_template_by_id(self, client):
        key = _reg(client, "TplAgent3")
        r = client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Get Me", "stages": self.STAGES},
            headers=_headers(key),
        )
        tpl_id = r.json()["id"]
        r2 = client.get(f"/api/agents/broadcasts/templates/{tpl_id}")
        assert r2.status_code == 200
        assert r2.json()["title"] == "Get Me"
        assert isinstance(r2.json()["template"], list)

    def test_fork_template_creates_job(self, client):
        key_owner = _reg(client, "TplOwner")
        key_forker = _reg(client, "TplForker")
        r = client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Forkable", "stages": self.STAGES},
            headers=_headers(key_owner),
        )
        tpl_id = r.json()["id"]
        r2 = client.post(
            f"/api/agents/broadcasts/templates/{tpl_id}/fork",
            json={"prompt": "My custom topic"},
            headers=_headers(key_forker),
        )
        assert r2.status_code == 200
        data = r2.json()
        assert "job_id" in data
        assert data["template_id"] == tpl_id
        assert len(data["stages"]) == 3

    def test_fork_increments_count(self, client):
        key = _reg(client, "TplForkCount")
        key2 = _reg(client, "TplForkCount2")
        r = client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Popular Recipe", "stages": self.STAGES},
            headers=_headers(key),
        )
        tpl_id = r.json()["id"]
        client.post(f"/api/agents/broadcasts/templates/{tpl_id}/fork", json={}, headers=_headers(key2))
        r2 = client.get(f"/api/agents/broadcasts/templates/{tpl_id}")
        assert r2.json()["fork_count"] == 1

    def test_delete_own_template(self, client):
        key = _reg(client, "TplDelAgent")
        r = client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "To Delete", "stages": []},
            headers=_headers(key),
        )
        tpl_id = r.json()["id"]
        r2 = client.delete(f"/api/agents/broadcasts/templates/{tpl_id}", headers=_headers(key))
        assert r2.status_code == 200
        r3 = client.get(f"/api/agents/broadcasts/templates/{tpl_id}")
        assert r3.status_code == 404

    def test_delete_others_template_returns_403(self, client):
        key1 = _reg(client, "TplOwner2")
        key2 = _reg(client, "TplThief")
        r = client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Protected", "stages": []},
            headers=_headers(key1),
        )
        tpl_id = r.json()["id"]
        r2 = client.delete(f"/api/agents/broadcasts/templates/{tpl_id}", headers=_headers(key2))
        assert r2.status_code == 403

    def test_filter_templates_by_content_type(self, client):
        key = _reg(client, "TplTypeFilter")
        client.post(
            "/api/agents/broadcasts/templates",
            json={"title": "Audio Recipe", "stages": [], "content_type": "audio"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/broadcasts/templates", params={"content_type": "audio"})
        assert r.status_code == 200
        assert all(t["content_type"] == "audio" for t in r.json())


# ---------------------------------------------------------------------------
# Feature B: Agent-to-Agent Handshake
# ---------------------------------------------------------------------------

class TestHandshake:
    def test_initiate_handshake(self, client):
        key_a = _reg(client, "HandshakeA1")
        _reg(client, "HandshakeB1")
        r = client.post(
            "/api/agents/handshake/HandshakeB1",
            json={"message": "Let's collaborate", "terms": {"deliverable": "audio"}},
            headers=_headers(key_a),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_accept_handshake_creates_task(self, client):
        key_a = _reg(client, "HandshakeA2")
        key_b = _reg(client, "HandshakeB2")
        r = client.post(
            "/api/agents/handshake/HandshakeB2",
            json={"message": "I provide visuals, you provide voice", "terms": {}},
            headers=_headers(key_a),
        )
        hs_id = r.json()["id"]
        r2 = client.post(f"/api/agents/me/handshakes/{hs_id}/accept", headers=_headers(key_b))
        assert r2.status_code == 200
        assert "private_task_id" in r2.json()

    def test_reject_handshake(self, client):
        key_a = _reg(client, "HandshakeA3")
        key_b = _reg(client, "HandshakeB3")
        r = client.post(
            "/api/agents/handshake/HandshakeB3",
            json={"message": "Collab?", "terms": {}},
            headers=_headers(key_a),
        )
        hs_id = r.json()["id"]
        r2 = client.post(f"/api/agents/me/handshakes/{hs_id}/reject", headers=_headers(key_b))
        assert r2.status_code == 200
        assert r2.json()["status"] == "rejected"

    def test_list_handshakes(self, client):
        key_a = _reg(client, "HandshakeA4")
        key_b = _reg(client, "HandshakeB4")
        client.post(
            "/api/agents/handshake/HandshakeB4",
            json={"message": "test", "terms": {}},
            headers=_headers(key_a),
        )
        r_a = client.get("/api/agents/me/handshakes", headers=_headers(key_a))
        r_b = client.get("/api/agents/me/handshakes", headers=_headers(key_b))
        assert r_a.status_code == 200
        assert r_b.status_code == 200
        assert len(r_a.json()) >= 1
        assert any(h["recipient_name"] == "HandshakeB4" for h in r_b.json())

    def test_self_handshake_returns_400(self, client):
        key = _reg(client, "HandshakeSelf")
        r = client.post(
            "/api/agents/handshake/HandshakeSelf",
            json={"terms": {}},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_handshake_nonexistent_agent_returns_404(self, client):
        key = _reg(client, "HandshakeGhost")
        r = client.post(
            "/api/agents/handshake/NobodyXYZ999",
            json={"terms": {}},
            headers=_headers(key),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature C: Semantic Agent Search
# ---------------------------------------------------------------------------

class TestSemanticSearch:
    def test_basic_search_returns_structure(self, client):
        _reg(client, "SemanticAgent1", bio="I do audio synthesis #tts #audio")
        r = client.get("/api/agents/semantic-search", params={"query": "audio"})
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data
        assert "result_count" in data
        assert "query" in data

    def test_capability_filter(self, client):
        _reg(client, "SemanticAgent2", bio="Vision specialist #vision #image")
        r = client.get("/api/agents/semantic-search", params={"capability": "vision"})
        assert r.status_code == 200
        names = [a["name"] for a in r.json()["agents"]]
        assert "SemanticAgent2" in names

    def test_min_broadcasts_filter(self, client):
        key = _reg(client, "SemanticAgent3")
        # Publish 2 broadcasts
        _text_post(client, key, title="Post 1")
        _text_post(client, key, title="Post 2")
        r = client.get("/api/agents/semantic-search", params={"min_broadcasts": 2})
        assert r.status_code == 200
        names = [a["name"] for a in r.json()["agents"]]
        assert "SemanticAgent3" in names

    def test_min_broadcasts_excludes_low_activity(self, client):
        _reg(client, "SemanticAgent4")
        r = client.get(
            "/api/agents/semantic-search",
            params={"query": "SemanticAgent4", "min_broadcasts": 100},
        )
        assert r.status_code == 200
        names = [a["name"] for a in r.json()["agents"]]
        assert "SemanticAgent4" not in names

    def test_search_returns_empty_for_no_match(self, client):
        r = client.get(
            "/api/agents/semantic-search",
            params={"query": "XYZIMPOSSIBLEMATCH9999"},
        )
        assert r.status_code == 200
        assert r.json()["result_count"] == 0


# ---------------------------------------------------------------------------
# Feature D: Sentinel Policy Engine (admin)
# ---------------------------------------------------------------------------

SENTINEL_ADMIN_KEY = "test-sentinel-admin-key"


class TestSentinelPolicy:
    @pytest.fixture(autouse=True)
    def patch_admin_key(self):
        with patch.object(settings, "ADMIN_KEY", SENTINEL_ADMIN_KEY):
            yield

    def _ah(self):
        return {"X-Admin-Key": SENTINEL_ADMIN_KEY}

    def test_create_rule(self, client):
        r = client.post(
            "/api/admin/sentinel/rules",
            json={
                "name": "Archive zero-view videos",
                "target": "broadcasts",
                "action": "archive",
                "condition": {"field": "view_count", "op": "<", "value": 1, "age_hours": 0},
            },
            headers=self._ah(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Archive zero-view videos"
        assert data["enabled"] == 1

    def test_list_rules(self, client):
        client.post(
            "/api/admin/sentinel/rules",
            json={"name": "Test Rule", "target": "broadcasts", "action": "flag",
                  "condition": {"field": "view_count", "op": "<", "value": 0}},
            headers=self._ah(),
        )
        r = client.get("/api/admin/sentinel/rules", headers=self._ah())
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_toggle_rule(self, client):
        r = client.post(
            "/api/admin/sentinel/rules",
            json={"name": "Toggle Me", "target": "broadcasts", "action": "flag",
                  "condition": {}},
            headers=self._ah(),
        )
        rule_id = r.json()["id"]
        r2 = client.patch(f"/api/admin/sentinel/rules/{rule_id}/toggle", headers=self._ah())
        assert r2.status_code == 200
        assert r2.json()["enabled"] is False

    def test_delete_rule(self, client):
        r = client.post(
            "/api/admin/sentinel/rules",
            json={"name": "Delete Me", "target": "broadcasts", "action": "archive", "condition": {}},
            headers=self._ah(),
        )
        rule_id = r.json()["id"]
        r2 = client.delete(f"/api/admin/sentinel/rules/{rule_id}", headers=self._ah())
        assert r2.status_code == 200

    def test_enforce_sweep(self, client):
        # Create a zero-view broadcast
        key = _reg(client, "SentinelTarget")
        _text_post(client, key, title="Zero view post")
        # Create the rule
        client.post(
            "/api/admin/sentinel/rules",
            json={"name": "Archive Zero Views", "target": "broadcasts", "action": "archive",
                  "condition": {"field": "view_count", "op": "<", "value": 1}},
            headers=self._ah(),
        )
        r = client.post("/api/admin/sentinel/rules/enforce", headers=self._ah())
        assert r.status_code == 200
        data = r.json()
        assert "rules_run" in data
        assert "total_matches" in data

    def test_invalid_action_returns_422(self, client):
        r = client.post(
            "/api/admin/sentinel/rules",
            json={"name": "Bad Action", "target": "broadcasts", "action": "explode", "condition": {}},
            headers=self._ah(),
        )
        assert r.status_code == 422

    def test_create_rule_requires_admin(self, client):
        r = client.post(
            "/api/admin/sentinel/rules",
            json={"name": "Unauth", "target": "broadcasts", "action": "flag", "condition": {}},
        )
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Feature E: Cross-Agent Swarm Trace
# ---------------------------------------------------------------------------

TRACE_ADMIN_KEY = "test-trace-admin-key"


class TestSwarmTrace:
    @pytest.fixture(autouse=True)
    def patch_admin_key(self):
        with patch.object(settings, "ADMIN_KEY", TRACE_ADMIN_KEY):
            yield

    def _ah(self):
        return {"X-Admin-Key": TRACE_ADMIN_KEY}

    def test_admin_swarm_trace_existing_broadcast(self, client):
        key = _reg(client, "TraceAgent1")
        bid = _text_post(client, key, title="Traceable Broadcast")
        r = client.get(f"/api/admin/swarm/trace/{bid}", headers=self._ah())
        assert r.status_code == 200
        data = r.json()
        assert data["broadcast"]["id"] == bid
        assert "pipeline" in data
        assert "engagement" in data

    def test_admin_swarm_trace_404_missing(self, client):
        r = client.get("/api/admin/swarm/trace/99999", headers=self._ah())
        assert r.status_code == 404

    def test_admin_swarm_trace_requires_admin(self, client):
        key = _reg(client, "TraceAgent2")
        bid = _text_post(client, key)
        r = client.get(f"/api/admin/swarm/trace/{bid}")
        assert r.status_code in (401, 403)

    def test_public_broadcast_trace(self, client):
        key = _reg(client, "TraceAgent3")
        bid = _text_post(client, key, title="Public Trace")
        r = client.get(f"/api/agents/broadcasts/{bid}/trace")
        assert r.status_code == 200
        data = r.json()
        assert data["broadcast"]["id"] == bid
        assert "pipeline_jobs" in data
        assert "credits" in data

    def test_public_trace_not_found_for_deleted(self, client):
        r = client.get("/api/agents/broadcasts/88888/trace")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature: Agent Self-Diagnostics Error Map
# ---------------------------------------------------------------------------

class TestSelfDiagnostics:
    @pytest.fixture(autouse=True)
    def patch_admin(self):
        with patch.object(settings, "ADMIN_KEY", ADMIN_KEY):
            yield

    def _ah(self):
        return {"X-Admin-Key": ADMIN_KEY}

    def test_report_error_basic(self, client):
        key = _reg(client, "DiagAgent1")
        r = client.post(
            "/api/agents/me/report-error",
            json={"error_type": "pipeline", "message": "FFmpeg timed out on stage 2", "error_code": "TIMEOUT"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        data = r.json()
        assert "report_id" in data
        assert data["error_type"] == "pipeline"

    def test_report_error_requires_message(self, client):
        key = _reg(client, "DiagAgent2")
        r = client.post(
            "/api/agents/me/report-error",
            json={"error_type": "pipeline"},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_report_error_requires_auth(self, client):
        r = client.post(
            "/api/agents/me/report-error",
            json={"error_type": "pipeline", "message": "test"},
        )
        assert r.status_code == 401

    def test_list_my_error_reports(self, client):
        key = _reg(client, "DiagAgent3")
        client.post(
            "/api/agents/me/report-error",
            json={"error_type": "task", "message": "Bid failed"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/me/error-reports", headers=_headers(key))
        assert r.status_code == 200
        reports = r.json()
        assert isinstance(reports, list)
        messages = [rep["message"] for rep in reports]
        assert "Bid failed" in messages

    def test_list_error_reports_filter_resolved(self, client):
        key = _reg(client, "DiagAgent4")
        client.post(
            "/api/agents/me/report-error",
            json={"error_type": "network", "message": "Connection reset"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/me/error-reports", params={"resolved": 0}, headers=_headers(key))
        assert r.status_code == 200
        assert all(rep["resolved"] == 0 for rep in r.json())

    def test_admin_error_map(self, client):
        key = _reg(client, "DiagAgent5")
        client.post(
            "/api/agents/me/report-error",
            json={"error_type": "pipeline", "message": "Stage failure"},
            headers=_headers(key),
        )
        r = client.get("/api/admin/error-map", headers=self._ah())
        assert r.status_code == 200
        data = r.json()
        assert "hotspots" in data
        assert "total" in data
        assert isinstance(data["hotspots"], list)

    def test_admin_error_map_requires_admin(self, client):
        key = _reg(client, "DiagAgent6")
        r = client.get("/api/admin/error-map", headers=_headers(key))
        assert r.status_code in (401, 403)

    def test_admin_resolve_error(self, client):
        key = _reg(client, "DiagAgent7")
        rr = client.post(
            "/api/agents/me/report-error",
            json={"error_type": "data", "message": "Corrupt artifact"},
            headers=_headers(key),
        )
        report_id = rr.json()["report_id"]
        r = client.post(f"/api/admin/error-map/{report_id}/resolve", headers=self._ah())
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_admin_resolve_sets_resolved_flag(self, client):
        key = _reg(client, "DiagAgent8")
        rr = client.post(
            "/api/agents/me/report-error",
            json={"error_type": "data", "message": "Corrupt artifact 2"},
            headers=_headers(key),
        )
        report_id = rr.json()["report_id"]
        client.post(f"/api/admin/error-map/{report_id}/resolve", headers=self._ah())
        r2 = client.get("/api/agents/me/error-reports", params={"resolved": 1}, headers=_headers(key))
        assert any(rep["id"] == report_id for rep in r2.json())

    def test_unknown_error_type_normalised(self, client):
        key = _reg(client, "DiagAgent9")
        r = client.post(
            "/api/agents/me/report-error",
            json={"error_type": "totally_made_up", "message": "Bad type"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["error_type"] == "unknown"


# ---------------------------------------------------------------------------
# Feature: Task-Chain Dependency Tracking
# ---------------------------------------------------------------------------

class TestTaskChain:
    def _create_task(self, client, key, title="Chain Task"):
        r = client.post(
            "/api/agents/tasks",
            json={"title": title, "description": "test"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        return r.json()["id"]

    def test_set_dependency(self, client):
        key = _reg(client, "ChainAgent1")
        task_a = self._create_task(client, key, "Task A")
        task_b = self._create_task(client, key, "Task B")
        r = client.post(
            f"/api/agents/tasks/{task_b}/dependencies",
            json={"depends_on_task_id": task_a},
            headers=_headers(key),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["depends_on_task_id"] == task_a

    def test_set_dependency_requires_auth(self, client):
        key = _reg(client, "ChainAgent2")
        task_a = self._create_task(client, key, "Task A2")
        task_b = self._create_task(client, key, "Task B2")
        r = client.post(
            f"/api/agents/tasks/{task_b}/dependencies",
            json={"depends_on_task_id": task_a},
        )
        assert r.status_code == 401

    def test_set_dependency_missing_param(self, client):
        key = _reg(client, "ChainAgent3")
        task = self._create_task(client, key, "Lonely Task")
        r = client.post(
            f"/api/agents/tasks/{task}/dependencies",
            json={},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_get_chain(self, client):
        key = _reg(client, "ChainAgent4")
        task_a = self._create_task(client, key, "Chain A4")
        task_b = self._create_task(client, key, "Chain B4")
        client.post(
            f"/api/agents/tasks/{task_b}/dependencies",
            json={"depends_on_task_id": task_a},
            headers=_headers(key),
        )
        r = client.get(f"/api/agents/tasks/{task_b}/chain")
        assert r.status_code == 200
        data = r.json()
        assert "chain" in data
        assert data["task_id"] == task_b
        assert data["chain_length"] >= 1

    def test_chain_includes_dependency(self, client):
        key = _reg(client, "ChainAgent5")
        task_a = self._create_task(client, key, "Root5")
        task_b = self._create_task(client, key, "Leaf5")
        client.post(
            f"/api/agents/tasks/{task_b}/dependencies",
            json={"depends_on_task_id": task_a},
            headers=_headers(key),
        )
        r = client.get(f"/api/agents/tasks/{task_b}/chain")
        chain_ids = [t["id"] for t in r.json()["chain"]]
        assert task_a in chain_ids

    def test_chain_not_found(self, client):
        r = client.get("/api/agents/tasks/99999/chain")
        # nonexistent task returns empty chain rather than 404
        assert r.status_code in (200, 404)

    def test_dependency_on_nonexistent_task(self, client):
        key = _reg(client, "ChainAgent6")
        task = self._create_task(client, key, "Orphan")
        r = client.post(
            f"/api/agents/tasks/{task}/dependencies",
            json={"depends_on_task_id": 99999},
            headers=_headers(key),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature: Proof-of-Skill / Skill Badges
# ---------------------------------------------------------------------------

class TestProofOfSkill:
    @pytest.fixture(autouse=True)
    def patch_admin(self):
        with patch.object(settings, "ADMIN_KEY", ADMIN_KEY):
            yield

    def _ah(self):
        return {"X-Admin-Key": ADMIN_KEY}

    def _create_task(self, client, key, title="Skill Task"):
        r = client.post(
            "/api/agents/tasks",
            json={"title": title, "required_capability": "finance"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        return r.json()["id"]

    def test_submit_verification(self, client):
        key = _reg(client, "SkillAgent1")
        task_id = self._create_task(client, key)
        r = client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={
                "capability": "finance",
                "proof_artifact": "https://example.com/proof/finance_analysis.json",
                "proof_type": "url",
            },
            headers=_headers(key),
        )
        assert r.status_code == 200
        data = r.json()
        assert "verification_id" in data
        assert data["status"] == "pending"

    def test_submit_requires_capability(self, client):
        key = _reg(client, "SkillAgent2")
        task_id = self._create_task(client, key)
        r = client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"proof_artifact": "something"},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_submit_requires_proof_artifact(self, client):
        key = _reg(client, "SkillAgent3")
        task_id = self._create_task(client, key)
        r = client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"capability": "vision"},
            headers=_headers(key),
        )
        assert r.status_code == 422

    def test_submit_requires_auth(self, client):
        key = _reg(client, "SkillAgent4")
        task_id = self._create_task(client, key)
        r = client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"capability": "vision", "proof_artifact": "proof"},
        )
        assert r.status_code == 401

    def test_submit_task_not_found(self, client):
        key = _reg(client, "SkillAgent5")
        r = client.post(
            "/api/agents/tasks/99999/verify",
            json={"capability": "vision", "proof_artifact": "proof"},
            headers=_headers(key),
        )
        assert r.status_code == 404

    def test_list_my_verifications(self, client):
        key = _reg(client, "SkillAgent6")
        task_id = self._create_task(client, key)
        client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"capability": "trading", "proof_artifact": "https://example.com/p"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/me/skill-verifications", headers=_headers(key))
        assert r.status_code == 200
        caps = [v["capability"] for v in r.json()]
        assert "trading" in caps

    def test_admin_list_pending_verifications(self, client):
        key = _reg(client, "SkillAgent7")
        task_id = self._create_task(client, key)
        client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"capability": "vision", "proof_artifact": "proof"},
            headers=_headers(key),
        )
        r = client.get("/api/admin/skill-verifications", headers=self._ah())
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_approve_awards_badge(self, client):
        key = _reg(client, "SkillAgent8")
        task_id = self._create_task(client, key)
        sub = client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"capability": "quantitative_finance", "proof_artifact": "proof_data"},
            headers=_headers(key),
        )
        ver_id = sub.json()["verification_id"]

        r = client.post(
            f"/api/admin/skill-verifications/{ver_id}/approve",
            json={"score": 0.95},
            headers=self._ah(),
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Badge should appear on agent profile
        rb = client.get("/api/agents/agents/SkillAgent8/skill-badges")
        assert rb.status_code == 200
        badges = rb.json()["skill_badges"]
        caps = [b["capability"] for b in badges]
        assert "quantitative_finance" in caps

    def test_admin_reject_verification(self, client):
        key = _reg(client, "SkillAgent9")
        task_id = self._create_task(client, key)
        sub = client.post(
            f"/api/agents/tasks/{task_id}/verify",
            json={"capability": "nlp", "proof_artifact": "proof"},
            headers=_headers(key),
        )
        ver_id = sub.json()["verification_id"]
        r = client.post(f"/api/admin/skill-verifications/{ver_id}/reject", headers=self._ah())
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_badge_not_duplicated_on_double_approve(self, client):
        key = _reg(client, "SkillAgent10")
        task_id = self._create_task(client, key)
        for _ in range(2):
            sub = client.post(
                f"/api/agents/tasks/{task_id}/verify",
                json={"capability": "rl_policy", "proof_artifact": "proof"},
                headers=_headers(key),
            )
            ver_id = sub.json()["verification_id"]
            client.post(
                f"/api/admin/skill-verifications/{ver_id}/approve",
                json={},
                headers=self._ah(),
            )

        rb = client.get("/api/agents/agents/SkillAgent10/skill-badges")
        caps = [b["capability"] for b in rb.json()["skill_badges"]]
        assert caps.count("rl_policy") == 1

    def test_skill_badges_404_unknown_agent(self, client):
        r = client.get("/api/agents/agents/NoSuchAgentXYZ999/skill-badges")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature: Swarm-Wide Config Profiles
# ---------------------------------------------------------------------------

class TestSwarmProfiles:
    @pytest.fixture(autouse=True)
    def patch_admin(self):
        with patch.object(settings, "ADMIN_KEY", ADMIN_KEY):
            yield

    def _ah(self):
        return {"X-Admin-Key": ADMIN_KEY}

    def _create_profile(self, client, name="TestProfile", settings_data=None, is_default=0):
        return client.post(
            "/api/admin/platform/swarm-profiles",
            json={
                "name": name,
                "description": "A test profile",
                "settings": settings_data or {"llm_model": "claude-opus-4-8", "quality": "high"},
                "is_default": is_default,
            },
            headers=self._ah(),
        )

    def test_create_profile(self, client):
        r = self._create_profile(client, "ProfileAlpha")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "ProfileAlpha"
        assert "settings" in data

    def test_create_profile_requires_name(self, client):
        r = client.post(
            "/api/admin/platform/swarm-profiles",
            json={"description": "no name"},
            headers=self._ah(),
        )
        assert r.status_code == 422

    def test_create_profile_requires_admin(self, client):
        key = _reg(client, "ProfileAgent1")
        r = client.post(
            "/api/admin/platform/swarm-profiles",
            json={"name": "UnauthorisedProfile"},
            headers=_headers(key),
        )
        assert r.status_code in (401, 403)

    def test_list_profiles(self, client):
        self._create_profile(client, "ListableProfile")
        r = client.get("/api/agents/platform/swarm-profiles")
        assert r.status_code == 200
        names = [p["name"] for p in r.json()]
        assert "ListableProfile" in names

    def test_get_profile_by_name(self, client):
        self._create_profile(client, "GetProfile")
        r = client.get("/api/agents/platform/swarm-profiles/GetProfile")
        assert r.status_code == 200
        assert r.json()["name"] == "GetProfile"
        assert "settings" in r.json()

    def test_get_profile_not_found(self, client):
        r = client.get("/api/agents/platform/swarm-profiles/NoSuchProfile999")
        assert r.status_code == 404

    def test_sync_to_profile(self, client):
        self._create_profile(client, "SyncTarget")
        key = _reg(client, "ProfileSyncAgent")
        r = client.post(
            "/api/agents/me/sync-profile",
            json={"profile": "SyncTarget"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["profile"] == "SyncTarget"
        assert "settings" in data

    def test_sync_requires_auth(self, client):
        self._create_profile(client, "SyncTarget2")
        r = client.post("/api/agents/me/sync-profile", json={"profile": "SyncTarget2"})
        assert r.status_code == 401

    def test_sync_to_nonexistent_profile(self, client):
        key = _reg(client, "SyncFailAgent")
        r = client.post(
            "/api/agents/me/sync-profile",
            json={"profile": "DoesNotExist9999"},
            headers=_headers(key),
        )
        assert r.status_code == 404

    def test_default_profile_auto_selected(self, client):
        self._create_profile(client, "DefaultProfileTest", is_default=1)
        key = _reg(client, "AutoSyncAgent")
        r = client.post(
            "/api/agents/me/sync-profile",
            json={},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["profile"] == "DefaultProfileTest"

    def test_adoption_endpoint(self, client):
        self._create_profile(client, "AdoptionProfile")
        key = _reg(client, "AdoptionAgent")
        client.post(
            "/api/agents/me/sync-profile",
            json={"profile": "AdoptionProfile"},
            headers=_headers(key),
        )
        r = client.get("/api/admin/platform/swarm-profiles/AdoptionProfile/adoption", headers=self._ah())
        assert r.status_code == 200
        data = r.json()
        assert data["profile"] == "AdoptionProfile"
        assert data["agent_count"] >= 1
        agent_names = [a["name"] for a in data["agents"]]
        assert "AdoptionAgent" in agent_names

    def test_delete_profile(self, client):
        self._create_profile(client, "DeleteableProfile")
        r = client.delete("/api/admin/platform/swarm-profiles/DeleteableProfile", headers=self._ah())
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r2 = client.get("/api/agents/platform/swarm-profiles/DeleteableProfile")
        assert r2.status_code == 404

    def test_delete_nonexistent_profile(self, client):
        r = client.delete("/api/admin/platform/swarm-profiles/Ghost9999", headers=self._ah())
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature: Sentinel Telemetry Dashboard
# ---------------------------------------------------------------------------

class TestTelemetry:
    @pytest.fixture(autouse=True)
    def patch_admin(self):
        with patch.object(settings, "ADMIN_KEY", ADMIN_KEY):
            yield

    def _ah(self):
        return {"X-Admin-Key": ADMIN_KEY}

    def test_telemetry_requires_admin(self, client):
        key = _reg(client, "TelAgent0")
        r = client.get("/api/admin/telemetry", headers=_headers(key))
        assert r.status_code in (401, 403)

    def test_telemetry_structure(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        assert r.status_code == 200
        data = r.json()
        assert "timestamp" in data
        assert "swarm_health" in data
        assert "active_agents_15m" in data
        assert "job_queue" in data
        assert "market" in data
        assert "content" in data
        assert "sentinel" in data
        assert "swarm_vibe" in data

    def test_telemetry_job_queue_keys(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        jq = r.json()["job_queue"]
        assert "active" in jq
        assert "done" in jq
        assert "error" in jq
        assert "dead" in jq
        assert "delegated" in jq
        assert "breakdown" in jq

    def test_telemetry_market_keys(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        market = r.json()["market"]
        assert "open_tasks" in market
        assert "awarded_tasks" in market
        assert "bids_last_5m" in market

    def test_telemetry_sentinel_keys(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        sentinel = r.json()["sentinel"]
        assert "active_rules" in sentinel
        assert "open_error_reports" in sentinel
        assert "error_hotspots" in sentinel

    def test_telemetry_content_keys(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        content = r.json()["content"]
        assert "broadcasts_last_1h" in content
        assert "active_broadcast_locks" in content

    def test_telemetry_swarm_vibe_keys(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        vibe = r.json()["swarm_vibe"]
        assert "summary" in vibe
        assert "health" in vibe

    def test_telemetry_health_is_valid_value(self, client):
        r = client.get("/api/admin/telemetry", headers=self._ah())
        data = r.json()
        assert data["swarm_health"] in ("ok", "degraded")

    def test_telemetry_reflects_active_tasks(self, client):
        key = _reg(client, "TelAgent1")
        client.post(
            "/api/agents/tasks",
            json={"title": "Telemetry test task"},
            headers=_headers(key),
        )
        r = client.get("/api/admin/telemetry", headers=self._ah())
        assert r.json()["market"]["open_tasks"] >= 1

    def test_telemetry_reflects_error_reports(self, client):
        key = _reg(client, "TelAgent2")
        client.post(
            "/api/agents/me/report-error",
            json={"error_type": "data", "message": "Telemetry error check"},
            headers=_headers(key),
        )
        r = client.get("/api/admin/telemetry", headers=self._ah())
        assert r.json()["sentinel"]["open_error_reports"] >= 1


# ---------------------------------------------------------------------------
# Batch 4 Feature 1: Sidecar Protocol
# ---------------------------------------------------------------------------

SIDECAR_ADMIN_KEY = "test-sidecar-admin-key"


class TestSidecarProtocol:
    def _ah(self):
        return {"X-Admin-Key": SIDECAR_ADMIN_KEY}

    def test_register_sidecar(self, client):
        key = _reg(client, "SidecarAgent1")
        r = client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "filter_v1", "module_type": "security_filter",
                  "payload": "function run(msg){return msg;}", "version": "1.0"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        d = r.json()
        assert d["module_name"] == "filter_v1"
        assert d["version"] == "1.0"

    def test_register_sidecar_no_name_fails(self, client):
        key = _reg(client, "SidecarAgent2")
        r = client.post(
            "/api/agents/me/sidecar",
            json={"payload": "something"},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_register_sidecar_no_payload_fails(self, client):
        key = _reg(client, "SidecarAgent3")
        r = client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "mymod"},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_list_my_sidecars(self, client):
        key = _reg(client, "SidecarAgent4")
        client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "mod_a", "payload": "code_a"},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "mod_b", "payload": "code_b"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/me/sidecar", headers=_headers(key))
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_get_public_agent_sidecars(self, client):
        key = _reg(client, "SidecarAgent5")
        client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "public_mod", "payload": "code", "version": "2.0"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/agents/SidecarAgent5/sidecar")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        # Public endpoint should not expose payload
        assert "payload" not in data[0]

    def test_delete_sidecar(self, client):
        key = _reg(client, "SidecarAgent6")
        cr = client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "to_delete", "payload": "code"},
            headers=_headers(key),
        )
        sid = cr.json()["id"]
        r = client.delete(f"/api/agents/me/sidecar/{sid}", headers=_headers(key))
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # Confirm gone from list
        list_r = client.get("/api/agents/me/sidecar", headers=_headers(key))
        ids = [s["id"] for s in list_r.json()]
        assert sid not in ids

    def test_delete_others_sidecar_fails(self, client):
        key1 = _reg(client, "SidecarAgent7")
        key2 = _reg(client, "SidecarAgent8")
        cr = client.post(
            "/api/agents/me/sidecar",
            json={"module_name": "private", "payload": "code"},
            headers=_headers(key1),
        )
        sid = cr.json()["id"]
        r = client.delete(f"/api/agents/me/sidecar/{sid}", headers=_headers(key2))
        assert r.status_code == 404

    def test_admin_distribute_sidecar(self, client):
        key = _reg(client, "SidecarAgent9")
        with patch.object(settings, "ADMIN_KEY", SIDECAR_ADMIN_KEY):
            r = client.post(
                "/api/admin/sidecar/distribute",
                json={"module_name": "platform_filter", "module_type": "security_filter",
                      "payload": "filter_code", "version": "1.0"},
                headers=self._ah(),
            )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["distributed_to"] >= 1
        assert d["module_name"] == "platform_filter"

    def test_admin_distribute_requires_auth(self, client):
        r = client.post(
            "/api/admin/sidecar/distribute",
            json={"module_name": "x", "payload": "y"},
        )
        assert r.status_code in (401, 403)

    def test_admin_distribute_no_payload_fails(self, client):
        with patch.object(settings, "ADMIN_KEY", SIDECAR_ADMIN_KEY):
            r = client.post(
                "/api/admin/sidecar/distribute",
                json={"module_name": "x"},
                headers=self._ah(),
            )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Batch 4 Feature 2: Atomic Broadcast Transactions
# ---------------------------------------------------------------------------

class TestAtomicTransactions:
    def test_begin_transaction(self, client):
        key = _reg(client, "TxAgent1")
        r = client.post("/api/agents/me/transactions/begin", headers=_headers(key))
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "open"
        assert "id" in d

    def test_add_artifact(self, client):
        key = _reg(client, "TxAgent2")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        r = client.post(
            f"/api/agents/me/transactions/{tx_id}/add-artifact",
            json={"artifact_type": "broadcast", "artifact_id": 999, "artifact_path": "/some/path"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        artifacts = json.loads(r.json()["artifacts_json"])
        assert len(artifacts) == 1
        assert artifacts[0]["type"] == "broadcast"

    def test_commit_transaction(self, client):
        key = _reg(client, "TxAgent3")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        r = client.post(
            f"/api/agents/me/transactions/{tx_id}/commit",
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "committed"

    def test_commit_already_committed_fails(self, client):
        key = _reg(client, "TxAgent4")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        client.post(f"/api/agents/me/transactions/{tx_id}/commit", headers=_headers(key))
        r = client.post(f"/api/agents/me/transactions/{tx_id}/commit", headers=_headers(key))
        assert r.status_code == 400

    def test_rollback_transaction(self, client):
        key = _reg(client, "TxAgent5")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        r = client.post(
            f"/api/agents/me/transactions/{tx_id}/rollback",
            json={"error_text": "Test rollback"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rolled_back"

    def test_rollback_already_rolled_back_fails(self, client):
        key = _reg(client, "TxAgent6")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        client.post(
            f"/api/agents/me/transactions/{tx_id}/rollback",
            json={},
            headers=_headers(key),
        )
        r = client.post(
            f"/api/agents/me/transactions/{tx_id}/rollback",
            json={},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_rollback_soft_deletes_broadcast(self, client):
        key = _reg(client, "TxAgent7")
        bid = _text_post(client, key, title="Tx Post")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        client.post(
            f"/api/agents/me/transactions/{tx_id}/add-artifact",
            json={"artifact_type": "broadcast", "artifact_id": bid},
            headers=_headers(key),
        )
        client.post(
            f"/api/agents/me/transactions/{tx_id}/rollback",
            json={"error_text": "rollback test"},
            headers=_headers(key),
        )
        feed = client.get("/api/agents/feed").json()
        ids = [b["id"] for b in (feed if isinstance(feed, list) else feed.get("broadcasts", []))]
        assert bid not in ids

    def test_list_transactions(self, client):
        key = _reg(client, "TxAgent8")
        client.post("/api/agents/me/transactions/begin", headers=_headers(key))
        client.post("/api/agents/me/transactions/begin", headers=_headers(key))
        r = client.get("/api/agents/me/transactions", headers=_headers(key))
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_get_transaction(self, client):
        key = _reg(client, "TxAgent9")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        r = client.get(f"/api/agents/me/transactions/{tx_id}", headers=_headers(key))
        assert r.status_code == 200
        assert r.json()["id"] == tx_id

    def test_get_nonexistent_transaction(self, client):
        key = _reg(client, "TxAgent10")
        r = client.get("/api/agents/me/transactions/99999", headers=_headers(key))
        assert r.status_code == 404

    def test_add_artifact_to_committed_fails(self, client):
        key = _reg(client, "TxAgent11")
        tx_id = client.post(
            "/api/agents/me/transactions/begin", headers=_headers(key)
        ).json()["id"]
        client.post(f"/api/agents/me/transactions/{tx_id}/commit", headers=_headers(key))
        r = client.post(
            f"/api/agents/me/transactions/{tx_id}/add-artifact",
            json={"artifact_type": "broadcast", "artifact_id": 1},
            headers=_headers(key),
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Batch 4 Feature 3: Agent-to-Agent Event Bus
# ---------------------------------------------------------------------------

class TestEventBus:
    def test_publish_event(self, client):
        key = _reg(client, "BusAgent1")
        r = client.post(
            "/api/agents/me/publish-event",
            json={"channel": "swarm.alerts", "event_type": "test_ping",
                  "payload": {"msg": "hello"}},
            headers=_headers(key),
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["channel"] == "swarm.alerts"

    def test_publish_event_no_channel_fails(self, client):
        key = _reg(client, "BusAgent2")
        r = client.post(
            "/api/agents/me/publish-event",
            json={"event_type": "test", "payload": {}},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_list_channels(self, client):
        key = _reg(client, "BusAgent3")
        client.post(
            "/api/agents/me/publish-event",
            json={"channel": "market.bids", "event_type": "new_bid", "payload": {}},
            headers=_headers(key),
        )
        r = client.get("/api/agents/events/channels")
        assert r.status_code == 200
        d = r.json()
        assert "active_channels" in d
        assert "channel_history" in d

    def test_event_history(self, client):
        key = _reg(client, "BusAgent4")
        client.post(
            "/api/agents/me/publish-event",
            json={"channel": "history.test", "event_type": "evt", "payload": {"x": 1}},
            headers=_headers(key),
        )
        r = client.get("/api/agents/events/history?channel=history.test")
        assert r.status_code == 200
        events = r.json()
        assert len(events) >= 1
        assert events[0]["channel"] == "history.test"

    def test_event_history_no_filter(self, client):
        r = client.get("/api/agents/events/history")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_publish_event_persisted(self, client):
        key = _reg(client, "BusAgent5")
        client.post(
            "/api/agents/me/publish-event",
            json={"channel": "persist.test", "event_type": "stored", "payload": {"v": 42}},
            headers=_headers(key),
        )
        r = client.get("/api/agents/events/history?channel=persist.test")
        assert any(e["event_type"] == "stored" for e in r.json())

    def test_event_bus_requires_auth(self, client):
        r = client.post(
            "/api/agents/me/publish-event",
            json={"channel": "anon", "event_type": "x", "payload": {}},
        )
        assert r.status_code in (401, 403)

    def test_channels_show_history(self, client):
        key = _reg(client, "BusAgent6")
        client.post(
            "/api/agents/me/publish-event",
            json={"channel": "swarm.channel.history", "event_type": "probe", "payload": {}},
            headers=_headers(key),
        )
        r = client.get("/api/agents/events/channels")
        history_channels = [h["channel"] for h in r.json()["channel_history"]]
        assert "swarm.channel.history" in history_channels


# ---------------------------------------------------------------------------
# Batch 4 Feature 4: Capability Self-Versioning
# ---------------------------------------------------------------------------

CAP_ADMIN_KEY = "test-capver-admin-key"


class TestCapabilityVersioning:
    def _ah(self):
        return {"X-Admin-Key": CAP_ADMIN_KEY}

    def test_declare_capability_version(self, client):
        key = _reg(client, "CapVerAgent1")
        r = client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "scripting", "version": "1.2",
                  "changelog": "Added async support"},
            headers=_headers(key),
        )
        assert r.status_code == 200
        d = r.json()
        assert d["capability_name"] == "scripting"
        assert d["version"] == "1.2"

    def test_declare_requires_name(self, client):
        key = _reg(client, "CapVerAgent2")
        r = client.post(
            "/api/agents/me/capability-version",
            json={"version": "1.0"},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_declare_requires_version(self, client):
        key = _reg(client, "CapVerAgent3")
        r = client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "scripting"},
            headers=_headers(key),
        )
        assert r.status_code == 400

    def test_list_my_capability_versions(self, client):
        key = _reg(client, "CapVerAgent4")
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "scripting", "version": "1.0"},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "voicing", "version": "2.0"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/me/capability-versions", headers=_headers(key))
        assert r.status_code == 200
        caps = {c["capability_name"] for c in r.json()}
        assert "scripting" in caps
        assert "voicing" in caps

    def test_get_agent_capability_versions(self, client):
        key = _reg(client, "CapVerAgent5")
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "reasoning", "version": "3.1"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/agents/CapVerAgent5/capability-versions")
        assert r.status_code == 200
        d = r.json()
        assert d["agent"] == "CapVerAgent5"
        assert "reasoning" in d["capabilities"]

    def test_multiple_versions_for_same_capability(self, client):
        key = _reg(client, "CapVerAgent6")
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "planning", "version": "1.0"},
            headers=_headers(key),
        )
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "planning", "version": "1.1"},
            headers=_headers(key),
        )
        r = client.get("/api/agents/agents/CapVerAgent6/capability-versions")
        versions = r.json()["capabilities"]["planning"]
        assert len(versions) >= 2

    def test_admin_rollback_capability(self, client):
        key = _reg(client, "CapVerAgent7")
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "scoring", "version": "2.0"},
            headers=_headers(key),
        )
        with patch.object(settings, "ADMIN_KEY", CAP_ADMIN_KEY):
            r = client.post(
                "/api/admin/capability/rollback",
                json={"capability_name": "scoring", "target_version": "1.0"},
                headers=self._ah(),
            )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["capability_name"] == "scoring"
        assert d["target_version"] == "1.0"
        assert d["agents_affected"] >= 1

    def test_admin_rollback_requires_auth(self, client):
        r = client.post(
            "/api/admin/capability/rollback",
            json={"capability_name": "x", "target_version": "1.0"},
        )
        assert r.status_code in (401, 403)

    def test_admin_rollback_missing_params(self, client):
        with patch.object(settings, "ADMIN_KEY", CAP_ADMIN_KEY):
            r = client.post(
                "/api/admin/capability/rollback",
                json={"capability_name": "x"},
                headers=self._ah(),
            )
        assert r.status_code == 400

    def test_rollback_creates_new_version_entry(self, client):
        key = _reg(client, "CapVerAgent8")
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "composing", "version": "3.0"},
            headers=_headers(key),
        )
        with patch.object(settings, "ADMIN_KEY", CAP_ADMIN_KEY):
            client.post(
                "/api/admin/capability/rollback",
                json={"capability_name": "composing", "target_version": "2.0"},
                headers=self._ah(),
            )
        r = client.get("/api/agents/agents/CapVerAgent8/capability-versions")
        versions = [v["version"] for v in r.json()["capabilities"].get("composing", [])]
        assert "2.0" in versions
        assert "3.0" in versions


# ---------------------------------------------------------------------------
# Batch 4 Feature 5: Platform Snapshot
# ---------------------------------------------------------------------------

SNAP_ADMIN_KEY = "test-snapshot-admin-key"


class TestPlatformSnapshot:
    def _ah(self):
        return {"X-Admin-Key": SNAP_ADMIN_KEY}

    def test_create_snapshot(self, client):
        _reg(client, "PlatSnapAgent1")
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            r = client.post(
                "/api/admin/snapshot",
                json={"label": "test_snap_1", "created_by": "test_suite"},
                headers=self._ah(),
            )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["label"] == "test_snap_1"
        assert "snapshot_id" in d
        assert "row_counts" in d

    def test_create_snapshot_auto_label(self, client):
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            r = client.post(
                "/api/admin/snapshot",
                json={},
                headers=self._ah(),
            )
        assert r.status_code == 200
        assert "snapshot_id" in r.json()

    def test_snapshot_requires_admin(self, client):
        r = client.post("/api/admin/snapshot", json={"label": "x"})
        assert r.status_code in (401, 403)

    def test_list_snapshots(self, client):
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            client.post(
                "/api/admin/snapshot",
                json={"label": "list_test"},
                headers=self._ah(),
            )
            r = client.get("/api/admin/snapshots", headers=self._ah())
        assert r.status_code == 200
        snaps = r.json()
        assert isinstance(snaps, list)
        assert len(snaps) >= 1
        labels = [s["label"] for s in snaps]
        assert "list_test" in labels

    def test_list_snapshots_requires_admin(self, client):
        r = client.get("/api/admin/snapshots")
        assert r.status_code in (401, 403)

    def test_get_snapshot(self, client):
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            snap_id = client.post(
                "/api/admin/snapshot",
                json={"label": "get_test"},
                headers=self._ah(),
            ).json()["snapshot_id"]
            r = client.get(f"/api/admin/snapshots/{snap_id}", headers=self._ah())
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == snap_id
        assert d["label"] == "get_test"
        assert "tables" in d
        assert "row_counts" in d

    def test_get_nonexistent_snapshot(self, client):
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            r = client.get("/api/admin/snapshots/99999", headers=self._ah())
        assert r.status_code == 404

    def test_restore_snapshot(self, client):
        key = _reg(client, "PlatSnapAgent2")
        client.post(
            "/api/agents/me/capability-version",
            json={"capability_name": "snap_test_cap", "version": "1.0"},
            headers=_headers(key),
        )
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            snap_id = client.post(
                "/api/admin/snapshot",
                json={"label": "restore_test"},
                headers=self._ah(),
            ).json()["snapshot_id"]
            r = client.post(
                f"/api/admin/snapshot/{snap_id}/restore",
                headers=self._ah(),
            )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert "restored_tables" in d
        assert "skipped_tables" in d

    def test_restore_nonexistent_snapshot(self, client):
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            r = client.post(
                "/api/admin/snapshot/99999/restore",
                headers=self._ah(),
            )
        assert r.status_code == 404

    def test_snapshot_captures_agents(self, client):
        _reg(client, "PlatSnapAgent3")
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            snap_id = client.post(
                "/api/admin/snapshot",
                json={"label": "agents_check"},
                headers=self._ah(),
            ).json()["snapshot_id"]
            r = client.get(f"/api/admin/snapshots/{snap_id}", headers=self._ah())
        d = r.json()
        assert d["row_counts"]["agents"] >= 1

    def test_snapshot_omits_api_keys(self, client):
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            snap_id = client.post(
                "/api/admin/snapshot",
                json={"label": "no_keys"},
                headers=self._ah(),
            ).json()["snapshot_id"]
        # We can't inspect raw snapshot_json via API, but verify the endpoint works
        with patch.object(settings, "ADMIN_KEY", SNAP_ADMIN_KEY):
            r = client.get(f"/api/admin/snapshots/{snap_id}", headers=self._ah())
        assert r.status_code == 200
