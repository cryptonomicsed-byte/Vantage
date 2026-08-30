import React, { useEffect, useState } from 'react'
import { Rocket, AlertTriangle, Upload, CheckCircle2 } from 'lucide-react'

interface Wallet {
  id: number
  label: string
  chain: string
  address: string
}

const cardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid rgba(255,255,255,0.05)',
  borderRadius: 8,
  padding: 14,
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  background: 'rgba(255,255,255,0.03)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: 6,
  padding: '8px 10px',
  color: 'inherit',
  fontSize: 12,
  marginBottom: 10,
}

const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--muted)', marginBottom: 4, display: 'block' }

// Real deployment, real SOL. Mirrors backend/routers/pumpfun.py's
// create-token flow exactly: pin image -> pin metadata -> dry_run preview
// -> explicit second submit with dry_run=false to actually spend SOL.
// No step here silently skips the dry-run preview -- the "Launch for real"
// button only appears after a successful dry-run response.
export default function LaunchTokenPanel() {
  const apiKey = () => localStorage.getItem('vantage_api_key') || ''

  const [config, setConfig] = useState<{ ipfs_ready: boolean; note: string } | null>(null)
  const [wallets, setWallets] = useState<Wallet[]>([])
  const [walletId, setWalletId] = useState<number | ''>('')

  const [name, setName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [description, setDescription] = useState('')
  const [twitter, setTwitter] = useState('')
  const [telegram, setTelegram] = useState('')
  const [website, setWebsite] = useState('')
  const [devBuySol, setDevBuySol] = useState('0')

  const [imageFile, setImageFile] = useState<File | null>(null)
  const [imageUrl, setImageUrl] = useState('')
  const [uploading, setUploading] = useState(false)

  const [preview, setPreview] = useState<any>(null)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const k = apiKey()
    fetch('/api/intel/pumpfun/create/config', { headers: { 'X-Agent-Key': k } })
      .then(r => r.json()).then(setConfig).catch(() => {})
    fetch('/api/trading/wallets', { headers: { 'X-Agent-Key': k } })
      .then(r => r.json())
      .then(d => setWallets((d.wallets || d || []).filter((w: Wallet) => w.chain === 'solana')))
      .catch(() => {})
  }, [])

  async function doUploadImage() {
    if (!imageFile) return
    setUploading(true); setError('')
    try {
      const fd = new FormData()
      fd.append('image', imageFile)
      const r = await fetch('/api/intel/pumpfun/create/upload-image', {
        method: 'POST', headers: { 'X-Agent-Key': apiKey() }, body: fd,
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Image upload failed')
      setImageUrl(d.url)
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setUploading(false)
    }
  }

  function buildForm() {
    const fd = new FormData()
    fd.append('wallet_id', String(walletId))
    fd.append('name', name)
    fd.append('symbol', symbol)
    fd.append('image_url', imageUrl)
    fd.append('description', description)
    fd.append('twitter', twitter)
    fd.append('telegram', telegram)
    fd.append('website', website)
    fd.append('dev_buy_sol', devBuySol || '0')
    return fd
  }

  async function runDryRun() {
    setBusy(true); setError(''); setPreview(null); setResult(null)
    try {
      const fd = buildForm()
      fd.append('dry_run', 'true')
      const r = await fetch('/api/intel/pumpfun/create', {
        method: 'POST', headers: { 'X-Agent-Key': apiKey() }, body: fd,
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Preview failed')
      setPreview(d)
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  async function runLive() {
    if (!confirm(
      `This spends real SOL (~${preview?.estimated_cost_sol ?? '?'} SOL) to create "${name}" (${symbol}) ` +
      `on pump.fun mainnet. This cannot be undone. Continue?`
    )) return
    setBusy(true); setError('')
    try {
      const fd = buildForm()
      fd.append('dry_run', 'false')
      const r = await fetch('/api/intel/pumpfun/create', {
        method: 'POST', headers: { 'X-Agent-Key': apiKey() }, body: fd,
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Launch failed')
      setResult(d)
      setPreview(null)
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const canDryRun = !!(walletId && name && symbol && imageUrl)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Rocket size={20} color="#f59e0b" />
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Launch a Token</h2>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>Real pump.fun deployment</span>
      </div>

      {config && !config.ipfs_ready && (
        <div style={{ ...cardStyle, borderColor: 'rgba(239,68,68,0.3)', marginBottom: 16, display: 'flex', gap: 8 }}>
          <AlertTriangle size={14} color="#ef4444" style={{ flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: 12, color: '#ef4444' }}>{config.note}</div>
        </div>
      )}

      <div style={cardStyle}>
        <label style={labelStyle}>Wallet (must be a PumpPortal Lightning wallet)</label>
        <select style={inputStyle} value={walletId} onChange={e => setWalletId(Number(e.target.value) || '')}>
          <option value="">Select wallet…</option>
          {wallets.map(w => (
            <option key={w.id} value={w.id}>{w.label} — {w.address.slice(0, 6)}…{w.address.slice(-4)}</option>
          ))}
        </select>
        {wallets.length === 0 && (
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
            No Solana wallets found. Generate one via POST /api/trading/wallets/generate with system="pumpportal" first.
          </div>
        )}

        <label style={labelStyle}>Name</label>
        <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Omo Koda Mesh" />

        <label style={labelStyle}>Ticker (max 10 chars)</label>
        <input style={inputStyle} value={symbol} maxLength={10} onChange={e => setSymbol(e.target.value.toUpperCase())} placeholder="e.g. OKMESH" />

        <label style={labelStyle}>Image</label>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <input type="file" accept="image/*" onChange={e => setImageFile(e.target.files?.[0] || null)} style={{ fontSize: 12 }} />
          <button
            onClick={doUploadImage}
            disabled={!imageFile || uploading || !config?.ipfs_ready}
            style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, padding: '4px 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.1)', background: 'transparent', color: 'inherit', cursor: 'pointer' }}
          >
            <Upload size={12} /> {uploading ? 'Pinning…' : 'Pin to IPFS'}
          </button>
          {imageUrl && <CheckCircle2 size={16} color="#22c55e" />}
        </div>
        {imageUrl && <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 10, wordBreak: 'break-all' }}>{imageUrl}</div>}

        <label style={labelStyle}>Description</label>
        <textarea style={{ ...inputStyle, minHeight: 60 }} value={description} onChange={e => setDescription(e.target.value)} />

        <label style={labelStyle}>Twitter / X (optional)</label>
        <input style={inputStyle} value={twitter} onChange={e => setTwitter(e.target.value)} placeholder="https://x.com/…" />

        <label style={labelStyle}>Telegram (optional)</label>
        <input style={inputStyle} value={telegram} onChange={e => setTelegram(e.target.value)} placeholder="https://t.me/…" />

        <label style={labelStyle}>Website (optional)</label>
        <input style={inputStyle} value={website} onChange={e => setWebsite(e.target.value)} placeholder="https://…" />

        <label style={labelStyle}>Initial dev buy (SOL, 0 is valid)</label>
        <input style={inputStyle} type="number" min="0" step="0.001" value={devBuySol} onChange={e => setDevBuySol(e.target.value)} />

        {error && <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 10 }}>{error}</div>}

        <button
          onClick={runDryRun}
          disabled={!canDryRun || busy}
          style={{ width: '100%', padding: '10px', borderRadius: 6, border: 'none', background: canDryRun ? '#f59e0b' : 'rgba(255,255,255,0.06)', color: canDryRun ? '#000' : 'var(--muted)', fontWeight: 600, fontSize: 13, cursor: canDryRun ? 'pointer' : 'not-allowed' }}
        >
          {busy ? 'Working…' : 'Preview (dry run, no SOL spent)'}
        </button>
      </div>

      {preview && (
        <div style={{ ...cardStyle, marginTop: 12, borderColor: 'rgba(34,197,94,0.25)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: '#22c55e' }}>Dry-run preview</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>Metadata URI: {preview.metadata_uri}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
            Estimated cost: ~{preview.estimated_cost_sol} SOL (rent + dev buy; PumpPortal's trading fee on the dev buy is additional)
          </div>
          <button
            onClick={runLive}
            disabled={busy}
            style={{ width: '100%', padding: '10px', borderRadius: 6, border: '1px solid #ef4444', background: 'transparent', color: '#ef4444', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}
          >
            {busy ? 'Submitting…' : `Launch for real — spends ~${preview.estimated_cost_sol} SOL`}
          </button>
        </div>
      )}

      {result && (
        <div style={{ ...cardStyle, marginTop: 12, borderColor: 'rgba(34,197,94,0.4)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#22c55e', marginBottom: 6 }}>Launched</div>
          <div style={{ fontSize: 11, marginBottom: 4 }}>
            <a href={result.explorer_url} target="_blank" rel="noreferrer" style={{ color: '#22d3ee' }}>{result.signature}</a>
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>{result.metadata_uri}</div>
        </div>
      )}
    </div>
  )
}
