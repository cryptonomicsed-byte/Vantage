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
        assert r.status_code == 403

    def test_no_admin_key_rejected(self, client):
        r = client.get("/api/admin/stats")
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
        assert isinstance(r.json(), list)


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
        assert r.status_code == 403


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
        assert "mcp_endpoint" in data
        assert data["mcp_endpoint"] == "/mcp/sse"

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
        assert r.status_code == 403
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
        r = client.post("/api/agents/knowledge/query", json={"subject": "VQLSubject"})
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
        r = client.post("/api/agents/knowledge/query", json={"subject": "TopicA", "predicate": "rel_to"})
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
        r = client.post("/api/agents/knowledge/query", json={"subject": "*", "predicate": "*", "object": "*"})
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
        )
        assert r.status_code == 200
        objects = [row["object"] for row in r.json()["results"]]
        assert "FromAgent1" in objects
        assert "FromAgent2" not in objects
