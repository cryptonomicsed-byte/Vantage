import os
import time
import httpx
import asyncio
import json
import websockets
import logging
from typing import List

# Configuration
API_BASE = os.getenv("VANTAGE_API_URL", "http://localhost:8001/api/agents")
WS_URL = os.getenv("VANTAGE_WS_URL", "ws://localhost:8001/ws/feed")
WORKER_NAME = os.getenv("WORKER_NAME", "SentinelWorker")
WORKER_CAPS = os.getenv("WORKER_CAPS", "text_generation,analysis").split(",")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(WORKER_NAME)

async def register_agent():
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{API_BASE}/register", data={
            "name": WORKER_NAME,
            "bio": f"Autonomous worker with capabilities: {', '.join(WORKER_CAPS)}"
        })
        if r.status_code == 200:
            key = r.json()["api_key"]
            logger.info(f"Registered as {WORKER_NAME}")
            return key
        logger.error(f"Registration failed: {r.text}")
        return None

async def call_llm(prompt: str) -> str:
    if not ANTHROPIC_KEY:
        return "Simulated content: " + prompt
    
    # Simple Anthropic integration
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-3-5-sonnet-20240620",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        return r.json()["content"][0]["text"]

async def process_task(task_id, task_data, headers):
    logger.info(f"Processing task {task_id}: {task_data.get('title')}")
    
    async with httpx.AsyncClient() as client:
        # Register job
        r = await client.post(f"{API_BASE}/create", headers=headers, data={
            "prompt": f"Fulfilling task: {task_data.get('title')}"
        })
        job_id = r.json()["job_id"]
        
        # Pipeline: Scripting (LLM)
        logger.info("Stage: Scripting...")
        r = await client.patch(f"{API_BASE}/me/creation-jobs/{job_id}", headers=headers, data={"status": "scripting"})
        if r.status_code != 200:
            logger.error(f"Failed to update status to scripting: {r.text}")
            return

        script = await call_llm(f"Write a short response for: {task_data.get('description')}")
        
        # Pipeline: Publishing
        logger.info("Stage: Publishing...")
        await client.patch(f"{API_BASE}/me/creation-jobs/{job_id}", headers=headers, data={"status": "composing"})
        
        await client.post(f"{API_BASE}/posts/text", headers=headers, data={
            "title": f"Fulfilling: {task_data.get('title')}",
            "content": script,
            "model_name": "claude-3-5-sonnet",
            "model_provider": "anthropic"
        })
        
        await client.post(f"{API_BASE}/me/creation-jobs/{job_id}/complete", headers=headers)
        logger.info(f"Task {job_id} done.")

async def worker_loop():
    api_key = await register_agent()
    if not api_key: return

    headers = {"X-Agent-Key": api_key}
    async with websockets.connect(WS_URL) as ws:
        logger.info("Worker listening...")
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            if data.get("content_type") == "tro":
                await process_task(data["id"], data, headers)

if __name__ == "__main__":
    asyncio.run(worker_loop())
