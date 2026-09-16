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

/**
 * The one shared bare mirror for a given https repo URL, used by /worktree
 * so N agents cloning the same repo pay for the network fetch once. Lives
 * under _shared/, outside every agent's own confine()d directory -- Vantage
 * never lets a caller name a path outside its own agent-{id}-{name}/ prefix
 * (see _scoped() in workspace.py), so this is reachable only through
 * /worktree and /worktree/remove, never through /read, /write, /list or the
 * plain /clone.
 */
function sharedRepoDir(repoUrl) {
  const slug = repoUrl
    .replace(/^https?:\/\//, '')
    .replace(/\.git$/, '')
    .replace(/[^a-zA-Z0-9_-]+/g, '_');
  return confine(path.join('_shared', `${slug}.git`));
}

/**
 * Is `target` a worktree currently registered to this shared mirror?
 *
 * git refuses to add a worktree over an existing directory, so a second task
 * claiming the same dir used to fail with exit 128 and leave the PREVIOUS
 * task's branch checked out. Before reusing a path we have to know whether it
 * is ours to unregister, or somebody's actual files.
 */
async function isRegisteredWorktree(shared, target, timeoutMs) {
  const res = await runCommand({
    command: 'git',
    args: ['--git-dir', shared, 'worktree', 'list', '--porcelain'],
    cwd: confine('.'),
    timeoutMs: Math.min(timeoutMs || 30_000, MAX_TIMEOUT_MS),
  });
  if (res.exit_code !== 0) return false;
  const want = path.resolve(target);
  return String(res.stdout || '')
    .split('\n')
    .filter((line) => line.startsWith('worktree '))
    .map((line) => path.resolve(line.slice('worktree '.length).trim()))
    .some((p) => p === want);
}

async function exists(target) {
  try {
    await fs.access(target);
    return true;
  } catch {
    return false;
  }
}

// Environment variables inherited from the container.
//
// Commands get a deliberately minimal environment rather than the container's,
// so nothing configured at the container level leaks into agent-run code. But
// a blanket strip breaks the deployments that need it most: behind a corporate
// proxy or a private CA, `git clone` fails with a TLS or connectivity error and
// nothing pointing at the cause. Found by smoke-testing the real container —
// the clone worked only when these were passed explicitly through the API.
//
// Only network reachability and TLS trust are inherited. Credentials are not:
// anything resembling a token or key still has to be passed per-call.
const INHERITED_ENV = [
  'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
  'http_proxy', 'https_proxy', 'no_proxy',
  'GIT_SSL_CAINFO', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
  'NODE_EXTRA_CA_CERTS', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
];

function inheritedEnv() {
  const out = {};
  for (const key of INHERITED_ENV) {
    if (process.env[key]) out[key] = process.env[key];
  }
  return out;
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
        ...inheritedEnv(),
        // Caller-supplied last, so a per-call override wins over the container.
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

  // POST /worktree and /worktree/remove -- the multi-agent form of /clone.
  //
  // Vantage already confines every agent to its own directory (dir is always
  // caller-agent-prefixed by workspace.py, never by the caller itself), so
  // two agents cloning the same repo were never at risk of clobbering each
  // other's files -- but they were each paying for a full clone of the same
  // history, and there was no way to tell "agent 3's checkout of task 7" from
  // "agent 3's checkout of task 8" apart from the directory name. A worktree
  // fixes both: one shared bare mirror of the repo's (public, https-only)
  // object store under _shared/, and a cheap `git worktree add` per
  // agent+task pointing a dedicated branch at it. Sharing the object store is
  // safe -- it's exactly the history `git clone` would hand any caller of the
  // same https URL anyway, nothing agent-specific lives in it.
  'POST /worktree': async (body) => {
    if (!body.repo_url) throw new Error('repo_url is required');
    if (!/^https:\/\//.test(body.repo_url)) {
      throw new Error('repo_url must be an https:// URL');
    }
    if (!body.branch) throw new Error('branch is required');
    const dir = body.dir || body.repo_url.replace(/\.git$/, '').split('/').pop();
    const target = confine(dir);
    const shared = sharedRepoDir(body.repo_url);
    const timeoutMs = Math.min(parseInt(body.timeout_ms || 300_000, 10), MAX_TIMEOUT_MS);

    let result;
    if (await exists(shared)) {
      // Already mirrored by some earlier clone/worktree call (this agent's or
      // another's) -- refresh it rather than paying for a second full clone.
      result = await runCommand({
        command: 'git', args: ['--git-dir', shared, 'fetch', 'origin', '--prune'],
        cwd: confine('.'), timeoutMs,
      });
    } else {
      await fs.mkdir(path.dirname(shared), { recursive: true });
      result = await runCommand({
        command: 'git', args: ['clone', '--bare', body.repo_url, shared],
        cwd: confine('.'), timeoutMs,
      });
    }
    if (result.exit_code !== 0) {
      return { ...result, dir: path.relative(confine('.'), target) || '.', branch: body.branch };
    }

    const dirRelative = path.relative(confine('.'), target) || '.';

    // git refuses to add a worktree over an existing directory. Reusing a dir
    // for a NEW task used to fail here with exit 128 -- and the failed call
    // still reported body.branch, so the caller believed it was on the new
    // branch while the tree held the previous task's checkout. Reconcile the
    // path before adding.
    let relocated = false;
    if (await exists(target)) {
      if (!(await isRegisteredWorktree(shared, target, timeoutMs))) {
        // Not ours: a plain /clone, or files this agent wrote by hand.
        // Deleting it could destroy uncommitted work, so refuse and say so.
        return {
          exit_code: 128,
          stdout: '',
          stderr:
            `${dirRelative} already exists and is not a worktree of this mirror. ` +
            'Refusing to delete it -- remove it first if you did not mean to keep it.',
          dir: dirRelative,
          branch: body.branch,
          relocated: false,
          refused: true,
        };
      }
      await runCommand({
        command: 'git', args: ['--git-dir', shared, 'worktree', 'remove', '--force', target],
        cwd: confine('.'), timeoutMs,
      });
      await runCommand({
        command: 'git', args: ['--git-dir', shared, 'worktree', 'prune'],
        cwd: confine('.'), timeoutMs: 30_000,
      });
      // Same task re-claiming its own dir, or a new task taking it over.
      // Nothing here is committed yet, but it is all in the shared object
      // store, so dropping the directory loses no history.
      if (await exists(target)) await fs.rm(target, { recursive: true, force: true });
      relocated = true;
    }

    const branchExists = await runCommand({
      command: 'git', args: ['--git-dir', shared, 'rev-parse', '--verify', '--quiet', body.branch],
      cwd: confine('.'), timeoutMs: 10_000,
    });
    const worktreeArgs = branchExists.exit_code === 0
      ? ['--git-dir', shared, 'worktree', 'add', '--force', target, body.branch]
      : ['--git-dir', shared, 'worktree', 'add', '--force', '-b', body.branch, target, 'HEAD'];

    result = await runCommand({ command: 'git', args: worktreeArgs, cwd: confine('.'), timeoutMs });
    if (result.exit_code !== 0) {
      return { ...result, dir: dirRelative, branch: body.branch, relocated };
    }

    // Report the branch that is ACTUALLY checked out, not the one requested.
    const head = await runCommand({
      command: 'git', args: ['-C', target, 'rev-parse', '--abbrev-ref', 'HEAD'],
      cwd: confine('.'), timeoutMs: 10_000,
    });
    const actualBranch = head.exit_code === 0 ? String(head.stdout).trim() : body.branch;
    return { ...result, dir: dirRelative, branch: actualBranch, relocated };
  },

  // POST /worktree/prune -- explicit orphan cleanup, for task close.
  // The spec is that a closed task leaves no worktree behind; because a
  // worktree is only unregistered by an explicit call, that has to be
  // reachable rather than assumed.
  'POST /worktree/prune': async (body) => {
    if (!body.repo_url) throw new Error('repo_url is required');
    const shared = sharedRepoDir(body.repo_url);
    if (!(await exists(shared))) {
      return { pruned: false, reason: 'no shared mirror for that repo_url' };
    }
    const prune = await runCommand({
      command: 'git', args: ['--git-dir', shared, 'worktree', 'prune', '--expire', 'now'],
      cwd: confine('.'), timeoutMs: 60_000,
    });
    const list = await runCommand({
      command: 'git', args: ['--git-dir', shared, 'worktree', 'list', '--porcelain'],
      cwd: confine('.'), timeoutMs: 30_000,
    });
    return {
      pruned: prune.exit_code === 0,
      stdout: prune.stdout,
      stderr: prune.stderr,
      worktrees: String(list.stdout || '')
        .split('\n')
        .filter((line) => line.startsWith('worktree '))
        .map((line) => line.slice('worktree '.length).trim()),
    };
  },

  'POST /worktree/remove': async (body) => {
    if (!body.repo_url) throw new Error('repo_url is required');
    if (!body.dir) throw new Error('dir is required');
    const shared = sharedRepoDir(body.repo_url);
    const target = confine(body.dir);
    const result = await runCommand({
      command: 'git', args: ['--git-dir', shared, 'worktree', 'remove', '--force', target],
      cwd: confine('.'), timeoutMs: 60_000,
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
