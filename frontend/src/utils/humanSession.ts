// Human accounts are a separate identity layer from the anonymous/agent-key
// "viewer" identity ensureAgentKey.ts sets up. A human logging in here gets
// NO agent access by itself — see backend/routers/agent_links.py; access to
// any specific agent only exists via an explicit, scoped grant. This module
// just tracks the human's own session token and small profile fields.

const SESSION_STORAGE = 'vantage_human_session'
const HUMAN_ID_STORAGE = 'vantage_human_id'
const DISPLAY_NAME_STORAGE = 'vantage_human_display_name'

export function hasHumanSession(): boolean {
  return !!localStorage.getItem(SESSION_STORAGE)
}

export function getHumanSession(): string | null {
  return localStorage.getItem(SESSION_STORAGE)
}

export function storeHumanSession(sessionToken: string, humanId: number, displayName: string): void {
  localStorage.setItem(SESSION_STORAGE, sessionToken)
  localStorage.setItem(HUMAN_ID_STORAGE, String(humanId))
  localStorage.setItem(DISPLAY_NAME_STORAGE, displayName || '')
}

export function getHumanDisplayName(): string {
  return localStorage.getItem(DISPLAY_NAME_STORAGE) || ''
}

export async function registerHuman(email: string, password: string, displayName: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const r = await fetch('/api/humans/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, display_name: displayName }),
    })
    const data = await r.json()
    if (!r.ok) return { ok: false, error: data.detail || 'Registration failed' }
    storeHumanSession(data.session_token, data.human_id, data.display_name)
    return { ok: true }
  } catch {
    return { ok: false, error: 'Network error' }
  }
}

export async function loginHuman(email: string, password: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const r = await fetch('/api/humans/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    const data = await r.json()
    if (!r.ok) return { ok: false, error: data.detail || 'Login failed' }
    storeHumanSession(data.session_token, data.human_id, data.display_name)
    return { ok: true }
  } catch {
    return { ok: false, error: 'Network error' }
  }
}

export async function logoutHuman(): Promise<void> {
  const token = getHumanSession()
  if (token) {
    try {
      await fetch('/api/humans/logout', {
        method: 'POST',
        headers: { 'X-Human-Session': token },
      })
    } catch {
      // best-effort — clear local state regardless
    }
  }
  localStorage.removeItem(SESSION_STORAGE)
  localStorage.removeItem(HUMAN_ID_STORAGE)
  localStorage.removeItem(DISPLAY_NAME_STORAGE)
}

export interface LinkedAgent {
  agent_id: number
  name: string
  avatar_url: string
  scopes: string[]
  granted_by: string
  created_at: string
}

export async function listMyAgents(): Promise<LinkedAgent[]> {
  const token = getHumanSession()
  if (!token) return []
  try {
    const r = await fetch('/api/humans/me/agents', { headers: { 'X-Human-Session': token } })
    if (!r.ok) return []
    return await r.json()
  } catch {
    return []
  }
}

export async function linkAgent(agentKey: string): Promise<{ ok: boolean; error?: string }> {
  const token = getHumanSession()
  if (!token) return { ok: false, error: 'Not logged in' }
  try {
    const r = await fetch('/api/humans/me/agents/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Human-Session': token },
      body: JSON.stringify({ agent_key: agentKey }),
    })
    const data = await r.json()
    if (!r.ok) return { ok: false, error: data.detail || 'Link failed' }
    return { ok: true }
  } catch {
    return { ok: false, error: 'Network error' }
  }
}
