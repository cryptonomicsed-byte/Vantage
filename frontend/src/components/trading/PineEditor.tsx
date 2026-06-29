import React, { useState, useEffect, useCallback } from 'react'
import { Play, Save, BookOpen, AlertTriangle } from 'lucide-react'
import type { PineSeries } from './CandleChart'

// ══════════════════════════════════════════════════════════════════════════════
// PineEditor — agents author technical indicators in Pine Script. The script is
// sent to /api/pine/run, which reviews it (Zàngbétò), fetches candles, and
// executes it in the isolated pine-runtime sandbox. Only numeric series come
// back, which the parent overlays on the chart. Authoring code never runs here.
// ══════════════════════════════════════════════════════════════════════════════

const DEFAULT_SCRIPT = `// Pine: overlay an EMA and flag RSI extremes
plot(ta.ema(close, 20), "EMA 20")
plot(ta.rsi(close, 14), "RSI 14")`

function agentKey(): string {
  return localStorage.getItem('vantage_api_key') || ''
}

export default function PineEditor({ symbol, interval, onResult }: { symbol: string; interval: string; onResult: (s: PineSeries[]) => void }) {
  const [script, setScript] = useState(DEFAULT_SCRIPT)
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState('')
  const [name, setName] = useState('')
  const [saved, setSaved] = useState<any[]>([])
  const hasKey = !!agentKey()

  const loadSaved = useCallback(async () => {
    if (!hasKey) return
    try {
      const r = await fetch('/api/pine/indicators', { headers: { 'X-Agent-Key': agentKey() } })
      if (r.ok) setSaved(await r.json())
    } catch {}
  }, [hasKey])

  useEffect(() => { loadSaved() }, [loadSaved])

  async function run() {
    setRunning(true); setMsg('')
    try {
      const r = await fetch('/api/pine/run', {
        method: 'POST',
        headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ script, symbol, interval }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg(d.detail || `Run failed (${r.status})`); onResult([]); setRunning(false); return }
      const plots = d.plots || {}
      const series: PineSeries[] = Object.entries(plots).map(([k, v]: [string, any]) => ({ name: k, data: (v || []) as any }))
      onResult(series)
      setMsg(series.length ? `Plotted ${series.length} series${d.alerts?.length ? ` · ${d.alerts.length} alert(s)` : ''}` : 'Ran, but no plot() output')
    } catch {
      setMsg('Run failed — the Pine sandbox may be offline')
      onResult([])
    }
    setRunning(false)
  }

  async function save() {
    if (!name.trim()) { setMsg('Name your indicator to save it'); return }
    try {
      const r = await fetch('/api/pine/indicators', {
        method: 'POST',
        headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), script }),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) { setMsg('Saved to your vault'); setName(''); loadSaved() }
      else setMsg(d.detail || 'Save failed')
    } catch { setMsg('Save failed') }
  }

  if (!hasKey) {
    return (
      <div className="ares-stat-tile" style={{ padding: 16, fontSize: 12, color: 'var(--muted)' }}>
        Connect your agent key to author Pine Script indicators. The chart's built-in indicators work without a key.
      </div>
    )
  }

  return (
    <div style={{ background: 'rgba(12,12,22,0.9)', border: '1px solid var(--border)', borderRadius: 12, padding: 14 }}>
      <div className="ares-section-title" style={{ marginTop: 0 }}>Pine Script Indicator</div>
      <textarea
        value={script}
        onChange={e => setScript(e.target.value)}
        spellCheck={false}
        style={{ width: '100%', minHeight: 160, background: 'rgba(0,0,0,0.35)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 8, padding: 10, fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }}
      />
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-primary btn-sm" onClick={run} disabled={running}><Play size={12} /> {running ? 'Running…' : `Run on ${symbol}`}</button>
        <input className="ares-input" placeholder="Name to save" value={name} onChange={e => setName(e.target.value)} style={{ maxWidth: 140 }} />
        <button className="btn btn-ghost btn-sm" onClick={save}><Save size={12} /> Save</button>
      </div>
      {msg && <div style={{ fontSize: 11, color: msg.includes('fail') || msg.includes('offline') ? 'var(--warning)' : 'var(--muted)', marginTop: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
        {(msg.includes('fail') || msg.includes('offline')) && <AlertTriangle size={11} />}{msg}
      </div>}
      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8 }}>
        Runs in an isolated sandbox (no network/filesystem) and is reviewed before sharing. Output is numeric series only.
      </div>
      {saved.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4, marginBottom: 6 }}><BookOpen size={11} /> Saved indicators</div>
          {saved.map((s: any, i: number) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, padding: '4px 0', borderTop: '1px solid var(--border)' }}>
              <span style={{ fontWeight: 600 }}>{s.name}{s.shared ? <span style={{ fontSize: 9, color: 'var(--cyan)', marginLeft: 6 }}>GUILD</span> : ''}</span>
              <button className="btn btn-ghost btn-sm" onClick={() => { setScript(s.script || ''); setMsg(`Loaded "${s.name}"`) }}>Load</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
