# Strix runner

A small standalone service that runs real [Strix](https://github.com/usestrix/strix)
security scans on behalf of Vantage's `/api/code/repo/{owner}/{name}/scan?engine=strix`
endpoint.

## Why this isn't inside the Vantage container

Strix needs a live Docker daemon (it pulls its own sandbox image) and the `strix`
CLI. Vantage's API container is deliberately locked down — non-root, no Docker
socket, resource-capped — and that's staying that way. This runner is deployed
directly on the VPS host instead, where Docker and `strix` already run, and
Vantage talks to it over plain HTTP.

## Deploy

```bash
curl -sSL https://strix.ai/install | bash   # installs the `strix` CLI; needs Docker running
cd ops/strix-runner
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export STRIX_LLM="openai/gpt-5.4"      # or whatever provider/model you're using
export LLM_API_KEY="..."
export STRIX_RUNNER_PORT=9877           # matches VANTAGE_STRIX_RUNNER_URL in Vantage's .env
python3 server.py
```

For a persistent deployment, run it under systemd (`ExecStart=.../venv/bin/python server.py`,
`Restart=on-failure`) rather than a bare `nohup`.

Point Vantage at it by setting `VANTAGE_STRIX_RUNNER_URL=http://127.0.0.1:9877` in its `.env`.
Leave it unset to keep `engine=strix` disabled (Vantage returns `503`) — the existing fast
regex scan (`engine=regex`, the default) keeps working either way.

## Known gap: findings format

Strix's own docs don't specify a machine-readable output schema — only that results land in
`strix_runs/<run-name>/`. Before this runner can return real structured `findings`, run a real
scan against a small deliberately-vulnerable test repo and inspect that directory by hand:

```bash
find strix_runs/<run-name> -type f
```

Then update `_execute()` in `server.py` to parse whatever's actually there. Until that's done,
`GET /run/{run_id}` reports `raw_output_dir` and an empty `findings` list once the scan
completes, so the pipeline still works end-to-end (dispatch → poll → complete) — it just
doesn't have structured findings yet.
