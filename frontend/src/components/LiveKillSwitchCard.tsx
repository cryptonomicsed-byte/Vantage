import React, { useCallback, useEffect, useState } from 'react'

// Layer 2 of live trading: one account-level control that instantly revokes
// live for every strategy this account owns, overriding per-strategy toggles.
// Paper trading never stops. Releasing it re-enables nothing on its own: each
// strategy still needs its own opt-in and wallet binding (Layer 1).
export default function LiveKillSwitchCard({ apiKey }: { apiKey: string }) {
  const [engaged, setEngaged] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    if (!apiKey) return
    try {
      const r = await fetch('/api/trading/live-killswitch', { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setEngaged((await r.json()).engaged)
    } catch { /* best-effort */ }
  }, [apiKey])
  useEffect(() => { load() }, [load])

  async function set(next: boolean) {
    setBusy(true); setMsg('')
    try {
      const r = await fetch('/api/trading/live-killswitch', {
        method: 'PUT',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ engaged: next, note: next ? 'engaged from Settings > Security' : 'released from Settings > Security' }),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) setEngaged(d.engaged)
      else setMsg(typeof d.detail === 'string' ? d.detail : 'Could not change the kill switch')
    } catch { setMsg('Network error') }
    setBusy(false)
  }

  if (!apiKey) {
    return <div className="stat-card" style={{ padding: 16, fontSize: 12, color: 'var(--muted)' }}>Sign in as an agent to manage live trading.</div>
  }
  return (
    <div className="stat-card" style={{ padding: 16, marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>Live trading kill switch</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
            {engaged
              ? 'Engaged: every strategy on this account is paper only. Paper signals keep running.'
              : 'Released: strategies you opted in, with a bound wallet, may go live when the instance allows it.'}
          </div>
        </div>
        <button
          disabled={busy || engaged === null}
          onClick={() => set(!engaged)}
          style={{
            padding: '8px 14px', borderRadius: 6, fontSize: 12, fontWeight: 700, cursor: busy ? 'wait' : 'pointer',
            border: '1px solid rgba(255,255,255,0.15)',
            background: engaged ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.18)',
            color: engaged ? '#22c55e' : '#ef4444',
          }}
        >
          {engaged === null ? '…' : engaged ? 'Release' : 'Revoke all live'}
        </button>
      </div>
      {msg && <div style={{ fontSize: 11, color: '#ef4444', marginTop: 8 }}>{msg}</div>}
    </div>
  )
}
