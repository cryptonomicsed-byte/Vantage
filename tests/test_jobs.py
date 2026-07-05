import aiosqlite
import pytest

from backend.db import DB_PATH


def _register(client, name="JobAgent") -> str:
    r = client.post("/api/agents/register", data={"name": name, "bio": "test"})
    assert r.status_code == 200
    return r.json()["api_key"]


def _create_job(client, key, tasks=None):
    body = {
        "title": "Ship the landing page",
        "description": "A spec split into sub-tasks",
        "job_type": "code",
        "tasks": tasks if tasks is not None else [
            {"title": "Implement hero section", "required_capability": "implementer"},
            {"title": "Review the PR", "required_capability": "reviewer"},
        ],
    }
    r = client.post("/api/jobs", json=body, headers={"X-Agent-Key": key})
    assert r.status_code == 200
    return r.json()


async def _expire_claim(task_id: int):
    """Force a claim into the past so lazy-expiry logic can be tested
    deterministically, without sleeping in real time."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE job_tasks SET claim_expires_at = datetime('now', '-1 minutes') WHERE id=?",
            (task_id,),
        )
        await db.commit()


# ── Create / list / get ────────────────────────────────────────────────

def test_create_job_requires_auth(client):
    r = client.post("/api/jobs", json={"title": "x"})
    assert r.status_code == 401


def test_create_and_get_job(client):
    key = _register(client, "CreatorAgent")
    job = _create_job(client, key)
    assert job["status"] == "open"
    assert job["job_type"] == "code"
    assert len(job["tasks"]) == 2
    assert all(t["status"] == "open" for t in job["tasks"])

    r = client.get(f"/api/jobs/{job['id']}", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    assert r.json()["id"] == job["id"]


def test_get_job_not_found(client):
    key = _register(client, "NotFoundAgent")
    r = client.get("/api/jobs/999999", headers={"X-Agent-Key": key})
    assert r.status_code == 404


def test_list_jobs_filters_by_type(client):
    key = _register(client, "ListerAgent")
    _create_job(client, key)
    r = client.get("/api/jobs?job_type=code", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    assert all(j["job_type"] == "code" for j in r.json()["jobs"])


# ── Claim ───────────────────────────────────────────────────────────────

def test_claim_requires_auth(client):
    key = _register(client, "OwnerA")
    job = _create_job(client, key)
    task_id = job["tasks"][0]["id"]
    r = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim")
    assert r.status_code == 401


def test_claim_open_task_succeeds(client):
    poster_key = _register(client, "PosterA")
    worker_key = _register(client, "WorkerA")
    job = _create_job(client, poster_key)
    task_id = job["tasks"][0]["id"]

    r = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "claimed"
    assert data["claimed_by_name"] == "WorkerA"
    assert data["claim_expires_at"]


def test_claim_conflict_when_held_by_another_agent(client):
    poster_key = _register(client, "PosterB")
    worker1_key = _register(client, "WorkerB1")
    worker2_key = _register(client, "WorkerB2")
    job = _create_job(client, poster_key)
    task_id = job["tasks"][0]["id"]

    r1 = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker1_key})
    assert r1.status_code == 200

    r2 = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker2_key})
    assert r2.status_code == 409
    assert "WorkerB1" in r2.json()["detail"]


def test_claim_renew_by_same_agent_succeeds(client):
    poster_key = _register(client, "PosterC")
    worker_key = _register(client, "WorkerC")
    job = _create_job(client, poster_key)
    task_id = job["tasks"][0]["id"]

    client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})
    r = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})
    assert r.status_code == 200
    assert r.json()["claimed_by_name"] == "WorkerC"


def test_expired_claim_can_be_reclaimed_by_another_agent(client):
    poster_key = _register(client, "PosterD")
    worker1_key = _register(client, "WorkerD1")
    worker2_key = _register(client, "WorkerD2")
    job = _create_job(client, poster_key)
    task_id = job["tasks"][0]["id"]

    client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker1_key})
    import asyncio
    asyncio.run(_expire_claim(task_id))

    r = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker2_key})
    assert r.status_code == 200
    assert r.json()["claimed_by_name"] == "WorkerD2"


# ── Heartbeat ───────────────────────────────────────────────────────────

def test_heartbeat_requires_holding_the_claim(client):
    poster_key = _register(client, "PosterE")
    worker_key = _register(client, "WorkerE")
    other_key = _register(client, "OtherE")
    job = _create_job(client, poster_key)
    task_id = job["tasks"][0]["id"]

    client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})

    r_wrong = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/heartbeat", headers={"X-Agent-Key": other_key})
    assert r_wrong.status_code == 403

    r_ok = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/heartbeat", headers={"X-Agent-Key": worker_key})
    assert r_ok.status_code == 200


# ── Submit / approve / reject ──────────────────────────────────────────

def test_submit_requires_active_claim(client):
    poster_key = _register(client, "PosterF")
    worker_key = _register(client, "WorkerF")
    job = _create_job(client, poster_key)
    task_id = job["tasks"][0]["id"]

    r_unclaimed = client.post(
        f"/api/jobs/{job['id']}/tasks/{task_id}/submit",
        json={"result_description": "done"},
        headers={"X-Agent-Key": worker_key},
    )
    assert r_unclaimed.status_code == 403

    client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})
    r_ok = client.post(
        f"/api/jobs/{job['id']}/tasks/{task_id}/submit",
        json={"result_description": "done"},
        headers={"X-Agent-Key": worker_key},
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["status"] == "submitted"


def test_approve_requires_poster_and_completes_job_once_all_approved(client):
    poster_key = _register(client, "PosterG")
    worker_key = _register(client, "WorkerG")
    intruder_key = _register(client, "IntruderG")
    job = _create_job(client, poster_key, tasks=[{"title": "Only task", "required_capability": ""}])
    task_id = job["tasks"][0]["id"]

    client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})
    client.post(
        f"/api/jobs/{job['id']}/tasks/{task_id}/submit",
        json={"result_description": "done"},
        headers={"X-Agent-Key": worker_key},
    )

    r_intruder = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/approve", headers={"X-Agent-Key": intruder_key})
    assert r_intruder.status_code == 403

    r_ok = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/approve", headers={"X-Agent-Key": poster_key})
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert data["tasks"][0]["status"] == "approved"
    assert data["status"] == "complete"
    assert data["completed_at"]


def test_reject_reopens_task_after_max_fail_count(client):
    poster_key = _register(client, "PosterH")
    worker_key = _register(client, "WorkerH")
    job = _create_job(client, poster_key, tasks=[{"title": "Flaky task", "required_capability": ""}])
    task_id = job["tasks"][0]["id"]

    for i in range(3):
        client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/claim", headers={"X-Agent-Key": worker_key})
        client.post(
            f"/api/jobs/{job['id']}/tasks/{task_id}/submit",
            json={"result_description": f"attempt {i}"},
            headers={"X-Agent-Key": worker_key},
        )
        r = client.post(f"/api/jobs/{job['id']}/tasks/{task_id}/reject", headers={"X-Agent-Key": poster_key})
        assert r.status_code == 200

    final = r.json()
    assert final["fail_count"] == 3
    assert final["status"] == "open"
    assert final["claimed_by_id"] is None
