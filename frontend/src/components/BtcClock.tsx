import React, { useEffect, useState } from 'react'

/**
 * BtcClock — the ecosystem clock, in the header.
 *
 * Reads /api/clock (koodu_clock.snapshot()): Bitcoin height, the Koodu calendar
 * day (Access/Score/History/Query/Sync/Run/Policy), Sabbath flag and an ETA to
 * the next block. Deliberately NOT `Date.now()`: the wall clock can be wrong,
 * the chain cannot be that wrong.
 */
interface ClockPayload {
  ok: boolean
  height: number
  domain?: string
  sabbath?: boolean
  now_est?: number
  next_block_eta_s?: number
  skew_s?: number
  uncertainty_s?: number
  halving_era?: number
  degraded?: boolean
  verified?: boolean
  headers_checked?: number | null
  pow_ok?: boolean | null
  continuity_ok?: boolean
  rooted?: boolean
  pin?: { status?: string; height?: number; source?: string; class?: string;
          rooted_by?: { height?: number; class?: string; source?: string } | null } | null
  verified_depth?: number | null
  verified_lowest?: number | null
  anchor_height?: number | null
  anchor_seeded?: boolean
  anchor_sources?: string[] | null
  gap_walked?: number
}

export default function BtcClock() {
  const [c, setC] = useState<ClockPayload | null>(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const r = await fetch('/api/clock/height')
        const d = await r.json()
        if (!alive) return
        if (d && typeof d.height === 'number') { setC(d); setErr(false) }
        else setErr(true)
      } catch { if (alive) setErr(true) }
    }
    load()
    const t = setInterval(load, 30000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (err || !c) return null

  const eta = c.next_block_eta_s ?? 0
  const mins = Math.floor(eta / 60)
  const secs = eta % 60
  const sabbath = !!c.sabbath
  const verified = !!c.verified

  return (
    <div
      title={
        `Bitcoin height ${c.height} · ${c.domain || '—'} day\n` +
        (verified
          ? `chain verified: ${c.headers_checked ?? '?'} headers, linkage + proof-of-work OK\n` +
            (c.continuity_ok
              ? ((c.anchor_seeded
                    ? `anchor seeded from ${(c.anchor_sources || []).join(' + ') || 'source agreement'}\n`
                    : '') +
                 `connected to anchor at ${c.anchor_height ?? '?'}${c.gap_walked ? ` (walked back ${c.gap_walked})` : ''}\n`)
              : `NOT connected to a previous anchor — treat this height as unconfirmed\n`)
          : `NOT verified — height is a quorum claim, not checked against proof-of-work\n`) +
        `next block ~${mins}m ${secs}s\n` +
        `clock skew ${c.skew_s ?? '?'}s · ±${c.uncertainty_s ?? '?'}s\n` +
        `halving era ${c.halving_era ?? '?'}${c.degraded ? ' · DEGRADED (cached)' : ''}`
      }
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '5px 12px',
        margin: '0 0 8px',
        background: sabbath ? 'rgba(245,158,11,0.10)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${sabbath ? 'rgba(245,158,11,0.35)' : 'rgba(255,255,255,0.08)'}`,
        borderRadius: 10,
        fontFamily: 'monospace',
        fontSize: 11,
        color: 'var(--muted, #9ca3af)',
        cursor: 'default',
        userSelect: 'none',
      }}
    >
      <span style={{ color: sabbath ? '#f59e0b' : '#22c55e', fontSize: 12 }}>₿</span>
      <span style={{ color: '#fff' }}>{c.height.toLocaleString()}</span>
      {verified && (
        <span title={`${c.headers_checked ?? '?'} headers verified: linkage + proof-of-work${c.verified_depth ? `\n${c.verified_depth} headers PoW-verified in total, back to block ${c.verified_lowest}` : ''}${c.rooted ? `\nrooted: ${c.pin?.rooted_by?.class || c.pin?.class || 'pin'} at block ${c.pin?.rooted_by?.height || c.pin?.height} (${c.pin?.rooted_by?.source || c.pin?.source || 'external checkpoint'})` : `\nNOT rooted — depth is known, provenance is not (no external root)`}`}
              style={{ color: '#22c55e', fontWeight: 700 }}>✓</span>
      )}
      <span style={{ opacity: 0.55 }}>·</span>
      <span style={{ textTransform: 'capitalize' }}>{c.domain || '—'}</span>
      <span style={{ opacity: 0.55 }}>·</span>
      <span title="expected time to the next block">{eta > 0 ? `${mins}m ${String(secs).padStart(2, '0')}s` : 'due'}</span>
      {sabbath && <span style={{ color: '#f59e0b', fontWeight: 600 }}>SABBATH</span>}
      {c.degraded && <span style={{ color: '#f59e0b' }}>cached</span>}
    </div>
  )
}
