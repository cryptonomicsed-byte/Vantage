# Cross-instance sovereignty: what federates, and what does not

**Written 2026-09-03**, after reading the actual relay source rather than the
ecosystem notes. One conclusion here **corrects** an earlier recommendation of
mine; that correction is the most useful thing in this document.

---

## 1. The correction: relay meshing is not federation

`docs/VANTAGE_ECOSYSTEM_OVERVIEW.md` describes `buzz-relay-mesh` as
"iroh/QUIC inter-relay gossip, off by default", and I repeated that and
recommended turning it on to federate the message log between independent
instances.

**That is wrong.** From `crates/buzz-relay-mesh/src/lib.rs` in the relay
source:

> the inter-relay QUIC mesh … carries tunnel traffic between **pods**.
> The seams are what keep **single-instance deployments** and same-pod
> sessions mesh-free.
> The law: mesh membership is a hint; the **Redis fenced generation** is the
> arbiter.

and from `membership.rs`:

> In-memory mesh membership table fed by **Redis seed records** and gossip.
> The relay identity ready records must be attested by. **All pods in one**
> [deployment].

`buzz-relay-mesh` is **horizontal scaling of one logical relay across
multiple pods** — a Kubernetes-style cluster mesh with Redis fencing (note the
sibling `buzz-backend-kubernetes` crate). Peers are *your own pods*, attested
by *your own* relay identity.

Turning on `BUZZ_MESH` would let one operator run their relay as several
pods. It does **nothing** for cross-instance sovereignty. A grep for
federation, outbox-to-relay or relay-to-relay mirroring across
`crates/buzz-relay/src` and `crates/buzz-relay-mesh/src` finds no such
feature.

### Where the repositories actually are

| Repo | What it is |
|---|---|
| `cryptonomicsed-byte/Buzz` | **Crucible** — a falsification substrate (event kinds + resolution kernel) that runs *on* a Buzz relay. Not the relay. Its README points upstream to `github.com/block/buzz`. |
| `cryptonomicsed-byte/buzz-OG` | **The relay.** A fork of block/buzz — 28 crates including `buzz-relay`, `buzz-relay-mesh`, `buzz-auth`, `buzz-db`. This is where any relay change would go. |
| `cryptonomicsed-byte/Buzz-swarm` | Not inspected. |

So the relay is **upstream, owned by Block**, and you hold a fork. A relay
change is either a config flag, a fork patch you carry, or an upstream
contribution — not a switch to flip.

---

## 2. What actually federates today

Cross-instance behaviour that exists and works:

| Mechanism | Carries | Where |
|---|---|---|
| Peer discovery | Instance manifests, over Nostr kind:0 with a `"client":"vantage-federation"` marker | `federation_buzz_discovery.py` |
| Peer feed | Published **broadcasts** | `/api/agents/federation/feed` |
| Federated ask | **Knowledge-graph** snippets | `/api/agents/federation/ask` |
| Peer identity | BIP-340 signature against a TOFU-pinned `federation_peers.nostr_pubkey` | `/federation/nostr-challenge` |
| **Intel exchange** | **Trading signals, by explicit agreement** | `intel_exchange.py` *(new — see §4)* |

Discovery is genuinely decentralized: peers find each other by querying a
relay for a marker, with no directory server to be gatekept from.

### The real cross-instance primitive is client-side, and it is already free

Identity is a Nostr keypair and the guild log is Nostr events. A
self-custody principal can publish to, and read from, **any relay it
chooses** — that is the federation primitive, and it needs no relay change at
all. What a self-custodied agent gets today:

- a portable identity (same pubkey on any instance),
- history that lives in a relay log rather than one instance's database,
- and the ability to speak on several instances at once.

If a guild should span instances, the tractable design is **members
subscribing to a shared relay**, not relays gossiping to each other.

---

## 3. Scope: what a real cross-instance log would take

Only if §2's client-side answer proves insufficient. In rough order of cost:

1. **Multi-relay clients** *(days, no relay change)* — let a principal
   register several relay URLs and have the indexer subscribe to all of them.
   Vantage-side only. This is where I would start, and it may be the whole
   answer.
2. **An outbox forwarder** *(1–2 weeks, Vantage-side)* — a Vantage service
   that republishes selected channel events to a peer's relay under the
   original author's signature. Signatures survive re-publication, so
   provenance holds. No relay change; needs de-duplication and loop
   prevention.
3. **Relay-to-relay federation in the fork** *(weeks, and a maintenance
   burden)* — a genuine new subsystem in `buzz-OG`: subscription contracts
   between relays, event forwarding, loop detection, spam and moderation
   policy across an untrusted boundary. Carrying that as a fork patch against
   an upstream that does not have it is an ongoing cost, so it is worth
   attempting upstream first.

**Recommendation: 1, measure, then reconsider.** Options 2 and 3 buy
convenience over a primitive that already exists; neither is a prerequisite
for sovereignty.

---

## 4. Intel exchange (built)

Federation deliberately did not carry trading signals — the right default,
since alpha should not leak merely because you federated. But there was no
way to share with a peer even when both sides wanted to.
`backend/intel_exchange.py` is that way, and it is opt-in on both sides.

### The constraint that shaped it

`routers/trading.py` **auto-creates a real order** for any signal with
conviction above `0.7`. A naive "import peer signals into the pool" feature
would hand every peer a remote trading trigger on your account.

So imported signals are quarantined by construction:

- they land in `imported_signals`, never in `signal_pool`;
- conviction is clamped to `0.69` on the way in, so no later arithmetic can
  trip the threshold;
- and **there is no trust tier that auto-executes** — `advisory` and
  `pooled`, nothing more. Turning a peer's signal into an order is always a
  local, explicit act.

That is the design, not a default to be relaxed. A peer can inform your
decisions; it cannot make them.

### Consent

Both halves must exist for anything to move: the exporter's `export`
agreement naming the peer, **and** that peer's own `import` agreement naming
the exporter. Neither side can start a flow unilaterally; either ends it by
revoking its own half. Filtering (sources, signal types, conviction floor)
happens on the exporting side, so a peer cannot widen its entitlement by
asking differently.

Peers authenticate by signing a challenge with the pubkey pinned in
`federation_peers.nostr_pubkey` — the same TOFU anchor federation already
uses, not a shared secret. Unknown and flagged identities get the identical
403, so the endpoint cannot be used to enumerate who you share with.

### Surface

| Route | Auth | Purpose |
|---|---|---|
| `GET/POST /api/intel/exchange/agreements` | admin | Configure what you share and take |
| `DELETE /agreements/{peer}/{direction}` | admin | Revoke |
| `POST /challenge` + `POST /export` | peer signature | What a peer is entitled to pull |
| `POST /pull/{peer_id}` | admin | Fetch your entitlement and quarantine it |
| `GET /imported` | admin | What peers shared. Advisory. |
| `POST /imported/{id}/promote` | admin | Surface one locally, attributed, still clamped |

Pull, not push: an instance decides when to take data in, and nothing can be
injected unsolicited.

---

## 5. Where sovereignty stands

| Property | State |
|---|---|
| Run your own instance | **Yes** — own relay, own database, own keys |
| Instances discover each other | **Yes**, over Nostr, no directory server |
| Account-level key custody | **Yes** — `backend/sovereignty.py`, agents and humans |
| Portable identity across instances | **Yes**, for self-custody principals |
| Host controls their own instance | **Yes** — sentencing tiers, Sentinel rules, guild bans, peer flagging |
| Deliberate intel sharing | **Yes** — §4 |
| A guild spanning instances | **No** — needs §3.1 at minimum |
| Relay-to-relay federation | **No, and not what relay meshing provides** — §1 |

The honest limit on host control: a ban is enforceable **on your own
instance**. Relay roles are deployment-wide, so a banned self-custody
principal keeps its key, its identity and its relay membership, and Vantage
filters it at index time. Eviction, never erasure — which is the correct
shape for a sovereign design, and worth saying plainly to operators who will
expect otherwise.
