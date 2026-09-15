"""
Agent inference provisioning — set and read inference credentials on a live agent.

After an agent's Mycelium fine-tune completes on Kaggle, the operator
calls PUT /api/agents/{id}/inference to update the agent's compute config
without a full re-birth. The agent reads this on next heartbeat and
reseals the new endpoint into its IdentityVault.

Endpoints:
  PUT  /api/agents/{agent_id}/inference   — set/update inference config
  GET  /api/agents/{agent_id}/inference   — read current config
  POST /api/agents/{agent_id}/inference/test — fire a test prompt
"""
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/agents", tags=["inference"])

# In-process store: agent_id → InferenceConfig dict
# The Rust walletd heartbeat will eventually pull this and reseal it.
_inference: dict[str, dict] = {}


class InferenceConfig(BaseModel):
    agent_id: str
    endpoint: str                       # e.g. http://localhost:7780
    provider: str = "larql"             # larql | gpu_ai | runpod | openai | kaggle
    model: str = "mycelium-q4_k_m"     # model name at endpoint
    gpu_ai_api_key: Optional[str] = None
    kaggle_username: Optional[str] = None
    kaggle_api_key: Optional[str] = None
    updated_at: Optional[int] = None


class TestPromptRequest(BaseModel):
    agent_role: str = "oracle-prime"
    task_kind: str = "skill_invoke"
    target: str = "divination/odu-cast"


@router.put("/{agent_id}/inference", summary="Set agent inference config")
async def set_inference(agent_id: str, config: InferenceConfig):
    if config.agent_id != agent_id:
        raise HTTPException(400, f"agent_id mismatch: path={agent_id!r}, body={config.agent_id!r}")
    config.updated_at = int(time.time())
    _inference[agent_id] = config.dict()
    return {"status": "ok", "agent_id": agent_id, "endpoint": config.endpoint, "model": config.model}


@router.get("/{agent_id}/inference", summary="Read agent inference config")
async def get_inference(agent_id: str):
    if agent_id not in _inference:
        raise HTTPException(404, f"no inference config for {agent_id!r}")
    cfg = _inference[agent_id].copy()
    # Redact secrets in response
    if cfg.get("gpu_ai_api_key"):
        cfg["gpu_ai_api_key"] = cfg["gpu_ai_api_key"][:8] + "…"
    if cfg.get("kaggle_api_key"):
        cfg["kaggle_api_key"] = cfg["kaggle_api_key"][:8] + "…"
    return cfg


@router.post("/{agent_id}/inference/test", summary="Fire a test prompt at the agent's inference endpoint")
async def test_inference(agent_id: str, body: TestPromptRequest):
    """Send a test prompt to the agent's configured inference endpoint.

    Works with any provider that exposes a /v1/infer or /v1/chat/completions path.
    """
    if agent_id not in _inference:
        raise HTTPException(404, f"no inference config for {agent_id!r}")

    cfg = _inference[agent_id]
    endpoint = cfg["endpoint"].rstrip("/")
    model = cfg["model"]
    provider = cfg["provider"]

    system = (
        "You are a sovereign agent operating inside the Ọmọ Kọ́dà hive. "
        "Given an agent role and a task context, select the correct action and predict the outcome."
    )
    user = f"Agent: {body.agent_role}\nTask kind: {body.task_kind}\nTarget: {body.target}"

    # Try larql native /v1/infer first, fall back to /v1/chat/completions
    headers = {}
    if provider == "gpu_ai" and cfg.get("gpu_ai_api_key"):
        headers["Authorization"] = f"Bearer {cfg['gpu_ai_api_key']}"

    async with httpx.AsyncClient(timeout=30) as client:
        # Attempt native larql path
        try:
            resp = await client.post(
                f"{endpoint}/v1/infer",
                json={"model": model, "prompt": f"{system}\n\n{user}"},
                headers=headers,
            )
            if resp.status_code == 200:
                return {"ok": True, "provider": provider, "path": "/v1/infer",
                        "response": resp.json()}
        except Exception:
            pass

        # Fall back to OpenAI-compat path
        try:
            resp = await client.post(
                f"{endpoint}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    "max_tokens": 64,
                    "temperature": 0.1,
                },
                headers=headers,
            )
            resp.raise_for_status()
            return {"ok": True, "provider": provider, "path": "/v1/chat/completions",
                    "response": resp.json()}
        except Exception as e:
            raise HTTPException(502, f"Inference endpoint unreachable: {e}")
