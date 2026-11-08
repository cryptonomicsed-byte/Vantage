import React, { useEffect, useState } from 'react'

/**
 * ReceiptPulse — the last thing the organism proved, next to the clock.
 *
 * Time alone says nothing; time plus a signed receipt root says "at this point
 * in the chain, this was the state we attested to". Reads /api/receipts/latest,
 * which returns only safe fields (no paths, no target).
 */
interface Receipt {
  receipt_id: string
  merkle_root: string
  chain_index: number | null
  created_at: string
  repo: string
  findings: number
  max_cvss: number | null
  ots: 'none' | 'pending' | 'attested' | 'verified' | 'unreadable'
  ots_block?: number | null
  ots_verified?: boolean
  prev_receipt: string | null
}

export default function ReceiptPulse() {
  const [r, setR] = useState<Receipt | null>(null)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const res = await fetch('/api/receipts/latest')
        const d = await res.json()
        if (!alive) return
        if (d?.ok && d.latest) { setR(d.latest); setTotal(d.total || 0) }
      } catch { /* header keeps working without it */ }
    }
    load()
    const t = setInterval(load, 60000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!r) return null

  const proofColor = r.ots === 'verified' ? '#22c55e' : r.ots === 'attested' ? '#f59e0b' : r.ots === 'pending' ? '#f59e0b' : 'var(--muted, #9ca3af)'
  const proofLabel = r.ots === 'verified' ? `btc ✓ ${r.ots_block ?? ''}`.trim()
    : r.ots === 'attested' ? `btc ? ${r.ots_block ?? ''}`.trim()
    : r.ots === 'pending' ? 'ots…' : 'no proof'

  return (
    <div
      title={
        `receipt ${r.receipt_id}\nmerkle ${r.merkle_root}\n` +
        `chain #${r.chain_index ?? '?'} · prev ${r.prev_receipt ? r.prev_receipt.slice(0, 12) : 'genesis'}\n` +
        `repo ${r.repo} · ${r.findings} findings · max cvss ${r.max_cvss ?? '—'}\n` +
        `signed ${r.created_at} · ${total} receipts in the chain`
      }
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '5px 12px',
        margin: '0 0 8px 8px',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 10,
        fontFamily: 'monospace',
        fontSize: 11,
        color: 'var(--muted, #9ca3af)',
        cursor: 'default',
        userSelect: 'none',
      }}
    >
      <span style={{ color: '#8a4bff' }}>◈</span>
      <span style={{ color: '#fff' }}>{r.receipt_id.slice(0, 8)}</span>
      <span style={{ opacity: 0.55 }}>·</span>
      <span>{r.findings} findings</span>
      {r.max_cvss !== null && (
        <>
          <span style={{ opacity: 0.55 }}>·</span>
          <span style={{ color: r.max_cvss >= 9 ? '#ef4444' : r.max_cvss >= 7 ? '#f59e0b' : '#22c55e' }}>
            {r.max_cvss}
          </span>
        </>
      )}
      <span style={{ opacity: 0.55 }}>·</span>
      <span style={{ color: proofColor }}>{proofLabel}</span>
    </div>
  )
}
