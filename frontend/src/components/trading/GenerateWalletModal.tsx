import React, { useState } from 'react'
import { X } from 'lucide-react'

// ── Generate Wallet — POST /api/trading/wallets/generate, real and working
// on the backend (both 'bip39' — the vanity-real/CloakSeed-family
// multi-chain generator on port 8778 — and 'bipon39' — the Yoruba Ifá
// wordlist system, /opt/ares/ares_bip39.py + the `bipon39` CLI). Shared
// between ExecutionPanel (terminal) and Portfolio (Wallets tab) — takes
// tradingApi as a prop rather than pulling from TradingStore context,
// since Portfolio has its own standalone fetch helper, not the context one.
//
// The mnemonic is shown here exactly once, straight from the API response
// — the backend never stores it, only the encrypted private key. CloakSeed
// itself (vanity-cloakseed) is a separate, standalone client-side app for
// turning a real seed phrase into a stealth cipher for physical backup —
// it has no server API, so it can't be called from here, but any mnemonic
// generated below is a real BIP-39-shaped phrase you can paste into it
// yourself if you want that extra layer.
export default function GenerateWalletModal({
  onClose, onCreated, tradingApi,
}: {
  onClose: () => void
  onCreated: () => void
  tradingApi: (path: string, opts?: RequestInit) => Promise<Response>
}) {
  const [system, setSystem] = useState<'bip39' | 'bipon39'>('bip39')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<any>(null)

  async function generate() {
    setBusy(true); setError('')
    try {
      const r = await tradingApi('/wallets/generate', {
        method: 'POST',
        body: JSON.stringify({ system, chain: 'solana', label: label || undefined }),
      })
      const d = await r.json()
      if (r.ok) { setResult(d); onCreated() }
      else setError(d.detail || 'Generation failed')
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setBusy(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 'min(420px, 92vw)', background: 'rgba(10,10,20,0.98)', border: '1px solid rgba(138,75,255,0.3)', borderRadius: 14, padding: '18px 20px', maxHeight: '85vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: '#fff' }}>Generate Wallet</span>
          <button onClick={onClose} className="btn btn-ghost btn-sm"><X size={14} /></button>
        </div>

        {!result ? (
          <>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)', marginBottom: 10 }}>
              Creates a real Solana wallet, encrypts the key at rest, and shows the recovery mnemonic once. Save it now — it is never shown again or stored in plaintext.
            </div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
              <button onClick={() => setSystem('bip39')}
                style={{ flex: 1, padding: '8px 0', borderRadius: 6, border: '1px solid ' + (system === 'bip39' ? 'rgba(138,75,255,0.5)' : 'rgba(255,255,255,.1)'), background: system === 'bip39' ? 'rgba(138,75,255,0.15)' : 'transparent', color: system === 'bip39' ? '#c4b5fd' : '#9ca3af', fontSize: 12, cursor: 'pointer' }}>
                Standard (BIP-39)
              </button>
              <button onClick={() => setSystem('bipon39')}
                style={{ flex: 1, padding: '8px 0', borderRadius: 6, border: '1px solid ' + (system === 'bipon39' ? 'rgba(138,75,255,0.5)' : 'rgba(255,255,255,.1)'), background: system === 'bipon39' ? 'rgba(138,75,255,0.15)' : 'transparent', color: system === 'bipon39' ? '#c4b5fd' : '#9ca3af', fontSize: 12, cursor: 'pointer' }}>
                BIPỌ̀N39 (Ifá)
              </button>
            </div>
            <input value={label} onChange={e => setLabel(e.target.value)} placeholder="Label (optional)"
              style={{ width: '100%', marginBottom: 10, background: 'rgba(255,255,255,.05)', border: '1px solid rgba(255,255,255,.1)', borderRadius: 6, color: '#fff', fontSize: 12, padding: '7px 8px' }} />
            {error && <div style={{ fontSize: 11, color: '#ff2d4a', marginBottom: 8 }}>{error}</div>}
            <button onClick={generate} disabled={busy}
              style={{ width: '100%', padding: '9px 0', background: '#8a4bff', border: 'none', borderRadius: 6, color: '#fff', fontWeight: 700, fontSize: 12, cursor: busy ? 'wait' : 'pointer' }}>
              {busy ? 'Generating…' : 'Generate'}
            </button>
          </>
        ) : (
          <>
            <div style={{ fontSize: 10, color: '#39ff14', fontWeight: 700, marginBottom: 6 }}>✅ Wallet created</div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.6)', marginBottom: 4 }}>Address</div>
            <div style={{ fontSize: 11, fontFamily: 'monospace', color: '#fff', wordBreak: 'break-all', marginBottom: 10, padding: '6px 8px', background: 'rgba(255,255,255,.04)', borderRadius: 6 }}>{result.address}</div>
            <div style={{ fontSize: 11, color: '#ffaa00', fontWeight: 700, marginBottom: 4 }}>⚠️ Mnemonic — save this now, shown only once</div>
            <div style={{ fontSize: 11, fontFamily: 'monospace', color: '#ffaa00', wordBreak: 'break-word', marginBottom: 10, padding: '8px 10px', background: 'rgba(255,170,0,0.08)', border: '1px solid rgba(255,170,0,0.25)', borderRadius: 6, lineHeight: 1.6 }}>
              {result.mnemonic}
            </div>
            {result.dominant_macro && (
              <div style={{ fontSize: 10, color: 'rgba(255,255,255,.4)', marginBottom: 10 }}>
                Ifá: dominant macro {result.dominant_macro}, odù index {result.odu_primary_index}
              </div>
            )}
            <div style={{ fontSize: 10, color: 'rgba(255,255,255,.4)', marginBottom: 12 }}>
              For a stealth paper backup, this mnemonic can be pasted into the separate CloakSeed app (client-side only — not connected to Vantage) to generate a cipher-obscured version.
            </div>
            <button onClick={onClose} style={{ width: '100%', padding: '9px 0', background: 'rgba(255,255,255,.08)', border: '1px solid rgba(255,255,255,.15)', borderRadius: 6, color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>
              Done
            </button>
          </>
        )}
      </div>
    </div>
  )
}
