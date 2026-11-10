import React, { useEffect, useState } from 'react'
import { authHeaders } from './feed'

const TYPE_LABEL: Record<string, string> = { solana: 'Solana wallet', polymarket: 'Polymarket / EVM wallet', twitter: 'X / Twitter', telegram: 'Telegram', bitcoin: 'Bitcoin' }

// Add Tracker: paste anything, see a read-only preview (per-chain validation,
// what actually lives at that address, whether it's already tracked, and the
// exact row that will be written) before submitting.
export default function AddTrackerPanel() {
  const [input, setInput] = useState('')
  const [label, setLabel] = useState('')
  const [type, setType] = useState<string>('')
  const [preview, setPreview] = useState<any>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewErr, setPreviewErr] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setResult(null); setError('')
    const raw = input.trim()
    if (!raw) { setPreview(null); setPreviewErr(''); return }
    const t = setTimeout(async () => {
      setPreviewing(true); setPreviewErr('')
      try {
        const r = await fetch('/api/intel/ingest/preview', {
          method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ input: raw, label: label.trim(), type: type || undefined }),
        })
        const d = await r.json().catch(() => null)
        if (r.ok) setPreview(d)
        else { setPreview(null); setPreviewErr(r.status === 401 ? 'Sign in with an agent key to preview and add trackers.' : (typeof d?.detail === 'string' ? d.detail : `HTTP ${r.status}`)) }
      } catch (e: any) { setPreview(null); setPreviewErr(e?.message || 'preview unavailable') }
      setPreviewing(false)
    }, 400)
    return () => clearTimeout(t)
  }, [input, label, type])

  async function submit() {
    setSubmitting(true); setError(''); setResult(null)
    try {
      const r = await fetch('/api/intel/ingest', {
        method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ input: input.trim(), label: label.trim(), type: type || preview?.type || undefined }),
      })
      const d = await r.json().catch(() => null)
      if (r.ok) setResult(d)
      else setError(typeof d?.detail === 'string' ? d.detail : (d?.detail?.message || `HTTP ${r.status}`))
    } catch { setError('Request failed — check connection') }
    setSubmitting(false)
  }

  const p = preview
  const ok = !!p?.can_submit
  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ color: 'var(--muted)', marginBottom: 12, fontSize: 13 }}>
        Paste a Solana wallet, a Polymarket/EVM wallet, an X/Twitter or Telegram username or link. A preview shows what will be tracked before anything is saved.
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <input className="ares-input" placeholder="Link, handle or address…" value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && ok && !submitting && submit()} style={{ minWidth: 320, flex: 1, fontFamily: 'monospace' }} />
        <input className="ares-input" placeholder="Label (optional)" value={label} onChange={e => setLabel(e.target.value)} style={{ maxWidth: 180 }} />
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={!ok || submitting}>{submitting ? 'Adding…' : 'Add'}</button>
      </div>
      {previewing && <div style={{ fontSize: 11, color: 'var(--muted)' }}>checking…</div>}
      {previewErr && <div style={{ fontSize: 12, color: '#f59e0b' }}>{previewErr}</div>}
      {p && (
        <div style={{ padding: 12, border: `1px solid ${ok ? '#39ff1455' : '#f59e0b55'}`, borderRadius: 8, marginTop: 8, fontSize: 12 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
            <b>{TYPE_LABEL[p.type] || p.type || 'Unrecognised'}</b>
            <span style={{ color: p.valid ? '#39ff14' : '#ff2d4a' }}>{p.valid ? '✓' : '✗'} {p.reason}</span>
            {p.already_tracked && <span style={{ color: '#f59e0b' }}>already tracked{p.existing?.since ? ` since ${p.existing.since}` : ''}{p.existing?.label ? ` as “${p.existing.label}”` : ''}</span>}
          </div>
          {p.candidates && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
              {Object.entries(p.candidates).map(([k, v]: [string, any]) => (
                <button key={k} className={`btn btn-sm ${type === k ? 'btn-primary' : 'btn-ghost'}`} disabled={!v.valid} title={v.reason} onClick={() => setType(k)}>
                  {TYPE_LABEL[k]}{v.valid ? '' : ' (invalid)'}</button>
              ))}
            </div>
          )}
          {p.account && <div style={{ color: 'var(--muted)' }}>On-chain: {p.account.detail}{p.account.sol != null ? ` · ${p.account.sol} SOL` : ''}</div>}
          {p.labels?.length > 0 && <div style={{ color: 'var(--muted)' }}>Known labels: {p.labels.join(', ')}</div>}
          {(p.warnings || []).map((w: string, i: number) => <div key={i} style={{ color: '#f59e0b' }}>⚠ {w}</div>)}
          {p.will_track && (
            <div style={{ marginTop: 6 }}>
              <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Will track</div>
              <table className="ares-table" style={{ fontSize: 11 }}><tbody>
                {Object.entries(p.will_track).map(([k, v]) => <tr key={k}><td style={{ color: 'var(--muted)', width: 110 }}>{k}</td><td style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{String(v)}</td></tr>)}
              </tbody></table>
            </div>
          )}
        </div>
      )}
      {error && <div style={{ color: 'var(--danger)', marginTop: 12, fontSize: 13 }}>{error}</div>}
      {result && (
        <div style={{ padding: 12, border: '1px solid var(--border)', borderRadius: 8, marginTop: 12 }}>
          <div style={{ color: '#39ff14', fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Added → {result.tracker}</div>
          <pre style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(result.result ?? { wallet: result.wallet, output: result.output }, null, 2)}</pre>
          {result.degen_watchlist_note && <div style={{ marginTop: 8, fontSize: 11, color: '#ffaa00' }}>{result.degen_watchlist_note}</div>}
        </div>
      )}
    </div>
  )
}
