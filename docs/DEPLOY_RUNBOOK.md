# Deploying the coordination work to the VPS

**Written 2026-09-04.** Covers the change set on
`claude/session-01kgkxcpyjpdvayax38hscdf-k54vrp` (typed work references, the
kind registry, workspace roles, presence, runtime receipts, radio mesh
ingress, transports).

This was written from the CI container, which has **no SSH access to the VPS
and no route to it** — port 22 is unreachable and outbound `CONNECT` is
policy-gated. So nothing here has been run against `2.25.70.156`. What *has*
been run, in this container, is everything below the "Verified here" line.

---

## The one thing that is not just `git pull`

There is a new runtime dependency: **`blake3`**. Receipt verification
recomputes the agent kernel's receipt ids, which are BLAKE3 over the receipt
fields; `hashlib` cannot do it (BLAKE2 is a different function). It is in
`pyproject.toml`, and a deploy that skips `pip install` will import fine and
then fail the first time anyone submits a receipt.

Everything else is additive: nine new tables, created idempotently at
startup, and no destructive migration.

---

## Steps

```sh
# 1. On the VPS, as whoever owns the service
cd /opt/ares/Vantage

# 2. Back the database up first. It is small and the restore is a file copy.
cp data/vantage.db "data/vantage.db.$(date +%Y%m%d-%H%M%S).bak"

# 3. Pull
git fetch origin
git checkout main
git pull --ff-only origin main

# 4. Install the new dependency (this is the step that is easy to skip)
.venv/bin/pip install -e .        # or: .venv/bin/pip install 'blake3>=0.4.0'
.venv/bin/python -c "import blake3; print('blake3', blake3.__version__)"

# 5. Frontend
cd frontend && npm ci && npm run build && cd ..
#    WEBUI_DIR is /opt/ares/Vantage/frontend/dist -- build in place.

# 6. Restart
sudo systemctl restart vantage.service
sudo systemctl status vantage.service --no-pager
```

### Verify it actually came up

```sh
# The nine new tables
sqlite3 data/vantage.db "
  SELECT name FROM sqlite_master WHERE type='table' AND name IN
   ('work_ref_links','workspace_memberships','role_templates',
    'principal_presence','receipt_keys','runtime_receipts',
    'mesh_nodes','mesh_packets','mesh_relay_attestations')
  ORDER BY name;"
# expect: all nine

# Exactly four instance-wide role templates, no more, after any number of restarts
sqlite3 data/vantage.db \
  "SELECT COUNT(*) FROM role_templates WHERE guild_id IS NULL;"
# expect: 4

# New endpoints answer
curl -s localhost:8000/api/guilds/work/kinds -H "X-Agent-Key: $KEY" | head -c 200
curl -s localhost:8000/api/meshnet/packets | head -c 200
```

### Rolling back

```sh
sudo systemctl stop vantage.service
git checkout <previous-sha>
cp data/vantage.db.<timestamp>.bak data/vantage.db   # only if you must
sudo systemctl start vantage.service
```

The new tables are additive and unused by older code, so **rolling back the
code alone is safe and does not need the database restored**. Restore the
backup only if something wrote data you want gone.

---

## The Conductor (optional, unchanged deployment)

`ops/conductor` gained a work-state op. It has no dependencies and builds with
`mix compile`. If it is not running, nothing here needs it: channels in `open`
flow work without it, and presence still writes through the HTTP path.

---

## Verified here

Run in the CI container against a database built by the code currently on
`main` — that is, a copy of the shape the VPS has today:

- **Full app lifespan boots** on a fresh database: 157 tables, all nine new
  ones created, four templates seeded.
- **In-place upgrade of a production-shaped database**, three consecutive
  boots: pre-existing agent rows and an open TRO survived untouched, table
  count stable, template count stable at four.
- **Python suite**: 1024 passed. Three failures (`test_alpha` ×2,
  `test_degen_filters` ×1) reproduce identically on a clean checkout of
  `main` and are not from this change set.
- **Elixir**: 87/87. **Rust** (`omokoda-core`): 683/683. **C++** mesh bridge:
  51/51.
- **Frontend builds** clean.

Two bugs were found by running the lifespan, and both would have hit the VPS:

1. **`init_mesh_db` name collision.** `backend/mesh_store.py` already exports
   that name and `main.py` imports it at module level. A bare
   `from .mesh_gateway import init_mesh_db` inside the lifespan rebound the
   name for the whole function, making the module-level one an
   `UnboundLocalError` at its own call site earlier in startup. **The service
   would not have booted.** No test caught it because the suite does not run
   the lifespan at all (`conftest.py`: "ASGITransport doesn't run app
   lifespan"). Fixed by aliasing the import.
2. **Role templates re-seeded on every restart.** The `UNIQUE (guild_id,
   name)` constraint cannot fire for an instance-wide template, because
   `guild_id` is NULL and SQLite does not treat NULL as equal to NULL in a
   unique index — so `INSERT OR IGNORE` inserted four more rows per boot,
   forever. Fixed by checking first, plus `deduplicate_builtin_templates()`
   which repairs a database that already has duplicates. The test that should
   have caught this asserted on `list_templates()`, which deduplicates by
   name in Python — it passed happily while the table grew underneath it. It
   now counts rows.

## Not verified

Nothing has run against the live relay, a Freenet node, or LoRa hardware.
Receipt verification was checked against a receipt the real kernel produced;
the Meshtastic decoders were checked against each other. Neither is the same
as a live round trip.
