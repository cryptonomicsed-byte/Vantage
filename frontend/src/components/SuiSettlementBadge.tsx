import React, { useState } from 'react'

interface Props {
  receiptId: string
  suiTxDigest?: string | null
  settled?: boolean
}

function truncate(s: string, n = 12): string {
  return s.length <= n ? s : s.slice(0, n) + '…'
}

export default function SuiSettlementBadge({ receiptId, suiTxDigest, settled }: Props) {
  const [loading, setLoading] = useState(false)
  const [unsignedPtb, setUnsignedPtb] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const isSettled = settled ?? !!suiTxDigest

  async function handleSettle() {
    setLoading(true)
    setError(null)
    try {
      const apiKey = localStorage.getItem('vantage_api_key') || ''
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (apiKey) headers['X-Agent-Key'] = apiKey
      const r = await fetch(`/api/receipts/${receiptId}/settle/sui`, { method: 'POST', headers })
      if (!r.ok) {
        const txt = await r.text().catch(() => 'Unknown error')
        setError(txt || `HTTP ${r.status}`)
        return
      }
      const data = await r.json()
      setUnsignedPtb(data.unsigned_ptb || JSON.stringify(data, null, 2))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Network error')
    } finally {
      setLoading(false)
    }
  }

  function handleCopy() {
    if (!unsignedPtb) return
    navigator.clipboard.writeText(unsignedPtb).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }

  if (isSettled && suiTxDigest) {
    return (
      <a
        href={`https://suiexplorer.com/txblock/${suiTxDigest}?network=testnet`}
        target="_blank"
        rel="noopener noreferrer"
        title={suiTxDigest}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          fontSize: 10, fontWeight: 600, color: '#3cc878',
          background: 'rgba(60,200,120,0.12)', border: '1px solid rgba(60,200,120,0.3)',
          borderRadius: 4, padding: '2px 7px', textDecoration: 'none',
        }}
      >
        ⬡ Settled <span style={{ fontFamily: 'monospace', fontWeight: 400, opacity: 0.8 }}>{truncate(suiTxDigest, 10)}</span>
      </a>
    )
  }

  return (
    <>
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontSize: 10, fontWeight: 600, color: '#f59e0b',
        background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)',
        borderRadius: 4, padding: '2px 7px',
      }}>
        ⬡ Unsettled
        <button
          onClick={handleSettle}
          disabled={loading}
          style={{
            fontSize: 10, color: '#f59e0b', background: 'transparent', border: 'none',
            cursor: loading ? 'wait' : 'pointer', padding: 0, fontWeight: 600,
          }}
        >
          {loading ? '…' : 'Settle on Sui →'}
        </button>
      </span>

      {error && (
        <span style={{ fontSize: 10, color: '#ef4444', marginLeft: 6 }}>{error}</span>
      )}

      {unsignedPtb && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 400,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setUnsignedPtb(null)}
        >
          <div
            style={{ width: '90%', maxWidth: 560, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <strong style={{ fontSize: 14 }}>Unsigned PTB — Sign in your Sui wallet</strong>
              <button className="btn btn-ghost btn-xs" onClick={() => setUnsignedPtb(null)}>✕</button>
            </div>
            <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
              Copy this Programmable Transaction Block and sign it with your Sui wallet (e.g. Sui Wallet extension or CLI) to settle on-chain.
            </p>
            <pre style={{
              background: 'rgba(0,0,0,0.4)', border: '1px solid var(--border)', borderRadius: 6,
              padding: 12, fontSize: 10, fontFamily: 'monospace', whiteSpace: 'pre-wrap',
              wordBreak: 'break-all', maxHeight: 260, overflowY: 'auto', margin: 0,
            }}>
              {unsignedPtb}
            </pre>
            <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
              <button className="btn btn-sm btn-primary" onClick={handleCopy}>
                {copied ? '✓ Copied' : 'Copy PTB'}
              </button>
              <button className="btn btn-sm" onClick={() => setUnsignedPtb(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
