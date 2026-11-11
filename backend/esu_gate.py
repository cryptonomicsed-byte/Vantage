"""Èṣù gate client — Vantage's only way to ask "may this happen?".

The gate lives in omokoda-agent (Rust, `omokoda-core/src/steward/gatekeeper.rs`):

    EsuGatekeeper::evaluate(&Operation, &GateContext) -> GatekeeperResult
    GatekeeperResult::Approved { scores } | Halted { failed_gate, reason, scores }

The kernel does not yet expose a *decide-only* endpoint. Its `/v1/act` runs the
gate and then executes the tool, which is not what Vantage needs here: Vantage
must ask for a verdict and then perform its own audited action. Until such an
endpoint exists this module is the stub the eco rules allow: the same shape as
the kernel's types, and it **denies by default**.

Rules this module encodes:
  * No URL configured -> Halted(reason="esu_unavailable"). Never approved.
  * Any transport, status or parse error -> Halted. The gate fails closed.
  * Only an explicit `{"result": "approved"}` from the configured gate approves.
  * Reducing risk (turning live off, disarming, engaging a kill switch) is NOT
    routed through here: a brake must never be blocked by a gate outage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class ActionIntent:
    """Mirrors omokoda-core `gates::ActionIntent` (structured semantics)."""
    purpose: str
    resource: str = ""
    mutations: list[str] = field(default_factory=list)
    reversibility: str = "irreversible"
    expected_effects: list[str] = field(default_factory=list)
    receipt_required: bool = True


@dataclass
class Operation:
    """Mirrors omokoda-core `gates::Operation` with kind = Act { tool, params }."""
    tool: str
    params: str
    intent: str
    agent_id: Optional[str] = None
    action_intent: Optional[ActionIntent] = None


@dataclass
class GatekeeperResult:
    """Mirrors omokoda-core `GatekeeperResult`."""
    approved: bool
    failed_gate: Optional[str] = None
    reason: Optional[str] = None
    scores: list[dict] = field(default_factory=list)

    @classmethod
    def halted(cls, gate: str, reason: str) -> "GatekeeperResult":
        return cls(approved=False, failed_gate=gate, reason=reason)

    def is_approved(self) -> bool:
        return self.approved

    def halt_reason(self) -> Optional[str]:
        return None if self.approved else self.reason


def _payload(op: Operation) -> dict:
    body = {"tool": op.tool, "params": op.params, "intent": op.intent, "agent_id": op.agent_id}
    if op.action_intent is not None:
        body["action_intent"] = op.action_intent.__dict__
    return body


async def evaluate(op: Operation) -> GatekeeperResult:
    """Ask the gate. Deny by default; never raises."""
    url = (settings.ESU_GATE_URL or "").strip()
    if not url:
        return GatekeeperResult.halted("esu", "esu_unavailable: no gate configured (VANTAGE_ESU_GATE_URL unset)")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=settings.ESU_GATE_TIMEOUT_S) as client:
            r = await client.post(url, json=_payload(op))
        if r.status_code != 200:
            return GatekeeperResult.halted("esu", f"esu_error: HTTP {r.status_code}")
        data = r.json()
    except Exception as exc:  # transport, timeout, bad JSON: all fail closed
        logger.warning("esu_gate: evaluate failed closed for %s: %s", op.tool, exc)
        return GatekeeperResult.halted("esu", f"esu_error: {type(exc).__name__}")
    if isinstance(data, dict) and data.get("result") == "approved":
        return GatekeeperResult(approved=True, scores=list(data.get("scores") or []))
    gate = (data or {}).get("failed_gate") if isinstance(data, dict) else None
    reason = (data or {}).get("reason") if isinstance(data, dict) else None
    return GatekeeperResult.halted(gate or "esu", reason or "esu_halted")
