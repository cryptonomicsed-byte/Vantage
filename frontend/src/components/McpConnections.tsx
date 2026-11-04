import React, { useCallback, useEffect, useState } from 'react'
import { Plus, Trash2, RefreshCw, Wifi, WifiOff, Search, ExternalLink, Save } from 'lucide-react'

/**
 * Everything a user can attach to an agent:
 *   1. external MCP servers      (name, url, transport, optional auth headers)
 *   2. custom HTTP integrations  (own endpoint + actions + sealed secret)
 *   3. custom skills             (standing instructions injected each turn)
 *   4. Vantage's own catalogue   (by category, searchable)
 *   5. Composio toolkits
 */

type Server = {
  id: number
  name: string
  transport: string
  url?: string
  tool_count?: number
  last_error?: string | null
}

type CatalogCategory = { category: string; count: number }
type CatalogTool = { name: string; method: string; path: string; summary: string }
type ActionDraft = { name: string; method: string; path: string; description: string }

export default function McpConnections({ apiKey }: { apiKey: string }) {
  const h: Record<string, string> = apiKey ? { 'X-Agent-Key': apiKey } : {}
  const hj: Record<string, string> = { ...h, 'Content-Type': 'application/json' }

  const [servers, setServers] = useState<Server[]>([])
  const [cats, setCats] = useState<CatalogCategory[]>([])
  const [tools, setTools] = useState<CatalogTool[]>([])
  const [cat, setCat] = useState('')
  const [q, setQ] = useState('')
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [transport, setTransport] = useState('http')
  const [authHeader, setAuthHeader] = useState('')
  const [authValue, setAuthValue] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [composio, setComposio] = useState<any[]>([])

  const [integrations, setIntegrations] = useState<any[]>([])
  const [iName, setIName] = useState('')
  const [iBase, setIBase] = useState('')
  const [iHeader, setIHeader] = useState('')
  const [iSecret, setISecret] = useState('')
  const [actions, setActions] = useState<ActionDraft[]>([{ name: '', method: 'GET', path: '/', description: '' }])

  const [skills, setSkills] = useState<any[]>([])
  const [sName, setSName] = useState('')
  const [sDesc, setSDesc] = useState('')
  const [sInstr, setSInstr] = useState('')

  const loadServers = useCallback(async () => {
    try {
      const r = await fetch('/api/agents/me/mcp/servers', { headers: h })
      if (r.ok) {
        const d = await r.json()
        setServers(d.servers || d || [])
      }
    } catch { /* offline is not fatal here */ }
  }, [apiKey])

  const loadCatalog = useCallback(async () => {
    try {
      const r = await fetch('/api/mcp/catalog/categories', { headers: h })
      if (r.ok) setCats((await r.json()).categories || [])
    } catch { /* */ }
  }, [apiKey])

  const loadComposio = useCallback(async () => {
    try {
      const r = await fetch('/api/composio/connections', { headers: h })
      if (r.ok) setComposio((await r.json()).connections || [])
    } catch { /* */ }
  }, [apiKey])

  const loadCustom = useCallback(async () => {
    try {
      const [i, s] = await Promise.all([
        fetch('/api/agents/me/integrations', { headers: h }),
        fetch('/api/agents/me/skills', { headers: h }),
      ])
      if (i.ok) setIntegrations((await i.json()).integrations || [])
      if (s.ok) setSkills((await s.json()).skills || [])
    } catch { /* */ }
  }, [apiKey])

  useEffect(() => {
    loadServers(); loadCatalog(); loadComposio(); loadCustom()
  }, [loadServers, loadCatalog, loadComposio, loadCustom])

  async function search(category = cat, query = q) {
    setBusy('search')
    try {
      const p = new URLSearchParams()
      if (query) p.set('q', query)
      if (category) p.set('category', category)
      p.set('limit', '15')
      const r = await fetch(`/api/mcp/catalog/search?${p}`, { headers: h })
      if (r.ok) setTools((await r.json()).tools || [])
    } finally { setBusy('') }
  }

  async function addServer() {
    if (!name || !url) { setMsg('name and url are required'); return }
    setBusy('add')
    try {
      const headers: Record<string, string> = {}
      if (authHeader && authValue) headers[authHeader] = authValue
      const r = await fetch('/api/agents/me/mcp/servers', {
        method: 'POST',
        headers: hj,
        body: JSON.stringify({ name, transport, url, headers }),
      })
      const d = await r.json().catch(() => ({}))
      setMsg(r.ok ? `connected ${name}` : `failed: ${d.detail || r.status}`)
      if (r.ok) { setName(''); setUrl(''); setAuthHeader(''); setAuthValue(''); loadServers() }
    } finally { setBusy('') }
  }

  async function probe(id: number) {
    setBusy(`probe-${id}`)
    try {
      const r = await fetch(`/api/agents/me/mcp/servers/${id}/probe`, { method: 'POST', headers: h })
      const d = await r.json().catch(() => ({}))
      setMsg(d.ok ? `${d.count} tools` : `probe failed: ${d.error || r.status}`)
      loadServers()
    } finally { setBusy('') }
  }

  async function remove(id: number) {
    setBusy(`del-${id}`)
    try {
      await fetch(`/api/agents/me/mcp/servers/${id}`, { method: 'DELETE', headers: h })
      loadServers()
    } finally { setBusy('') }
  }

  async function connectToolkit(slug: string) {
    setBusy(`cx-${slug}`)
    try {
      const r = await fetch(`/api/composio/toolkits/${slug}/connect`, { method: 'POST', headers: h })
      const d = await r.json().catch(() => ({}))
      if (d.redirect_url || d.auth_url) window.open(d.redirect_url || d.auth_url, '_blank')
      else setMsg(d.detail || d.error || 'connect started')
      loadComposio()
    } finally { setBusy('') }
  }

  async function saveIntegration() {
    if (!iName || !iBase) { setMsg('integration needs a name and base url'); return }
    const clean = actions.filter(a => a.name && a.path)
    if (!clean.length) { setMsg('add at least one action with a name and path'); return }
    setBusy('int')
    try {
      const r = await fetch('/api/agents/me/integrations', {
        method: 'POST',
        headers: hj,
        body: JSON.stringify({
          name: iName,
          base_url: iBase,
          auth_header: iHeader,
          secret: iSecret,
          actions: clean.map(a => ({ ...a, params: [] })),
        }),
      })
      const d = await r.json().catch(() => ({}))
      setMsg(r.ok ? `integration ${d.slug} saved (${d.actions} actions)` : `failed: ${d.detail || r.status}`)
      if (r.ok) {
        setIName(''); setIBase(''); setIHeader(''); setISecret('')
        setActions([{ name: '', method: 'GET', path: '/', description: '' }])
        loadCustom()
      }
    } finally { setBusy('') }
  }

  async function saveSkill() {
    if (!sName || !sInstr) { setMsg('skill needs a name and instructions'); return }
    setBusy('skill')
    try {
      const r = await fetch('/api/agents/me/skills', {
        method: 'POST',
        headers: hj,
        body: JSON.stringify({ name: sName, description: sDesc, instructions: sInstr }),
      })
      const d = await r.json().catch(() => ({}))
      setMsg(r.ok ? `skill "${d.name}" saved` : `failed: ${d.detail || r.status}`)
      if (r.ok) { setSName(''); setSDesc(''); setSInstr(''); loadCustom() }
    } finally { setBusy('') }
  }

  async function del(path: string, id: number) {
    setBusy(`${path}-${id}`)
    try {
      await fetch(`${path}/${id}`, { method: 'DELETE', headers: h })
      loadCustom()
    } finally { setBusy('') }
  }

  const updAction = (idx: number, patch: Partial<ActionDraft>) =>
    setActions(a => a.map((x, i) => (i === idx ? { ...x, ...patch } : x)))

  return (
    <div>
      <h3 className="settings-section-title">MCP Servers — this agent&apos;s tools</h3>
      <div className="stat-card" style={{ marginBottom: 16 }}>
        {msg && <div style={{ fontSize: 12, color: 'var(--cyan)', marginBottom: 8 }}>{msg}</div>}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <input className="input" placeholder="name (e.g. heygen)" value={name}
                 onChange={e => setName(e.target.value)} style={{ flex: '0 0 170px' }} />
          <input className="input" placeholder="https://mcp.example.com/mcp" value={url}
                 onChange={e => setUrl(e.target.value)} style={{ flex: 1, minWidth: 230 }} />
          <select className="input" value={transport} onChange={e => setTransport(e.target.value)} style={{ flex: '0 0 92px' }}>
            <option value="http">http</option>
            <option value="sse">sse</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
          <input className="input" placeholder="auth header (optional, e.g. Authorization)" value={authHeader}
                 onChange={e => setAuthHeader(e.target.value)} style={{ flex: '0 0 250px' }} />
          <input className="input" placeholder="token / Bearer …" type="password" value={authValue}
                 onChange={e => setAuthValue(e.target.value)} style={{ flex: 1, minWidth: 170 }} />
          <button className="btn btn-primary btn-sm" onClick={addServer} disabled={busy === 'add'}>
            <Plus size={13} /> Connect
          </button>
        </div>

        {servers.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>No MCP servers connected yet.</div>
        )}
        {servers.map(s => (
          <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderTop: '1px solid var(--border)' }}>
            {s.last_error ? <WifiOff size={14} color="var(--red)" /> : <Wifi size={14} color="var(--green)" />}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13 }}>{s.name} <span style={{ color: 'var(--muted)', fontSize: 11 }}>({s.transport})</span></div>
              <div style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.url} {s.tool_count ? `· ${s.tool_count} tools` : ''} {s.last_error ? `· ${s.last_error.slice(0, 50)}` : ''}
              </div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => probe(s.id)} disabled={busy === `probe-${s.id}`}>
              <RefreshCw size={13} /> Probe
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => remove(s.id)} disabled={busy === `del-${s.id}`}>
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>

      <h3 className="settings-section-title">Custom integrations — your own API</h3>
      <div className="stat-card" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
          Point an integration at any HTTP API. Each action becomes a tool the agent can call.
          Secrets are sealed server-side and never returned.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <input className="input" placeholder="name (e.g. my-crm)" value={iName}
                 onChange={e => setIName(e.target.value)} style={{ flex: '0 0 160px' }} />
          <input className="input" placeholder="https://api.example.com" value={iBase}
                 onChange={e => setIBase(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          <input className="input" placeholder="auth header (e.g. X-Api-Key)" value={iHeader}
                 onChange={e => setIHeader(e.target.value)} style={{ flex: '0 0 210px' }} />
          <input className="input" placeholder="secret" type="password" value={iSecret}
                 onChange={e => setISecret(e.target.value)} style={{ flex: 1, minWidth: 170 }} />
        </div>

        {actions.map((a, i) => (
          <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
            <input className="input" placeholder="action name" value={a.name}
                   onChange={e => updAction(i, { name: e.target.value })} style={{ flex: '0 0 130px' }} />
            <select className="input" value={a.method} onChange={e => updAction(i, { method: e.target.value })} style={{ flex: '0 0 88px' }}>
              {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <input className="input" placeholder="/path" value={a.path}
                   onChange={e => updAction(i, { path: e.target.value })} style={{ flex: '0 0 160px' }} />
            <input className="input" placeholder="what it does" value={a.description}
                   onChange={e => updAction(i, { description: e.target.value })} style={{ flex: 1, minWidth: 150 }} />
            <button className="btn btn-ghost btn-sm" onClick={() => setActions(x => x.filter((_, j) => j !== i))}>
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button className="btn btn-ghost btn-sm"
                  onClick={() => setActions(x => [...x, { name: '', method: 'GET', path: '/', description: '' }])}>
            <Plus size={12} /> action
          </button>
          <button className="btn btn-primary btn-sm" onClick={saveIntegration} disabled={busy === 'int'}>
            <Save size={13} /> Save integration
          </button>
        </div>

        {integrations.map(it => (
          <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderTop: '1px solid var(--border)' }}>
            <div style={{ flex: 1, fontSize: 12 }}>
              <span style={{ fontFamily: 'monospace', color: 'var(--cyan)' }}>{it.slug}</span>{' '}
              <span style={{ color: 'var(--muted)' }}>{it.base_url}</span>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>
                {(it.actions || []).map((a: any) => `${a.method} ${a.path}`).join(' · ')}
                {it.has_secret ? ' · secret sealed' : ''}
              </div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => del('/api/agents/me/integrations', it.id)}>
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>

      <h3 className="settings-section-title">Custom skills — standing instructions</h3>
      <div className="stat-card" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
          Skills are injected into this agent&apos;s prompt on every turn, so it behaves the way you describe.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <input className="input" placeholder="skill name" value={sName}
                 onChange={e => setSName(e.target.value)} style={{ flex: '0 0 170px' }} />
          <input className="input" placeholder="what it is for (short)" value={sDesc}
                 onChange={e => setSDesc(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
        </div>
        <textarea className="input"
                  placeholder="Instructions the agent must follow, e.g. 'Always price in USDC and never quote a stock you have not fetched.'"
                  value={sInstr} onChange={e => setSInstr(e.target.value)}
                  style={{ width: '100%', minHeight: 72, marginBottom: 8, fontFamily: 'inherit', padding: 8 }} />
        <button className="btn btn-primary btn-sm" onClick={saveSkill} disabled={busy === 'skill'}>
          <Save size={13} /> Save skill
        </button>

        {skills.map(sk => (
          <div key={sk.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '7px 0', borderTop: '1px solid var(--border)' }}>
            <div style={{ flex: 1, fontSize: 12 }}>
              <div>{sk.name} <span style={{ color: 'var(--muted)', fontSize: 11 }}>{sk.description}</span></div>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>{(sk.instructions || '').slice(0, 140)}</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => del('/api/agents/me/skills', sk.id)}>
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>

      <h3 className="settings-section-title">Vantage tool catalogue — by category</h3>
      <div className="stat-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <select className="input" value={cat} onChange={e => { setCat(e.target.value); search(e.target.value, q) }} style={{ flex: '0 0 220px' }}>
            <option value="">All categories ({cats.reduce((a, c) => a + c.count, 0)})</option>
            {cats.map(c => <option key={c.category} value={c.category}>{c.category} ({c.count})</option>)}
          </select>
          <input className="input" placeholder="search tools…" value={q}
                 onChange={e => setQ(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') search() }}
                 style={{ flex: 1, minWidth: 200 }} />
          <button className="btn btn-primary btn-sm" onClick={() => search()} disabled={busy === 'search'}>
            <Search size={13} /> Search
          </button>
        </div>
        {tools.map(t => (
          <div key={t.name + t.path} style={{ padding: '6px 0', borderTop: '1px solid var(--border)', fontSize: 12 }}>
            <span style={{ color: 'var(--cyan)', fontFamily: 'monospace' }}>{t.method}</span>{' '}
            <span style={{ fontFamily: 'monospace' }}>{t.path}</span>
            <div style={{ color: 'var(--muted)', fontSize: 11 }}>{t.summary}</div>
          </div>
        ))}
        {tools.length === 0 && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Pick a category or search to list tools.</div>}
      </div>

      <h3 className="settings-section-title">Composio toolkits</h3>
      <div className="stat-card">
        {composio.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
            No toolkits connected. Connect one and its tools become available to this agent automatically.
          </div>
        ) : composio.map((c: any, i: number) => (
          <div key={i} style={{ fontSize: 12, padding: '4px 0' }}>
            {c.toolkitSlug || c.toolkit_slug || c.toolkit} — {c.status || 'connected'}
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          {['heygen', 'higgsfield', 'github', 'googlecalendar', 'slack', 'googlesheets', 'hackernews', 'text_to_pdf'].map(slug => (
            <button key={slug} className="btn btn-ghost btn-sm" onClick={() => connectToolkit(slug)} disabled={busy === `cx-${slug}`}>
              <ExternalLink size={12} /> {slug}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
