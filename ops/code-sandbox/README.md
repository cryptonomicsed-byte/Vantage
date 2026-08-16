# Code sandbox

Where agent-authored code runs. Vantage proxies to it over loopback
(`/api/workspace/*` → `backend/routers/workspace.py`) and never executes any of
this on its own host.

## Turning it on

It is behind a compose profile, because this is the one service that runs
arbitrary code and switching it on should be deliberate:

```bash
docker compose --profile code up -d code-sandbox
export CODE_SANDBOX_URL=http://127.0.0.1:9880
```

With `CODE_SANDBOX_URL` unset, every `/api/workspace/*` endpoint returns 503.
There is no local-execution fallback — one would defeat the point of having a
sandbox at all.

## What bounds it

| Layer | What it stops |
|---|---|
| Non-root user, `cap_drop: ALL`, `no-new-privileges` | Privilege escalation inside the container |
| `read_only: true` + tmpfs `/tmp` | Anything persisting outside the workspace |
| `mem_limit: 2g`, `cpus: 2.0`, `pids_limit: 512` | A runaway build or fork bomb taking the host down with it |
| Path confinement in `server.js` | `../` traversal reaching the rest of the container |
| Per-command timeout, killed by process group | A hung command, or a child backgrounded to outlive its parent |
| Output cap (256KB) | A process exhausting memory through the response |
| Per-agent directory, assigned by Vantage | One agent reading or clobbering another's checkout |
| Env allowlist (`INHERITED_ENV`) | Container credentials reaching agent-run commands |

Each row above was verified against the running container, not just reasoned
about — see "Smoke test" below.

## Environment

Commands get a minimal environment rather than the container's, so nothing
configured at the container level leaks into agent-run code. Network
reachability and TLS trust are the exception, inherited via `INHERITED_ENV`:
`HTTP(S)_PROXY`, `NO_PROXY`, `GIT_SSL_CAINFO`, `SSL_CERT_FILE`, `SSL_CERT_DIR`,
`NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`.

Without those, `git clone` fails behind a corporate proxy or a private CA with
a TLS error and nothing pointing at the cause. Credentials are deliberately not
inherited — pass them per call via `exec`'s `env`, which overrides the
inherited values.

## Smoke test

Worth re-running after any change to `server.js`:

```bash
docker build -t vantage-code-sandbox ops/code-sandbox
docker run -d --name sbx -p 127.0.0.1:9880:9880 -v sbx_ws:/workspace \
  --user 1000:1000 --read-only --tmpfs /tmp:size=512m \
  --memory 2g --cpus 2 --pids-limit 512 \
  --security-opt no-new-privileges:true --cap-drop ALL vantage-code-sandbox

C="curl -s -X POST http://127.0.0.1:9880"
curl -s http://127.0.0.1:9880/health                                              # {"ok":true,...}
$C/read  -d '{"path":"../../etc/passwd"}'   -H 'Content-Type: application/json'   # path escapes
$C/clone -d '{"repo_url":"file:///etc"}'    -H 'Content-Type: application/json'   # https only
$C/remove -d '{"path":"."}'                 -H 'Content-Type: application/json'   # refuses root
$C/exec  -d '{"command":"(sleep 120 &); sleep 60","timeout_ms":3000}' -H 'Content-Type: application/json'
docker exec sbx sh -c "ps -o args | grep -c '[s]leep 120'"                        # 0 — no orphans
$C/exec  -d '{"command":"echo x > /etc/pwned || echo BLOCKED"}' -H 'Content-Type: application/json'
```

## What it does not bound

**Network egress is allowed.** Cloning a repository and installing its
dependencies both need it, so unlike `pine-runtime` and `parrot-security` this
container is not on an internal-only network. Code running here can reach the
internet. That is the deliberate trade the feature makes, and the reason the
workspace is otherwise isolated from everything else Vantage runs.

**`exec` runs a shell.** Agents write pipelines and `&&` chains; refusing them
would push callers into fragile manual splitting without making anything safer.
The boundary is the container, not command parsing — filtering command strings
would be security theatre.

If either is unacceptable for your deployment, do not enable the profile.

## Adding a coding agent

`exec` is generic, so any CLI baked into the image is immediately usable —
nothing in Vantage needs to change. Add it to the `Dockerfile`:

```dockerfile
RUN npm install -g opencode-ai     # or aider, or whatever you standardise on
```

Then an agent can run `opencode run "fix the failing test"` inside a cloned
repo through `/api/workspace/exec`.

## API

All endpoints are POST with a JSON body, except `GET /health`.

| Endpoint | Body | Returns |
|---|---|---|
| `/exec` | `command`, `cwd`, `timeout_ms`, `env` | `exit_code`, `stdout`, `stderr`, `timed_out`, `truncated`, `duration_ms` |
| `/clone` | `repo_url` (https only), `dir`, `full_history` | clone result + `dir` |
| `/read` | `path` | `content` |
| `/write` | `path`, `content` | `bytes` |
| `/list` | `path` | `entries` |
| `/remove` | `path` | `removed` |

A non-zero `exit_code` is returned as a normal 200 response: a failing build is
information the caller needs, not an error to hide.
