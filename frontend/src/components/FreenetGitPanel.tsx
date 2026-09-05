import React, { useEffect, useRef, useState } from 'react'

interface RepoInfo {
  repo_name: string
  head_commit?: string | null
  bundle_hash?: string | null
  pushed_at?: string | null
}

function truncate8(s?: string | null): string {
  if (!s) return '—'
  return s.length <= 8 ? s : s.slice(0, 8)
}

function formatDate(ts?: string | null): string {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString() } catch { return ts }
}

export default function FreenetGitPanel({ guildSlug }: { guildSlug: string }) {
  const [repos, setRepos] = useState<RepoInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [offline, setOffline] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [pushError, setPushError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const apiKey = localStorage.getItem('vantage_api_key') || ''
  const authHeaders: Record<string, string> = apiKey ? { 'X-Agent-Key': apiKey } : {}

  useEffect(() => {
    setLoading(true)
    fetch(`/api/freenet/git/${guildSlug}`)
      .then(r => {
        if (!r.ok) { setOffline(true); return null }
        return r.json()
      })
      .then(d => {
        if (d) {
          setRepos(Array.isArray(d) ? d : [])
          setOffline(false)
        }
      })
      .catch(() => setOffline(true))
      .finally(() => setLoading(false))
  }, [guildSlug])

  function handlePushClick() {
    setPushError(null)
    fileInputRef.current?.click()
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return

    const repoName = window.prompt('Repository name (e.g. my-project):')
    if (!repoName) return

    setPushing(true)
    setPushError(null)
    try {
      const buf = await file.arrayBuffer()
      const bytes = new Uint8Array(buf)
      // encode as base64
      let binary = ''
      for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i])
      const bundle_b64 = btoa(binary)

      const r = await fetch(`/api/freenet/git/${guildSlug}/${encodeURIComponent(repoName)}`, {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ bundle_b64 }),
      })
      if (!r.ok) {
        const txt = await r.text().catch(() => `HTTP ${r.status}`)
        setPushError(txt || `HTTP ${r.status}`)
        return
      }
      const result = await r.json()
      // Add or update repo in list
      const newRepo: RepoInfo = {
        repo_name: repoName,
        head_commit: null,
        bundle_hash: result.bundle_hash,
        pushed_at: result.pushed_at,
      }
      setRepos(prev => {
        const idx = prev.findIndex(r => r.repo_name === repoName)
        if (idx >= 0) { const next = [...prev]; next[idx] = newRepo; return next }
        return [...prev, newRepo]
      })
    } catch (err: unknown) {
      setPushError(err instanceof Error ? err.message : 'Push failed')
    } finally {
      setPushing(false)
      // Reset file input so the same file can be re-selected
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13 }}>Loading Freenet Git repos…</div>
    )
  }

  if (offline) {
    return (
      <div style={{ padding: 24 }}>
        <div style={{
          padding: '12px 16px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          borderRadius: 8, fontSize: 13, color: '#fca5a5',
        }}>
          Freenet node offline — connect a local node to use Git replication
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>
            {repos.length > 0 ? 'Freenet Phase F6 — Git replication active' : 'No repos pushed yet'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
            Git bundles stored as Freenet contract state · guild: <code style={{ color: 'var(--cyan)' }}>{guildSlug}</code>
          </div>
        </div>
        <button
          className="btn btn-sm btn-primary"
          onClick={handlePushClick}
          disabled={pushing}
          style={{ fontSize: 11 }}
        >
          {pushing ? 'Pushing…' : '+ Push Bundle'}
        </button>
      </div>

      {pushError && (
        <div style={{ fontSize: 11, color: '#ef4444', padding: '8px 10px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 6 }}>
          {pushError}
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".bundle"
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      {/* Repo list */}
      {repos.length === 0 ? (
        <div style={{ color: 'var(--muted)', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
          No repos pushed yet. Click "Push Bundle" to upload a git bundle.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {repos.map(repo => (
            <div
              key={repo.repo_name}
              style={{
                background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
                borderRadius: 8, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {repo.repo_name}
                </div>
                <div style={{ display: 'flex', gap: 10, fontSize: 10, color: 'var(--muted)', marginTop: 3, flexWrap: 'wrap' }}>
                  {repo.head_commit && (
                    <span>commit: <code style={{ color: 'var(--cyan)' }}>{truncate8(repo.head_commit)}</code></span>
                  )}
                  <span>hash: <code style={{ color: 'rgba(255,255,255,0.4)' }}>{truncate8(repo.bundle_hash)}</code></span>
                  <span>pushed: {formatDate(repo.pushed_at)}</span>
                </div>
              </div>
              <a
                href={`/api/freenet/git/${guildSlug}/${encodeURIComponent(repo.repo_name)}/download`}
                download={`${repo.repo_name}.bundle`}
                style={{ fontSize: 11, color: 'var(--cyan)', textDecoration: 'none', flexShrink: 0 }}
              >
                ↓ Download
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
