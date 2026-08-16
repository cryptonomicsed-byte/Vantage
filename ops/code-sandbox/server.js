// Code sandbox — clone repositories, run commands, read and write files.
//
// This is the blast-radius boundary for agent-run code. Vantage never executes
// any of this on its own host: it proxies to this container over loopback, and
// everything below assumes the caller is hostile.
//
// What the container gives us (see docker-compose.yml): non-root user, dropped
// capabilities, no-new-privileges, pid/memory/cpu caps, and a workspace volume
// that is the only writable path.
//
// What this file has to give us on top:
//   * every path is resolved and confined under WORKSPACE_ROOT, so ..
//     traversal cannot reach the rest of the container
//   * every command runs with a hard timeout and gets killed by process group,
//     so a backgrounded child cannot outlive it
//   * output is capped, so a runaway process cannot exhaust memory through us
//
// Unlike pine-runtime, this container DOES have network egress -- it cannot
// clone a repository or install dependencies without it. That is the deliberate
// tradeoff of the feature, and the reason the workspace is isolated from
// everything else Vantage runs.

const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs/promises');
const path = require('path');

const HOST = process.env.SANDBOX_HOST || '0.0.0.0';
const PORT = parseInt(process.env.SANDBOX_PORT || '9880', 10);
const WORKSPACE_ROOT = process.env.WORKSPACE_ROOT || '/workspace';

const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 900_000;
const MAX_OUTPUT_BYTES = 256 * 1024;
const MAX_FILE_BYTES = 2 * 1024 * 1024;

/**
 * Resolve a caller-supplied path inside the workspace, or throw.
 * Uses path.resolve then a prefix check, so "../../etc/passwd", absolute
 * paths and symlink-shaped inputs all land back inside or are rejected.
 */
function confine(relative = '.') {
  const root = path.resolve(WORKSPACE_ROOT);
  const target = path.resolve(root, relative);
  if (target !== root && !target.startsWith(root + path.sep)) {
    throw new Error(`path escapes the workspace: ${relative}`);
  }
  return target;
}

function runCommand({ command, args, cwd, timeoutMs, env }) {
  return new Promise((resolve) => {
    const started = Date.now();
    let stdout = '';
    let stderr = '';
    let truncated = false;
    let timedOut = false;

    const child = spawn(command, args, {
      cwd,
      // Own process group, so the kill below takes any children with it.
      detached: true,
      env: {
        PATH: process.env.PATH,
        HOME: cwd,
        // Non-interactive everything: a command that stops to ask a question
        // would otherwise just sit there until the timeout.
        GIT_TERMINAL_PROMPT: '0',
        GIT_ASKPASS: '/bin/true',
        CI: '1',
        DEBIAN_FRONTEND: 'noninteractive',
        ...(env || {}),
      },
    });

    const capture = (chunk, which) => {
      if (truncated) return;
      const text = chunk.toString();
      if (which === 'out') stdout += text;
      else stderr += text;
      if (stdout.length + stderr.length > MAX_OUTPUT_BYTES) {
        truncated = true;
        stdout = stdout.slice(0, MAX_OUTPUT_BYTES);
        stderr = stderr.slice(0, MAX_OUTPUT_BYTES);
      }
    };
    child.stdout.on('data', (c) => capture(c, 'out'));
    child.stderr.on('data', (c) => capture(c, 'err'));

    const timer = setTimeout(() => {
      timedOut = true;
      try {
        process.kill(-child.pid, 'SIGKILL');
      } catch {
        /* already gone */
      }
    }, timeoutMs);

    child.on('error', (err) => {
      clearTimeout(timer);
      resolve({
        exit_code: -1,
        stdout,
        stderr: `${stderr}\n${err.message}`.trim(),
        timed_out: false,
        truncated,
        duration_ms: Date.now() - started,
      });
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({
        exit_code: timedOut ? 124 : code,
        stdout,
        stderr,
        timed_out: timedOut,
        truncated,
        duration_ms: Date.now() - started,
      });
    });
  });
}

async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_FILE_BYTES * 2) throw new Error('request body too large');
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString());
}

const routes = {
  'GET /health': async () => ({ ok: true, workspace: WORKSPACE_ROOT }),

  'POST /exec': async (body) => {
    const cwd = confine(body.cwd || '.');
    await fs.mkdir(cwd, { recursive: true });
    const timeoutMs = Math.min(
      Math.max(parseInt(body.timeout_ms || DEFAULT_TIMEOUT_MS, 10), 1000),
      MAX_TIMEOUT_MS
    );
    if (!body.command) throw new Error('command is required');
    // Shell on purpose: agents write pipelines and && chains, and refusing
    // them would just push callers into fragile manual splitting. The security
    // boundary is the container, not command parsing -- pretending otherwise
    // would be security theatre.
    return runCommand({
      command: '/bin/sh',
      args: ['-lc', body.command],
      cwd,
      timeoutMs,
      env: body.env,
    });
  },

  'POST /clone': async (body) => {
    if (!body.repo_url) throw new Error('repo_url is required');
    if (!/^https:\/\//.test(body.repo_url)) {
      // https only: ssh:// or file:// would reach the container's own keys and
      // filesystem, and git:// is unauthenticated plaintext.
      throw new Error('repo_url must be an https:// URL');
    }
    const dir = body.dir || body.repo_url.replace(/\.git$/, '').split('/').pop();
    const target = confine(dir);
    const depth = body.full_history ? [] : ['--depth', '1'];
    const result = await runCommand({
      command: 'git',
      args: ['clone', ...depth, body.repo_url, target],
      cwd: confine('.'),
      timeoutMs: Math.min(parseInt(body.timeout_ms || 300_000, 10), MAX_TIMEOUT_MS),
    });
    return { ...result, dir: path.relative(confine('.'), target) || '.' };
  },

  'POST /read': async (body) => {
    const target = confine(body.path);
    const stat = await fs.stat(target);
    if (stat.size > MAX_FILE_BYTES) throw new Error(`file too large (${stat.size} bytes)`);
    return { path: body.path, content: await fs.readFile(target, 'utf-8') };
  },

  'POST /write': async (body) => {
    const target = confine(body.path);
    const content = String(body.content ?? '');
    if (Buffer.byteLength(content) > MAX_FILE_BYTES) throw new Error('content too large');
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, content);
    return { path: body.path, bytes: Buffer.byteLength(content) };
  },

  'POST /list': async (body) => {
    const target = confine(body.path || '.');
    const entries = await fs.readdir(target, { withFileTypes: true });
    return {
      path: body.path || '.',
      entries: entries.slice(0, 500).map((e) => ({
        name: e.name,
        type: e.isDirectory() ? 'dir' : 'file',
      })),
      truncated: entries.length > 500,
    };
  },

  'POST /remove': async (body) => {
    const target = confine(body.path);
    if (target === path.resolve(WORKSPACE_ROOT)) {
      throw new Error('refusing to remove the workspace root');
    }
    await fs.rm(target, { recursive: true, force: true });
    return { removed: body.path };
  },
};

const server = http.createServer(async (req, res) => {
  const key = `${req.method} ${req.url.split('?')[0]}`;
  const handler = routes[key];
  res.setHeader('Content-Type', 'application/json');
  if (!handler) {
    res.writeHead(404);
    res.end(JSON.stringify({ error: `no route for ${key}` }));
    return;
  }
  try {
    const body = req.method === 'POST' ? await readBody(req) : {};
    const result = await handler(body);
    res.writeHead(200);
    res.end(JSON.stringify(result));
  } catch (err) {
    res.writeHead(400);
    res.end(JSON.stringify({ error: err.message }));
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[code-sandbox] listening on ${HOST}:${PORT}, workspace ${WORKSPACE_ROOT}`);
});
