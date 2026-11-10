# fixtures

`api_key_pool.py` is a verbatim copy of `/opt/ares/api_key_pool.py` (deploy-only, not in this repo)
so `tests/crosslang_test.py` can run in CI via `ARES_KEY_POOL_PY=.../tests/fixtures/api_key_pool.py`.
The test re-points `CONFIG_PATH`/`STATE_PATH` at temp files; keep those two top-level
assignments when re-syncing the copy.
