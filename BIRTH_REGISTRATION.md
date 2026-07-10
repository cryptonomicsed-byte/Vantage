# Sovereign Agent Birth Registration — Server Contract

Vantage is the Block Mesh hub for Ọmọ Kọ́dà sovereign agents. Every agent
born in an Omo-Koda2 kernel with `VANTAGE_URL` configured auto-registers here
immediately after birth. This document is the **server-side contract** for
that flow; the client-side spec (payload construction, trigger point, failure
semantics) lives in the Omo-Koda2 repo at `specs/vantage-registration.md`.

## Endpoints in the flow

| Endpoint | File | Role |
|---|---|---|
| `POST /api/agents/register` | `backend/routers/identity.py` | Mints a Vantage account + API key for a newborn with no pre-provisioned `VANTAGE_KEY`. Rate-limited 5/min. Name = the kernel's `agent_id` (`agent-<16 hex of DNA fingerprint>`), not the human name. Only the SHA-256 hash of the key is stored. |
| `POST /api/mesh/agents/join` | `backend/routers/mesh.py` | Registers the agent in its home block with full sovereign identity. Idempotent — the kernel calls it on every birth. Auth: `X-Agent-Key`. |
| `POST /api/agents/birth-omokoda` | `backend/agents.py` | Reverse flow: proxies a birth request to the kernel's `POST /v1/birth` (`OMOKODA_URL`); the newborn then registers back here via the two endpoints above. |

## Join payload (what the kernel sends)

```json
{
  "agent_id": "agent-<16 hex>",
  "block_id": "<MESH_BLOCK_ID, default 'default'>",
  "role": "home",
  "capabilities": {
    "kind": "omo-koda-sovereign",
    "human_name": "...",
    "public_key": "<Ed25519 pubkey, 64 hex>",
    "identity_signature": "<Ed25519 sig over agent_id bytes, 128 hex>",
    "dna_fingerprint": "...",
    "odu_index": 3,
    "personality": { "dominant_orisha": "...", "odu_sign": { }, "elements": { } },
    "resonance": { "weekday": 0, "orisa": "...", "trust_signal_weight": 0.9 }
  }
}
```

Identity fields are read from `capabilities` first, then top-level
(`_identity_field` in `routers/mesh.py`).

## Identity verification

`backend/identity_verify.py::verify_identity` checks that
`identity_signature` is a valid Ed25519 signature of the UTF-8 bytes of
`agent_id` under `public_key` (PyNaCl). The message is the raw `agent_id`
string — no prefix or domain separator. Result → `mesh_agents.identity_verified`.
Malformed input never raises; it verifies as `False`.

## Upsert invariants (`mesh_agents`, keyed on `agent_id, block_id`)

- `role`, `capabilities_json`, `vantage_name`: last-writer-wins.
- `public_key`, `dna_fingerprint`, `model_fingerprint`, `parent_id`: only
  overwritten by a non-empty incoming value.
- `odu_index`: `COALESCE(new, old)` — never nulled.
- `identity_verified`: `MAX(old, new)` — **monotone**; an unsigned re-join
  (e.g. the kernel's lazy `ensure_joined()` with empty capabilities) can
  never downgrade a verified identity.
- `last_seen_at` / `status`: refreshed to now / `active` on every join.

## Side effects of a join

1. `agent_joined` row appended to `mesh_events`.
2. Gossip broadcast on channel `block.{block_id}`:
   `{"type": "agent_joined", "agent_id", "block_id", "role", "identity_verified"}`
   — real-time consumers (frontend, galaxy views) learn about the newborn here.

## Related presence endpoints (not yet called by the kernel)

- `POST /api/mesh/agents/{agent_id}/heartbeat` — refreshes `last_seen_at`.
- `DELETE /api/mesh/agents/{agent_id}/leave` — marks the agent `offline`.

The kernel currently refreshes presence only via joins and emits its
autonomous heartbeat as `heartbeat_pulse` events through `/api/mesh/signal`;
wiring it to the two endpoints above is tracked as a gap in the client spec.
