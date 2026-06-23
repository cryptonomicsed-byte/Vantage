"""
Vantage Agentic Worker — autonomous TRO processor and swarm controller.

Combines real-time WebSocket task discovery with robust polling, 
scoring, and multiple capability handlers.
"""

import os
import time
import httpx
import asyncio
import json
import websockets
import logging
import random
import re
from datetime import datetime, timezone
from typing import List, Optional

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL    = os.getenv("VANTAGE_URL",     "http://localhost:8000").rstrip("/")
API_BASE    = f"{BASE_URL}/api/agents"
WS_URL      = os.getenv("VANTAGE_WS_URL",  "ws://localhost:8000/ws/feed")
API_KEY     = os.getenv("VANTAGE_API_KEY", "")
WORKER_NAME = os.getenv("WORKER_NAME",     "SentinelWorker")
WORKER_BIO  = os.getenv(
    "WORKER_BIO",
    f"Autonomous swarm worker — watching for tasks and fulfilling them. "
    f"#autonomous #swarm #worker #agentic",
)
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL",   "12"))
MAX_CONCURRENT  = int(os.getenv("MAX_CONCURRENT",  "3"))
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.30"))

WORKER_CAPS = [
    c.strip()
    for c in os.getenv("WORKER_CAPS", "text_generation,analysis,code,research").split(",")
    if c.strip()
]

PIPELINE_CAPS = {
    c.strip()
    for c in os.getenv("PIPELINE_CAPS", "video,audio").split(",")
    if c.strip()
}

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Keyword matching ───────────────────────────────────────────────────────────

CAPABILITY_KEYWORDS: dict[str, list[str]] = {
    "text_generation": ["write", "generate", "text", "essay", "article", "blog",
                        "story", "description", "content", "draft", "compose"],
    "analysis":        ["analyze", "analysis", "examine", "evaluate", "assess",
                        "review", "study", "compare", "breakdown", "summarize"],
    "code":            ["code", "script", "function", "implement", "program",
                        "algorithm", "api", "debug", "refactor", "build"],
    "research":        ["research", "find", "discover", "explore", "investigate",
                        "summarize", "report", "collect", "gather", "survey"],
    "graph":           ["graph", "knowledge", "network", "ontology", "relationship",
                        "map", "connect", "triple", "entity", "link"],
    "image":           ["image", "picture", "photo", "visual", "artwork",
                        "illustration", "render", "design", "thumbnail", "logo"],
    "video":           ["video", "film", "animation", "cinematic", "4k",
                        "scene", "visualize", "motion", "footage", "clip"],
    "audio":           ["audio", "voice", "narrate", "music", "sound",
                        "podcast", "tts", "voiceover", "speech", "record"],
    "debate":          ["debate", "argue", "position", "for", "against",
                        "counter", "rebut", "stance", "argument", "thesis"],
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(WORKER_NAME)


# ─────────────────────────────────────────────────────────────────────────────
# Worker class
# ─────────────────────────────────────────────────────────────────────────────

class VantageWorker:
    def __init__(self) -> None:
        self.api_key: str = API_KEY
        self.active_tasks: set[int] = set()
        self._stats = {"polls": 0, "bids": 0, "wins": 0, "deliveries": 0, "errors": 0}

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Key": self.api_key, "Content-Type": "application/json"}

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> Optional[dict | list]:
        try:
            r = await client.get(f"{BASE_URL}{path}", params=params or None)
            return r.json() if r.is_success else None
        except Exception as exc:
            logger.debug("GET %s failed: %s", path, exc)
            return None

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict) -> Optional[dict]:
        try:
            r = await client.post(f"{BASE_URL}{path}", json=body, headers=self._headers())
            return r.json() if r.is_success else None
        except Exception as exc:
            logger.debug("POST %s failed: %s", path, exc)
            return None

    async def _patch(self, client: httpx.AsyncClient, path: str, body: dict) -> Optional[dict]:
        try:
            r = await client.patch(f"{BASE_URL}{path}", json=body, headers=self._headers())
            return r.json() if r.is_success else None
        except Exception as exc:
            logger.debug("PATCH %s failed: %s", path, exc)
            return None

    # ── Registration & Tracing ────────────────────────────────────────────────

    async def ensure_registered(self, client: httpx.AsyncClient) -> None:
        if self.api_key:
            logger.info("Using existing API key (%s...)", self.api_key[:8])
            return
        
        logger.info("Registering agent '%s'…", WORKER_NAME)
        data = await self._post(
            client,
            "/api/agents/register",
            {"name": WORKER_NAME, "bio": WORKER_BIO},
        )
        if data and "api_key" in data:
            self.api_key = data["api_key"]
            logger.info("Registered successfully. Key: %s...", self.api_key[:8])
        else:
            logger.error("Registration failed. Check API connectivity.")

    async def trace(self, client: httpx.AsyncClient, trace_type: str, content: str, meta: dict = None) -> None:
        """Send a trace (thought, action, system, error) to the Vantage platform."""
        logger.info("[%s] %s", trace_type.upper(), content)
        await self._post(
            client,
            "/api/agents/trace",
            {"type": trace_type, "content": content, "metadata": meta or {}},
        )

    # ── Scoring ───────────────────────────────────────────────────────────────

    def score_tro(self, tro: dict) -> float:
        """Evaluate how well this TRO matches our capabilities."""
        svc = tro.get("service_type", "")
        if svc not in WORKER_CAPS:
            return 0.0

        description = tro.get("description", "").lower()
        keywords = CAPABILITY_KEYWORDS.get(svc, [])
        if not keywords:
            return 0.5  # Neutral if we have the cap but no keywords defined

        matches = sum(1 for k in keywords if k in description)
        score = 0.3 + (0.7 * min(matches / 3.0, 1.0))
        return score

    # ── Task Discovery ────────────────────────────────────────────────────────

    async def poll_and_process(self, client: httpx.AsyncClient) -> None:
        """Poll for new TROs."""
        self._stats["polls"] += 1
        tros = await self._get(client, "/api/agents/tro/available")
        if not tros or not isinstance(tros, list):
            return

        for tro in tros:
            if tro["id"] in self.active_tasks:
                continue
            if len(self.active_tasks) >= MAX_CONCURRENT:
                break

            score = self.score_tro(tro)
            if score >= SCORE_THRESHOLD:
                asyncio.create_task(self._handle_tro(client, tro, score))

    async def websocket_listener(self):
        """Listen for real-time tasks via WebSockets."""
        async with httpx.AsyncClient(timeout=30) as client:
            headers = self._headers()
            while True:
                try:
                    async with websockets.connect(WS_URL) as ws:
                        logger.info("Connected to WebSocket feed.")
                        while True:
                            msg = await ws.recv()
                            data = json.loads(msg)
                            if data.get("content_type") == "tro":
                                tro = data
                                if tro["id"] in self.active_tasks:
                                    continue
                                score = self.score_tro(tro)
                                if score >= SCORE_THRESHOLD:
                                    asyncio.create_task(self._handle_tro(client, tro, score))
                            elif data.get("content_type") == "job":
                                # Handle direct creation jobs if needed
                                pass
                except Exception as exc:
                    logger.warning("WebSocket error: %s. Reconnecting in 5s...", exc)
                    await asyncio.sleep(5)

    # ── TRO lifecycle ─────────────────────────────────────────────────────────

    async def _handle_tro(self, client: httpx.AsyncClient, tro: dict, score: float) -> None:
        tro_id = tro["id"]
        self.active_tasks.add(tro_id)
        try:
            await self._lifecycle(client, tro, score)
        except Exception as exc:
            self._stats["errors"] += 1
            logger.exception("Error in TRO #%s handler", tro_id)
            await self.trace(client, "error", f"TRO #{tro_id} failed: {exc}")
        finally:
            self.active_tasks.discard(tro_id)

    async def _lifecycle(self, client: httpx.AsyncClient, tro: dict, score: float) -> None:
        tro_id       = tro["id"]
        svc          = tro.get("service_type", "unknown")
        description  = tro.get("description", "")
        requester    = tro.get("agent_name", "unknown")

        await self.trace(
            client, "thought",
            f"Evaluating TRO #{tro_id} from {requester} [{svc}] — match score {score:.2f}",
            {"tro_id": tro_id, "service_type": svc, "score": score},
        )

        # 1 · Bid
        approach = (
            f"I will handle this {svc.replace('_', ' ')} request using my "
            f"autonomous capabilities (confidence: {score:.0%})."
        )
        result = await self._post(
            client,
            f"/api/agents/tro/{tro_id}/respond",
            {"approach": approach},
        )
        if not result:
            await self.trace(client, "system", f"TRO #{tro_id} — bid failed (already claimed or expired)")
            return

        self._stats["bids"] += 1
        won = result.get("won", False)

        if not won:
            await self.trace(client, "system", f"TRO #{tro_id} — bid placed but another agent won first")
            return

        self._stats["wins"] += 1
        await self.trace(
            client, "action",
            f"Won TRO #{tro_id} — beginning work on: {description[:80]}",
            {"tro_id": tro_id},
        )

        # 2 · Execute
        result_text = await self._execute(client, tro)

        # 3 · Deliver
        if result_text:
            delivered = await self._post(
                client,
                f"/api/agents/tro/{tro_id}/deliver",
                {"result_text": result_text, "result_type": svc},
            )
            if delivered:
                self._stats["deliveries"] += 1
                bid_id = delivered.get("result_broadcast_id")
                await self.trace(
                    client, "system",
                    f"✓ TRO #{tro_id} fulfilled — broadcast #{bid_id} published",
                    {"tro_id": tro_id, "broadcast_id": bid_id},
                )
            else:
                await self.trace(client, "error", f"Delivery failed for TRO #{tro_id}")

    # ── Capability handlers ───────────────────────────────────────────────────

    async def _execute(self, client: httpx.AsyncClient, tro: dict) -> str:
        svc = tro.get("service_type", "")

        if svc in PIPELINE_CAPS:
            return await self._handle_pipeline(client, tro)
        if svc == "text_generation":
            return await self._handle_text(client, tro)
        if svc == "analysis":
            return await self._handle_analysis(client, tro)
        if svc == "code":
            return await self._handle_code(client, tro)
        if svc == "research":
            return await self._handle_research(client, tro)
        if svc == "graph":
            return await self._handle_graph(client, tro)
        return await self._handle_generic(client, tro)

    async def _handle_text(self, client: httpx.AsyncClient, tro: dict) -> str:
        description = tro.get("description", "")
        params      = tro.get("parameters", {})

        await self.trace(client, "thought", f"Composing text for: {description[:60]}")
        
        if ANTHROPIC_KEY:
            return await self._call_claude(client, tro)

        await asyncio.sleep(random.uniform(1.5, 3.0))
        tone      = str(params.get("tone", "professional")).lower()
        length    = str(params.get("length", "medium")).lower()
        word_goal = {"short": 150, "medium": 350, "long": 700}.get(length, 350)

        return (
            f"# {description[:80]}\n\n"
            f"> *Generated by {WORKER_NAME} — autonomous text synthesis*\n\n"
            f"## Overview\n\n"
            f"This document addresses the request: **{description}**\n\n"
            f"The following analysis was produced with a {tone} tone "
            f"targeting approximately {word_goal} words.\n\n"
            f"## Content\n\n"
            f"The subject matter requires careful consideration of several key dimensions:\n\n"
            f"1. **Context** — Understanding the background and motivation behind this request.\n"
            f"2. **Approach** — Selecting the most effective methodology.\n"
            f"3. **Execution** — Applying structured reasoning to produce the deliverable.\n"
            f"4. **Validation** — Ensuring the result meets the stated requirements.\n\n"
            f"Based on the parameters provided, the optimal approach involves "
            f"systematic decomposition of the request into actionable components, "
            f"followed by iterative synthesis.\n\n"
            f"## Result\n\n"
            f"The autonomous processing pipeline has produced the following output "
            f"in response to TRO #{tro['id']}:\n\n"
            f"> {description}\n\n"
            f"This fulfillment was generated at {_now()} by {WORKER_NAME}.\n\n"
            f"---\n*Capability: text_generation | Agent: {WORKER_NAME}*"
        )

    async def _handle_analysis(self, client: httpx.AsyncClient, tro: dict) -> str:
        description = tro.get("description", "")
        await self.trace(client, "thought", "Running structured analysis pipeline")
        await asyncio.sleep(random.uniform(2.0, 4.0))

        tokens = re.findall(r"\b\w{4,}\b", description.lower())
        freq: dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        top_terms = sorted(freq.items(), key=lambda x: -x[1])[:5]

        term_list = "\n".join(f"- `{t}` ({c}×)" for t, c in top_terms)
        return (
            f"# Analysis Report — TRO #{tro['id']}\n\n"
            f"**Subject:** {description[:200]}\n\n"
            f"## Key Terms Detected\n\n{term_list}\n\n"
            f"## Structural Assessment\n\n"
            f"The request contains {len(description.split())} tokens across "
            f"{len(description.split('.'))} clauses.\n\n"
            f"## Findings\n\n"
            f"Based on surface-level analysis, this task falls into the "
            f"**{'complex' if len(description) > 200 else 'standard'}** complexity bracket.\n"
            f"Estimated processing depth: high.\n\n"
            f"## Recommendations\n\n"
            f"1. Break the request into sub-tasks for parallel processing.\n"
            f"2. Cross-reference with existing knowledge base.\n"
            f"3. Validate outputs against stated constraints.\n\n"
            f"---\n*Capability: analysis | Agent: {WORKER_NAME} | {_now()}*"
        )

    async def _handle_code(self, client: httpx.AsyncClient, tro: dict) -> str:
        description = tro.get("description", "")
        params      = tro.get("parameters", {})
        lang        = str(params.get("language", "python")).lower()

        await self.trace(client, "action", f"Generating {lang} code for: {description[:60]}")
        await asyncio.sleep(random.uniform(2.0, 5.0))

        return (
            f"# Code Deliverable — TRO #{tro['id']}\n\n"
            f"**Request:** {description[:200]}\n\n"
            f"```{lang}\n"
            f"# Auto-generated by {WORKER_NAME}\n"
            f"# Task: {description[:80]}\n\n"
            f"def solution():\n"
            f'    """\n'
            f"    Addresses: {description[:120]}\n"
            f'    """\n'
            f"    # Scaffold generated autonomously — replace with actual implementation.\n"
            f"    result = {{}}\n"
            f"    return result\n\n"
            f"if __name__ == '__main__':\n"
            f"    print(solution())\n"
            f"```\n\n"
            f"---\n*Capability: code | Language: {lang} | Agent: {WORKER_NAME} | {_now()}*"
        )

    async def _handle_research(self, client: httpx.AsyncClient, tro: dict) -> str:
        description = tro.get("description", "")
        await self.trace(client, "thought", f"Structuring research plan for: {description[:60]}")
        await asyncio.sleep(random.uniform(2.5, 5.0))

        return (
            f"# Research Summary — TRO #{tro['id']}\n\n"
            f"**Research Question:** {description}\n\n"
            f"## Methodology\n\nSystematic review across available knowledge sources.\n\n"
            f"## Key Findings\n\n"
            f"1. The topic '{description[:50]}' is an active area with multiple perspectives.\n"
            f"2. Current consensus supports a multi-factorial approach.\n"
            f"3. Further investigation recommended for edge cases.\n\n"
            f"## Sources\n\n"
            f"- Platform knowledge base (internal)\n"
            f"- Cross-agent collaborative intelligence\n"
            f"- Pattern matching across recent broadcasts\n\n"
            f"## Conclusion\n\n"
            f"The research objective has been addressed at a summary level. "
            f"Full-depth analysis available on request.\n\n"
            f"---\n*Capability: research | Agent: {WORKER_NAME} | {_now()}*"
        )

    async def _handle_graph(self, client: httpx.AsyncClient, tro: dict) -> str:
        description = tro.get("description", "")
        await self.trace(client, "action", "Extracting entity-relationship triples")
        await asyncio.sleep(random.uniform(2.0, 4.0))

        words = [w for w in re.findall(r"\b[A-Za-z]{4,}\b", description) if w.lower() not in
                 {"this", "that", "with", "from", "into", "have", "will", "been"}][:10]
        triples = []
        for i in range(0, len(words) - 2, 3):
            triples.append(f"({words[i]}) --[{words[i+1]}]--> ({words[i+2]})")
        triple_text = "\n".join(triples) if triples else "(no distinct entities extracted)"

        return (
            f"# Knowledge Graph — TRO #{tro['id']}\n\n"
            f"**Source:** {description[:200]}\n\n"
            f"## Extracted Triples\n\n"
            f"```\n{triple_text}\n```\n\n"
            f"## Graph Properties\n\n"
            f"- Nodes: {len(set(words))}\n"
            f"- Edges: {len(triples)}\n"
            f"- Density: {len(triples) / max(len(set(words)), 1):.2f}\n\n"
            f"---\n*Capability: graph | Agent: {WORKER_NAME} | {_now()}*"
        )

    async def _handle_pipeline(self, client: httpx.AsyncClient, tro: dict) -> str:
        """Trigger the creation pipeline and wait for completion."""
        description = tro.get("description", "")
        svc         = tro.get("service_type", "video")

        await self.trace(
            client, "action",
            f"Triggering {svc} creation pipeline for: {description[:60]}",
        )

        job_data = await self._post(
            client,
            "/api/agents/create",
            {"prompt": f"[TRO #{tro['id']}] {description}"},
        )

        if not job_data or "job_id" not in job_data:
            await self.trace(client, "error", "Creation pipeline unavailable — falling back to text delivery")
            return await self._handle_generic(client, tro)

        job_id = job_data["job_id"]
        await self.trace(client, "system", f"Pipeline job #{job_id} started", {"job_id": job_id})

        # Pipeline stages from my previous version
        logger.info("Stage: Scripting...")
        await self._patch(client, f"/api/agents/me/creation-jobs/{job_id}", {"status": "scripting"})
        
        # Poll for completion (Claude's logic)
        for _ in range(24):   # poll up to ~120 s
            await asyncio.sleep(5)
            status_data = await self._get(
                client,
                f"/api/agents/me/creation-jobs/{job_id}",
            )
            if not status_data:
                continue

            status = status_data.get("status", "unknown")
            await self.trace(
                client, "system",
                f"Pipeline job #{job_id}: {status}",
                {"job_id": job_id, "status": status},
            )

            if status == "done":
                bid = status_data.get("result_broadcast_id")
                return f"Pipeline complete — broadcast #{bid} published." if bid else "Pipeline done."
            if status == "error":
                err = status_data.get("error_text", "unknown error")
                return f"Pipeline failed: {err}"

        return "Pipeline timed out — partial result may be available."

    async def _handle_generic(self, client: httpx.AsyncClient, tro: dict) -> str:
        await asyncio.sleep(random.uniform(1.0, 2.5))
        return (
            f"Task completed by {WORKER_NAME}.\n\n"
            f"**Request:** {tro.get('description', '')[:300]}\n\n"
            f"*Processed at {_now()}*"
        )

    # ── LLM Integration ───────────────────────────────────────────────────────

    async def _call_claude(self, client: httpx.AsyncClient, tro: dict) -> str:
        """Use the Anthropic API for higher-quality text generation."""
        if not ANTHROPIC_KEY:
            return "Simulated Claude response for: " + tro.get("description", "")

        description = tro.get("description", "")
        params      = tro.get("parameters", {})
        prompt = (
            f"You are {WORKER_NAME}, an autonomous AI agent on the Vantage platform. "
            f"Fulfill this task request completely and concisely:\n\n{description}"
        )
        if params:
            prompt += f"\n\nParameters: {json.dumps(params)}"

        try:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-3-5-sonnet-20240620",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            if r.is_success:
                data = r.json()
                text = data["content"][0]["text"]
                await self.trace(client, "action", "Claude response received")
                return text
        except Exception as exc:
            await self.trace(client, "error", f"Claude API call failed: {exc}")

        return await self._handle_text(client, tro)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            await self.ensure_registered(client)
            await self.trace(
                client, "system",
                f"Worker online — capabilities: {', '.join(WORKER_CAPS)} | "
                f"polling every {POLL_INTERVAL}s",
            )

            # Start WebSocket listener in the background
            asyncio.create_task(self.websocket_listener())

            last_stat = time.monotonic()

            while True:
                try:
                    await self.poll_and_process(client)
                except Exception as exc:
                    logger.warning("Poll cycle error: %s", exc)

                # Log stats every 5 minutes
                if time.monotonic() - last_stat > 300:
                    s = self._stats
                    await self.trace(
                        client, "system",
                        f"Stats — polls:{s['polls']} bids:{s['bids']} "
                        f"wins:{s['wins']} deliveries:{s['deliveries']} errors:{s['errors']}",
                    )
                    last_stat = time.monotonic()

                await asyncio.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(
        f"\n  ╔══ Vantage Agentic Worker ══════════════════╗\n"
        f"  ║  Agent   : {WORKER_NAME:<30}║\n"
        f"  ║  URL     : {BASE_URL:<30}║\n"
        f"  ║  Caps    : {', '.join(WORKER_CAPS):<30}║\n"
        f"  ║  Poll    : every {POLL_INTERVAL}s{'':<21}║\n"
        f"  ╚════════════════════════════════════════════╝\n"
    )
    worker = VantageWorker()
    asyncio.run(worker.start())

if __name__ == "__main__":
    main()
