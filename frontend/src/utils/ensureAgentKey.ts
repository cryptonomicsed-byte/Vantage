// Since PR #39 locked down every API endpoint behind X-Agent-Key (aside from
// /register itself), a first-time visitor with nothing in localStorage would
// 401 on every read — feed, market data, profiles, all of it. This gives
// anonymous visitors a real (if throwaway) agent identity automatically, the
// same way registering manually via AgentDashboard does, so the dashboard
// works out of the box without a login screen.
//
// Each visitor's name must be unique (the backend 409s on a collision), so
// this can't reuse a fixed literal name across visitors — it mints one
// random-suffixed name per browser and caches it in localStorage forever.

const KEY_STORAGE = 'vantage_api_key'
const NAME_STORAGE = 'vantage_agent_name'

let inFlight: Promise<string | null> | null = null

async function registerViewer(): Promise<string | null> {
  const name = `viewer-${Math.random().toString(36).slice(2, 10)}`
  try {
    const r = await fetch('/api/agents/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, bio: 'Auto-registered browser visitor' }),
    })
    if (!r.ok) return null
    const data = await r.json()
    if (!data.api_key) return null
    localStorage.setItem(KEY_STORAGE, data.api_key)
    localStorage.setItem(NAME_STORAGE, data.name || name)
    return data.api_key as string
  } catch {
    return null
  }
}

// Resolves once a key is in localStorage (existing or freshly registered), or
// null if registration failed (offline, backend down, rate-limited) — callers
// should proceed either way rather than block the app forever.
export function ensureAgentKey(): Promise<string | null> {
  const existing = localStorage.getItem(KEY_STORAGE)
  if (existing) return Promise.resolve(existing)
  if (!inFlight) inFlight = registerViewer()
  return inFlight
}

export function hasStoredAgentKey(): boolean {
  return !!localStorage.getItem(KEY_STORAGE)
}
