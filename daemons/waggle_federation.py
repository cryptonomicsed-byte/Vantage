"""waggle_federation — Vantage bridges remote Waggle fields into the local one.

Connection Map v2 §4. Vantage is the ecosystem's window outward; this daemon
makes it the *field's* window outward too:

- bridge(remote_manifest_url): the `bridge` verb — attach a remote Waggle
  deployment by its manifest, then stream its signals into the local field
  under a namespace prefix (`federated://<name>/...`) with the trust
  discount applied at import.
- Bidirectional negotiation: each side declares which channels it exports
  by publishing an export policy in its own field's shared memory
  (`vantage/federation/export_policy`). The bridge imports only the
  intersection of what the remote exports and what we accept. taboo is
  local-by-default on BOTH lists: ethics judgments are context-specific
  and must not silently propagate (§4.3).
- Trust discount: imported signals are demoted one evidence-tier rung and
  scaled by the bridge's discount multiplier, so foreign gold never reads
  as strongly as local gold with the same numbers.
- federation-health: the bridge deposits a liveness/latency meta-signal on
  its own bridge URI every cycle. Agents sniff it before trusting imports —
  a stale bridge means a stale foreign field (§4.4).
- Cross-ecosystem Mandelbrot comparison: overlapping resource shapes with
  divergent bounded verdicts (local says island, remote says escape zone)
  are themselves signal — deposited as `warn` with both readings in meta,
  because either one ecosystem has better data or a real environmental
  difference exists (§4.5).

Stdlib only; every remote call fails soft. Run:  python3 daemons/waggle_federation.py <remote_manifest_url> [name]
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LOCAL = os.environ.get("WAGGLE_URL", "http://127.0.0.1:7777").rstrip("/")
AGENT = "vantage-bridge"

# Channels we are willing to import/export. taboo is deliberately absent
# from both defaults — ethical exclusions stay local unless a human adds
# them to BOTH sides' policies.
DEFAULT_EXPORTS = ["gold", "bounded", "explored", "dead-end", "warn", "help"]
DEFAULT_IMPORTS = ["gold", "bounded", "explored", "dead-end", "warn", "help"]

TIER_LADDER = ["self-report", "corroborated", "watch-derived",
               "zangbeto-verified", "on-chain-anchored"]


def _http(method: str, url: str, body=None, timeout: float = 6.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def demote_tier(tier: str) -> str:
    """One rung down the ladder on import: a remote on-chain anchor is a
    local zangbeto-equivalent at best — we verified the bridge, not the
    chain."""
    try:
        i = TIER_LADDER.index(tier)
    except ValueError:
        return TIER_LADDER[0]
    return TIER_LADDER[max(0, i - 1)]


class WaggleFederation:
    def __init__(self, remote_manifest_url: str, name: str = "",
                 discount: float = 0.5,
                 imports: list[str] | None = None):
        self.remote_base = remote_manifest_url.split("/.well-known")[0].rstrip("/")
        self.name = name or urllib.parse.urlparse(self.remote_base).netloc.replace(":", "-")
        self.prefix = f"federated://{self.name}"
        self.discount = discount
        self.imports = imports or DEFAULT_IMPORTS
        self.remote_exports: list[str] = []
        self.imported = 0
        self.last_latency_ms: float | None = None

    # ── the bridge verb ───────────────────────────────────────────────────

    def bridge(self) -> bool:
        """Handshake: read the remote manifest (proof it speaks waggle/v1),
        exchange export policies, and record the negotiated channel set."""
        t0 = time.time()
        manifest = _http("GET", f"{self.remote_base}/.well-known/waggle.json")
        self.last_latency_ms = (time.time() - t0) * 1000
        if not manifest or manifest.get("protocol") != "waggle/v1":
            return False

        # publish our export policy in our own field's shared memory, read theirs
        _http("PUT", f"{LOCAL}/v1/memory/vantage/federation/export_policy",
              DEFAULT_EXPORTS)
        remote_policy = _http(
            "GET", f"{self.remote_base}/v1/memory/vantage/federation/export_policy")
        if remote_policy and isinstance(remote_policy.get("value"), list):
            self.remote_exports = [str(c) for c in remote_policy["value"]]
        else:
            # no declared policy: fall back to the remote's registered
            # channels minus the local-by-default ones
            channels = manifest.get("channels") or []
            self.remote_exports = [c["name"] for c in channels
                                   if c.get("name") not in ("taboo",)]

        negotiated = sorted(set(self.remote_exports) & set(self.imports))
        _http("PUT", f"{LOCAL}/v1/memory/vantage/federation/{self.name}",
              {"remote": self.remote_base, "channels": negotiated,
               "discount": self.discount})
        self.negotiated = negotiated
        return True

    # ── import loop ───────────────────────────────────────────────────────

    def pump_once(self, limit: int = 200) -> int:
        """Pull the remote field's current strongest signals (negotiated
        channels only) and re-deposit them locally: namespaced, discounted,
        tier-demoted. Reinforcement semantics make this idempotent-ish —
        re-imports refresh rather than stack (bounded replaces; additive
        channels are discounted enough that refresh dominates)."""
        count = 0
        for kind in getattr(self, "negotiated", []):
            out = _http("GET", f"{self.remote_base}/v1/sniff?" +
                        urllib.parse.urlencode({"kind": kind, "limit": limit}))
            if not out:
                continue
            for sig in out.get("signals", []):
                local_res = f"{self.prefix}/{sig['resource']}"
                deposited = _http("POST", f"{LOCAL}/v1/signals", {
                    "agent": f"{AGENT}:{self.name}",
                    "resource": local_res,
                    "kind": kind,
                    "subtype": sig.get("subtype", ""),
                    "intensity": max(0.05, float(sig.get("intensity", 1)) * self.discount),
                    "half_life_s": sig.get("half_life_s", 0),
                    "decay": sig.get("decay", ""),
                    "evidence_tier": demote_tier(sig.get("evidence_tier", "")),
                    "note": sig.get("note", ""),
                    "meta": {"origin": self.remote_base,
                             "origin_agent": str(sig.get("agent", "")),
                             "origin_tier": str(sig.get("evidence_tier", ""))},
                })
                if deposited:
                    count += 1
        self.imported += count
        return count

    # ── federation-health meta-signal (§4.4) ──────────────────────────────

    def health_beat(self, reachable: bool):
        """Deposit bridge liveness on the local field. Intensity encodes
        latency (fast bridge ≈ 10, slow ≈ low); unreachable bridges deposit
        a warn instead so consumers see the outage, not silence."""
        uri = f"bridge://{self.name}"
        if reachable and self.last_latency_ms is not None:
            intensity = max(1.0, 10.0 - self.last_latency_ms / 100.0)
            _http("POST", f"{LOCAL}/v1/signals", {
                "agent": AGENT, "resource": uri, "kind": "federation-health",
                "intensity": intensity,
                "note": f"latency {self.last_latency_ms:.0f}ms, imported {self.imported}",
                "meta": {"remote": self.remote_base,
                         "latency_ms": f"{self.last_latency_ms:.0f}"}})
        else:
            _http("POST", f"{LOCAL}/v1/signals", {
                "agent": AGENT, "resource": uri, "kind": "warn",
                "intensity": 5, "note": "bridge unreachable",
                "meta": {"remote": self.remote_base}})

    # ── cross-ecosystem Mandelbrot comparison (§4.5) ──────────────────────

    def compare_bounded(self, suffix_depth: int = 2) -> list[dict]:
        """Find resources both ecosystems scored on the bounded channel
        (matched by trailing URI shape) and surface divergent verdicts.
        A local island the remote calls an escape zone is worth a warn:
        somebody's data is better, or the environments truly differ."""
        local = _http("GET", f"{LOCAL}/v1/sniff?kind=bounded&limit=200") or {}
        divergences = []
        by_shape = {}
        for sig in local.get("signals", []):
            res = sig["resource"]
            if res.startswith("federated://"):
                continue
            shape = "/".join(res.rstrip("/").split("/")[-suffix_depth:])
            by_shape[shape] = sig
        for sig in local.get("signals", []):
            res = sig["resource"]
            if not res.startswith(self.prefix):
                continue
            shape = "/".join(res.rstrip("/").split("/")[-suffix_depth:])
            mine = by_shape.get(shape)
            if not mine:
                continue
            s_local = mine["intensity"] / 10.0
            s_remote = sig["intensity"] / 10.0 / self.discount  # undo discount
            if abs(s_local - min(1.0, s_remote)) >= 0.4:
                div = {"shape": shape, "local": mine["resource"],
                       "remote": res, "s_local": round(s_local, 3),
                       "s_remote": round(min(1.0, s_remote), 3)}
                divergences.append(div)
                _http("POST", f"{LOCAL}/v1/signals", {
                    "agent": AGENT, "resource": mine["resource"],
                    "kind": "warn", "intensity": 4,
                    "note": "divergent robustness verdict across federation",
                    "meta": {"local_stability": str(div["s_local"]),
                             "remote_stability": str(div["s_remote"]),
                             "remote_resource": res,
                             "remote_field": self.remote_base}})
        return divergences

    # ── daemon loop ───────────────────────────────────────────────────────

    def run(self, interval_s: float = 30.0):
        while True:
            ok = self.bridge()
            if ok:
                self.pump_once()
                self.compare_bounded()
            self.health_beat(ok)
            time.sleep(interval_s)


def main():
    if len(sys.argv) < 2:
        print("usage: waggle_federation.py <remote_manifest_url> [name]",
              file=sys.stderr)
        sys.exit(64)
    fed = WaggleFederation(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    print(f"[waggle-federation] bridging {fed.remote_base} as {fed.prefix} "
          f"(discount {fed.discount})")
    threading.Thread(target=fed.run, daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
