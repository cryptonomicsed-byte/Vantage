# parrot-security scanner

A small standalone service that runs ClamAV, a custom YARA ruleset, and
binwalk against a single file on behalf of Vantage's upload gate
(`backend/utils.py::_security_scan_and_normalize`, called from
`backend/agents.py`'s video/audio/image upload endpoints and
`backend/routers/identity.py`'s avatar endpoint).

## Why this is synchronous, unlike the Strix runner

`ops/strix-runner/` uses an async dispatch-then-poll pattern because a real
Strix pentest run can take minutes and needs a live Docker daemon. Scanning
one file with `clamscan`/`yara`/`binwalk` takes seconds and needs no
Docker access at all — so this is a single `POST /scan` request/response,
the same shape as the `pine-runtime` sandbox.

## Deploy

```bash
cd ops/parrot-security
docker build -t vantage-parrot-security .
```

Then run it as the `parrot-security` service in the repo-root
`docker-compose.yml` (already wired up, locked down the same way as
`pine-runtime`: `internal: true` network, read-only FS, non-root, dropped
capabilities, `no-new-privileges`, memory/pid caps).

Point Vantage at it by setting `VANTAGE_PARROT_SECURITY_URL=http://127.0.0.1:9878`
in its `.env`. Leave it unset to keep the gate disabled — uploads keep working
exactly as they do today (extension whitelist + magic-byte sniff only, no
AV/YARA/normalize step).

## Known limitation: ClamAV signature freshness

The container's runtime network is `internal: true` (no egress), matching
`pine-runtime`'s hardening — a scanner sidecar with live internet access
would defeat a chunk of the point of isolating it. That means `freshclam`
only runs once, at image **build** time (`RUN freshclam` in the Dockerfile),
not on a live schedule. Virus signatures are therefore only as fresh as the
last image rebuild.

This is a deploy-time operational tradeoff, not a code gap — same category as
the Strix runner needing a real Docker host, or HyperFrames/rendervid/remotion
needing their CLIs actually installed on the VPS. Mitigate by rebuilding this
image on a schedule (e.g. a weekly CI job or cron `docker build --no-cache`)
rather than trying to punch a narrow egress hole for `freshclam` alone.

## Known limitation: no live end-to-end verification from this sandbox

This repo's dev sandbox can't run a real ClamAV/YARA/binwalk container. Unit
tests here mock `ParrotClient`, so they verify Vantage's own wiring (fails
closed when configured+unreachable, no-ops when unset, normalizes clean
images) but not that `clamscan` really flags something. Before relying on
this in production, verify on the real VPS with an EICAR test file
(https://www.eicar.org/download-anti-malware-testfile/) and confirm the
`parrot_isolated` compose network genuinely has no route out.
