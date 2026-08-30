import React, { useState } from 'react'
import { X, CreditCard, ExternalLink } from 'lucide-react'

// ── Buy Crypto (debit card on-ramp) — POST /api/trading/wallets/{id}/onramp,
// which returns a real, signed MoonPay widget URL for this exact wallet's
// real Solana address. Vantage never touches card numbers, bank details, or
// KYC data -- all of that happens entirely inside MoonPay's own hosted
// widget once the user's browser opens the returned URL. See
// backend/moonpay_client.py's module docstring for the real provider
// comparison (MoonPay vs Transak vs Coinbase Onramp vs Stripe Crypto
// Onramp) and the exact HMAC-SHA256 signing algorithm.
//
// Honest limitation surfaced directly in the UI, not hidden: if this
// Vantage instance has no real MoonPay API key configured yet, the backend
// returns a 503 with a clear message -- shown here as-is, not papered over.
export default function OnrampModal({
  wallet, onClose, tradingApi,
}: {
  wallet: { id: number; label: string; address: string; chain: string }
  onClose: () => void
  tradingApi: (path: string, opts?: RequestInit) => Promise<Response>
}) {
  const [currency, setCurrency] = useState<'SOL' | 'USDC_SOL'>('SOL')
  const [fiatAmount, setFiatAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<any>(null)

  async function createSession() {
    setBusy(true); setError('')
    try {
      const qs = new URLSearchParams({ currency })
      if (fiatAmount) qs.set('fiat_amount', fiatAmount)
      const r = await tradingApi(`/wallets/${wallet.id}/onramp?${qs.toString()}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) setResult(d)
      else setError(d.detail || `Failed (${r.status})`)
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setBusy(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 'min(420px, 92vw)', background: 'rgba(10,10,20,0.98)', border: '1px solid rgba(57,255,20,0.3)', borderRadius: 14, padding: '18px 20px', maxHeight: '85vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, fontWeight: 700, color: '#fff' }}>
            <CreditCard size={15} color="#39ff14" /> Buy Crypto
          </span>
          <button onClick={onClose} className="btn btn-ghost btn-sm"><X size={14} /></button>
        </div>

        {!result ? (
          <>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)', marginBottom: 10 }}>
              Buy crypto with a debit/credit card via MoonPay, delivered straight to <b style={{ color: '#fff' }}>{wallet.label}</b>
              {' '}(<span style={{ fontFamily: 'monospace' }}>{wallet.address.slice(0, 6)}…{wallet.address.slice(-4)}</span>).
              MoonPay handles all KYC/identity verification and card processing themselves — Vantage never sees your
              card or ID.
            </div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
              <button onClick={() => setCurrency('SOL')}
                style={{ flex: 1, padding: '8px 0', borderRadius: 6, border: '1px solid ' + (currency === 'SOL' ? 'rgba(57,255,20,0.5)' : 'rgba(255,255,255,.1)'), background: currency === 'SOL' ? 'rgba(57,255,20,0.12)' : 'transparent', color: currency === 'SOL' ? '#39ff14' : '#9ca3af', fontSize: 12, cursor: 'pointer' }}>
                SOL
              </button>
              <button onClick={() => setCurrency('USDC_SOL')}
                style={{ flex: 1, padding: '8px 0', borderRadius: 6, border: '1px solid ' + (currency === 'USDC_SOL' ? 'rgba(57,255,20,0.5)' : 'rgba(255,255,255,.1)'), background: currency === 'USDC_SOL' ? 'rgba(57,255,20,0.12)' : 'transparent', color: currency === 'USDC_SOL' ? '#39ff14' : '#9ca3af', fontSize: 12, cursor: 'pointer' }}>
                USDC (Solana)
              </button>
            </div>
            <input value={fiatAmount} onChange={e => setFiatAmount(e.target.value)} placeholder="Amount in USD (optional)" type="number" min="0"
              style={{ width: '100%', marginBottom: 10, background: 'rgba(255,255,255,.05)', border: '1px solid rgba(255,255,255,.1)', borderRadius: 6, color: '#fff', fontSize: 12, padding: '7px 8px' }} />
            {error && (
              <div style={{ fontSize: 11, color: '#ff9d00', marginBottom: 10, padding: '8px 10px', background: 'rgba(255,157,0,0.08)', border: '1px solid rgba(255,157,0,0.25)', borderRadius: 6, lineHeight: 1.5 }}>
                {error}
                {error.toLowerCase().includes('not configured') && (
                  <div style={{ marginTop: 6, color: 'rgba(255,255,255,.5)' }}>
                    This Vantage instance doesn't have a real MoonPay account wired in yet — that needs the owner to
                    sign up at moonpay.com and provide a real API key pair (test or live).
                  </div>
                )}
              </div>
            )}
            <button onClick={createSession} disabled={busy}
              style={{ width: '100%', padding: '9px 0', background: '#39ff14', border: 'none', borderRadius: 6, color: '#000', fontWeight: 700, fontSize: 12, cursor: busy ? 'wait' : 'pointer' }}>
              {busy ? 'Preparing…' : 'Continue to MoonPay'}
            </button>
          </>
        ) : (
          <>
            <div style={{ fontSize: 10, color: '#39ff14', fontWeight: 700, marginBottom: 6 }}>✅ On-ramp session ready</div>
            {result.environment === 'sandbox' && (
              <div style={{ fontSize: 10, color: '#ffaa00', marginBottom: 10, padding: '6px 8px', background: 'rgba(255,170,0,0.08)', border: '1px solid rgba(255,170,0,0.25)', borderRadius: 6 }}>
                Sandbox mode — this uses MoonPay's test environment (test cards only, no real funds move).
              </div>
            )}
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.6)', marginBottom: 10 }}>
              MoonPay opens in a new tab. Complete their identity check and card payment there — funds land directly
              in {wallet.label} once the purchase settles.
            </div>
            <a href={result.url} target="_blank" rel="noopener noreferrer"
              style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '9px 0', background: '#39ff14', border: 'none', borderRadius: 6, color: '#000', fontWeight: 700, fontSize: 12, textDecoration: 'none', boxSizing: 'border-box' }}>
              Open MoonPay <ExternalLink size={12} />
            </a>
            <button onClick={onClose} style={{ width: '100%', marginTop: 8, padding: '9px 0', background: 'rgba(255,255,255,.08)', border: '1px solid rgba(255,255,255,.15)', borderRadius: 6, color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>
              Done
            </button>
          </>
        )}
      </div>
    </div>
  )
}
