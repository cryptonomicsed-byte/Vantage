# Live trading: layers, demo mode and Sentinel sign-off

Paper trading is always on. Every strategy produces paper signals against live
prices, with full intel, and nothing here can turn that off. Live is an extra
execution path: one signal pipeline, two executors. A trade leaves paper only
when **every** check below permits it.

| Layer | Who controls it | Default | Where |
|---|---|---|---|
| 0. Instance ceiling | the operator, in the service environment | closed (`VANTAGE_LIVE_TRADING` unset/false) | `trading_mode.env_ceiling_open()` |
| Instance switch | Sentinel operators, by sign-off | off | `PUT /api/trading/live-mode` |
| 1. Per-strategy opt-in | the strategy's owner | off | `arm`, `enable-live`, a bound wallet with a key |
| 2. Account kill switch | the account owner | released | Settings > Security, `PUT /api/trading/live-killswitch` |
| Demo mode | the operator | off | `VANTAGE_DEMO_MODE` |

## Rules

- The env var is a ceiling, never a force-on. With the ceiling open the
  instance switch still decides.
- An agent key can never change the instance switch or arm a standalone
  signer. Those are Sentinel operator actions (`X-Human-Session`).
- Toward live (switch on, arm a signer, set a signer's wallet) files a
  sign-off card and returns HTTP 202. A **different** operator with the owner
  or security role approves it; the requester never can. On approval the
  action is cleared with the Èṣù gate (deny by default while
  `VANTAGE_ESU_GATE_URL` is unset), then applied and audited with both
  identities. Unanswered cards expire; expiry means deny.
- Toward paper (switch off, disarm) is immediate for any operator and is
  never gated.
- Layer 1: changing or deleting a strategy's wallet drops it back to paper.
- Layer 2: engaging the kill switch revokes live for every strategy on that
  account at once. Releasing it re-enables nothing by itself.
- Deny-listed signers (`VANTAGE_TRADING_DENYLIST`) can never be armed, and
  always read as disarmed. Deny-listed units (`VANTAGE_DAEMON_DENYLIST`)
  cannot be started through the API.

## Demo mode

Demo and live are two different states. With `VANTAGE_DEMO_MODE=true`:

- The ceiling reads as open, so testing is not blocked.
- Every real settlement is refused: `execute-live` (HTTP 423), the execution
  engine, standalone signers (they always read disarmed), and Sui settlement.
- A sign-off can apply without an Èṣù approval, and the card records that.
  Nothing it enables can settle.
- A switch turned on in demo is tagged `set_in_demo` and reads as **off**
  once the instance leaves demo, so demo state never leaks into a launch.

## Traces

Paper and live decisions and fills both write traces in the same schema,
tagged `execution_mode=paper|live` and `instance_demo=true|false`.

## Operator bootstrap (Phase 1)

Until passkey and Nostr sign-in land, the first operators are added with the
instance admin key:

    POST /api/admin/sentinel/operators   {"human_id": 1, "role": "owner"}

Roles: `owner`, `security` (may approve trading cards), `operator` (may request
and brake). The inbox is `GET /api/sentinel/signoffs`.
