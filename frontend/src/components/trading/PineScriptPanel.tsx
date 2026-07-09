import React, { useState, useEffect, useCallback } from 'react'
import { Play, Save, BookOpen, AlertTriangle, Code, MessageSquare, Sparkles, X, RefreshCw } from 'lucide-react'
import { useTradingStore, PinePanelMode } from './tradingStore'
import type { PineSeries } from './CandleChart'

// ══════════════════════════════════════════════════════════════════════════════
// PineScriptPanel — slide-in panel for Pine Script authoring & overlay.
//
// Three modes:
//   • NL (natural language) — describe what you want, code auto-fills
//   • CODE — direct Pine Script editor with syntax-highlighted textarea
//   • SIGNAL — convert a selected signal into Pine Script
//   • BACKTEST — convert a backtested strategy into Pine Script
//
// Scripts run in the isolated pine-runtime sandbox (no network/filesystem).
// Only numeric series come back — they overlay the chart via onResult callback.
// ══════════════════════════════════════════════════════════════════════════════

const DEFAULT_SCRIPT = `//@version=5
indicator("Vantage Custom Indicator", overlay=true)

// Simple EMA crossover example
ema20 = ta.ema(close, 20)
ema50 = ta.ema(close, 50)

plot(ema20, "EMA 20", color=color.green)
plot(ema50, "EMA 50", color=color.red)

// Highlight crossover zones
bullish = ta.crossover(ema20, ema50)
bearish = ta.crossunder(ema20, ema50)

bgcolor(bullish ? color.new(color.green, 90) : na)
bgcolor(bearish ? color.new(color.red, 90) : na)`

const PRE_BUILT_SCRIPTS: { name: string; label: string; code: string; category: string }[] = [
  {
    name: 'vantage_rsi',
    label: 'Vantage RSI',
    category: 'oscillator',
    code: `//@version=5
indicator("Vantage RSI", overlay=false)
length = input.int(14, "RSI Length")
rsi = ta.rsi(close, length)
hline(70, "Overbought", color=color.red)
hline(30, "Oversold", color=color.green)
plot(rsi, "RSI", color=color.purple)
bgcolor(rsi > 70 ? color.new(color.red, 90) : rsi < 30 ? color.new(color.green, 90) : na)`,
  },
  {
    name: 'vantage_macd',
    label: 'Vantage MACD',
    category: 'oscillator',
    code: `//@version=5
indicator("Vantage MACD", overlay=false)
[macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)
plot(macdLine, "MACD", color=color.blue)
plot(signalLine, "Signal", color=color.orange)
plot(hist, "Histogram", color=color.white, style=plot.style_columns)`,
  },
  {
    name: 'vantage_bollinger',
    label: 'Vantage Bollinger Bands',
    category: 'overlay',
    code: `//@version=5
indicator("Vantage Bollinger Bands", overlay=true)
length = input.int(20, "Length")
mult = input.float(2.0, "Std Dev Multiplier")
basis = ta.sma(close, length)
dev = mult * ta.stdev(close, length)
upper = basis + dev
lower = basis - dev
plot(basis, "SMA", color=color.blue)
u = plot(upper, "Upper", color=color.red)
l = plot(lower, "Lower", color=color.green)
fill(u, l, color=color.new(color.gray, 95))`,
  },
  {
    name: 'vantage_volume_profile',
    label: 'Vantage Volume Profile',
    category: 'volume',
    code: `//@version=5
indicator("Vantage Volume Profile", overlay=true)
// Simple volume-weighted average price
vwap = ta.vwap(close)
plot(vwap, "VWAP", color=color.orange, linewidth=2)
// Volume color
volColor = close >= open ? color.green : color.red
plot(volume, "Volume", color=color.new(volColor, 70), style=plot.style_columns)`,
  },
  {
    name: 'vantage_whale_zones',
    label: 'Vantage Whale Zones',
    category: 'alert',
    code: `//@version=5
indicator("Vantage Whale Zones", overlay=true)
// Highlight areas of high volume (proxy for whale activity)
avgVol = ta.sma(volume, 20)
whaleVol = volume > avgVol * 2
barcolor(whaleVol ? color.yellow : na)
// Plot volume spike levels as horizontal lines
var float whaleLevel = na
if whaleVol and not whaleVol[1]
    whaleLevel := close
if not na(whaleLevel)
    line.new(bar_index[1], whaleLevel, bar_index, whaleLevel, color=color.yellow, width=1)`,
  },
  {
    name: 'vantage_signal_markers',
    label: 'Vantage Signal Markers',
    category: 'strategy',
    code: `//@version=5
indicator("Vantage Signal Markers", overlay=true)
// RSI + MACD confluence for signal markers
rsi = ta.rsi(close, 14)
[macd, signal, _] = ta.macd(close, 12, 26, 9)

buySignal = ta.crossover(rsi, 30) and macd > signal
sellSignal = ta.crossunder(rsi, 70) and macd < signal

plotshape(buySignal, "Buy", shape.triangleup, location.belowbar, color=color.green, size=size.small)
plotshape(sellSignal, "Sell", shape.triangledown, location.abovebar, color=color.red, size=size.small)`,
  },
  {
    name: 'vantage_threat_zones',
    label: 'Vantage Threat Zones',
    category: 'alert',
    code: `//@version=5
indicator("Vantage Threat Zones", overlay=true)
// Highlight high-volatility / threat periods
atr = ta.atr(14)
avgAtr = ta.sma(atr, 20)
threatZone = atr > avgAtr * 1.5
bgcolor(threatZone ? color.new(color.red, 85) : na)`,
  },
  {
    name: 'vantage_sentiment',
    label: 'Vantage Sentiment',
    category: 'oscillator',
    code: `//@version=5
indicator("Vantage Sentiment", overlay=false)
// Sentiment proxy: price relative to moving averages
sma20 = ta.sma(close, 20)
sma50 = ta.sma(close, 50)
sentiment = ((close - sma20) / sma20 * 50) + ((close - sma50) / sma50 * 50)
plot(sentiment, "Sentiment", color=sentiment > 0 ? color.green : color.red)
hline(0, "Neutral", color=color.gray)`,
  },
]

const MODE_BUTTONS: { mode: PinePanelMode; label: string; icon: React.ElementType }[] = [
  { mode: 'nl', label: 'Natural Language', icon: MessageSquare },
  { mode: 'code', label: 'Code Editor', icon: Code },
]

function agentKey(): string {
  return localStorage.getItem('vantage_api_key') || ''
}

export default function PineScriptPanel() {
  const { state, dispatch } = useTradingStore()
  const [script, setScript] = useState(DEFAULT_SCRIPT)
  const [nlInput, setNlInput] = useState('')
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState('')
  const [name, setName] = useState('')
  const [saved, setSaved] = useState<any[]>([])
  const [activeLibTab, setActiveLibTab] = useState<string>('all')
  const hasKey = !!agentKey()

  const mode = state.pineMode || 'code'

  // Load saved scripts
  const loadSaved = useCallback(async () => {
    if (!hasKey) return
    try {
      const r = await fetch('/api/pine/indicators', { headers: { 'X-Agent-Key': agentKey() } })
      if (r.ok) setSaved(await r.json())
    } catch {}
  }, [hasKey])

  useEffect(() => { loadSaved() }, [loadSaved])

  // Run script against the pine-runtime sandbox
  async function runScript(code?: string) {
    setRunning(true)
    setMsg('')
    try {
      const s = code || script
      const r = await fetch('/api/pine/run', {
        method: 'POST',
        headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ script: s, symbol: state.activePair?.split('/')[0] || 'BTC', interval: state.activeTimeframe || '1h' }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg(d.detail || `Run failed (${r.status})`); setRunning(false); return }
      const plots = d.plots || {}
      const count = Object.keys(plots).length
      setMsg(count ? `✅ ${count} plot(s) applied${d.alerts?.length ? ` · ${d.alerts.length} alert(s)` : ''}` : '⚠️ Ran, but no plot() output. Add plot() calls to see overlays.')
    } catch {
      setMsg('⚠️ Pine sandbox may be offline — the indicator will be saved for later.')
    }
    setRunning(false)
  }

  // NL → generate Pine script (simulated; Phase 3 adds LLM)
  async function generateFromNL() {
    if (!nlInput.trim()) { setMsg('Describe what you want the indicator to do'); return }
    setRunning(true)
    setMsg('')
    try {
      const r = await fetch('/api/pine/generate', {
        method: 'POST',
        headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: nlInput, symbol: state.activePair?.split('/')[0] || 'BTC', interval: state.activeTimeframe || '1h' }),
      })
      if (r.ok) {
        const d = await r.json()
        if (d.script) {
          setScript(d.script)
          setMsg('✅ Script generated! Review in Code Editor tab, then Run to apply.')
        } else {
          setMsg(d.message || 'Generation did not return a script.')
        }
      } else {
        // No backend yet — generate a template
        const template = generateTemplateFromNL(nlInput)
        setScript(template)
        setMsg('📝 Template generated (LLM backend not connected yet). Review and Run.')
      }
    } catch {
      setMsg('⚠️ Generation unavailable — using template fallback.')
    }
    setRunning(false)
  }

  // Save script to agent's library
  async function saveScript() {
    if (!name.trim()) { setMsg('Name your indicator first'); return }
    try {
      const r = await fetch('/api/pine/indicators', {
        method: 'POST',
        headers: { 'X-Agent-Key': agentKey(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), script, description: '' }),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) { setMsg('✅ Saved to your library'); setName(''); loadSaved() }
      else setMsg(d.detail || 'Save failed')
    } catch { setMsg('Save failed') }
  }

  // Load a pre-built or saved script
  function loadScript(code: string, label: string) {
    setScript(code)
    setName(label)
    dispatch({ type: 'SET_PINE_MODE', mode: 'code' })
    setMsg(`Loaded "${label}" — review and Run to apply`)
  }

  const categories = [...new Set(PRE_BUILT_SCRIPTS.map(s => s.category))]
  const filteredBuiltins = activeLibTab === 'all'
    ? PRE_BUILT_SCRIPTS
    : PRE_BUILT_SCRIPTS.filter(s => s.category === activeLibTab)

  return (
    <div style={styles.container}>
      {/* Mode tabs */}
      <div style={styles.modeBar}>
        {MODE_BUTTONS.map(b => (
          <button
            key={b.mode}
            style={{
              ...styles.modeBtn,
              background: mode === b.mode ? 'rgba(0,245,255,0.12)' : 'transparent',
              color: mode === b.mode ? '#00f5ff' : '#6b7280',
              borderColor: mode === b.mode ? 'rgba(0,245,255,0.3)' : 'transparent',
            }}
            onClick={() => dispatch({ type: 'SET_PINE_MODE', mode: b.mode })}
          >
            <b.icon size={12} /> {b.label}
          </button>
        ))}
        <button style={styles.closeBtn} onClick={() => dispatch({ type: 'TOGGLE_PINE_PANEL' })}>
          <X size={16} />
        </button>
      </div>

      {/* NL Mode */}
      {mode === 'nl' && (
        <div style={styles.nlSection}>
          <div style={styles.nlPrompt}>
            <Sparkles size={16} style={{ color: '#8a4bff' }} />
            <span style={{ fontSize: 12, fontWeight: 700, color: '#e0e0e0' }}>
              Describe your indicator in plain English
            </span>
          </div>
          <textarea
            style={styles.nlTextarea}
            placeholder={'Examples:\n"Show RSI divergence when price makes a higher high but RSI makes a lower high"\n"Plot a 20-period Bollinger Band with 2 standard deviations and highlight squeezes"\n"Create a buy signal when MACD crosses above signal AND RSI is below 30"'}
            value={nlInput}
            onChange={e => setNlInput(e.target.value)}
            rows={5}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button style={styles.generateBtn} onClick={generateFromNL} disabled={running}>
              <Sparkles size={12} /> {running ? 'Generating…' : 'Generate Script'}
            </button>
          </div>
          {nlInput && !running && (
            <div style={{ fontSize: 10, color: '#6b7280', marginTop: 8 }}>
              💡 The generator creates Pine Script v5 code. Switch to Code Editor tab to review, then Run to overlay on the chart.
            </div>
          )}
        </div>
      )}

      {/* Code Editor Mode */}
      {mode === 'code' && (
        <div style={styles.codeSection}>
          <textarea
            value={script}
            onChange={e => setScript(e.target.value)}
            spellCheck={false}
            style={styles.codeEditor}
            placeholder="// Write your Pine Script v5 code here..."
          />
          <div style={styles.codeActions}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn btn-primary btn-sm" style={{ display: 'flex', alignItems: 'center', gap: 4 }} onClick={() => runScript()} disabled={running}>
                <Play size={12} /> {running ? 'Running…' : `Run on ${state.activePair || 'BTC'}`}
              </button>
              <input
                style={styles.nameInput}
                placeholder="Name to save"
                value={name}
                onChange={e => setName(e.target.value)}
              />
              <button className="btn btn-ghost btn-sm" style={{ display: 'flex', alignItems: 'center', gap: 4 }} onClick={saveScript}>
                <Save size={12} /> Save
              </button>
            </div>
          </div>
          <div style={{ fontSize: 10, color: '#6b7280', marginTop: 6 }}>
            Runs in an isolated sandbox (no network/filesystem). Output is numeric series only.
          </div>
        </div>
      )}

      {/* Message */}
      {msg && (
        <div style={{
          ...styles.msg,
          color: msg.startsWith('✅') ? '#39ff14' : msg.startsWith('⚠️') ? '#ffaa00' : msg.startsWith('📝') ? '#00f5ff' : '#ff2d4a',
        }}>
          {(msg.includes('offline') || msg.includes('failed')) && <AlertTriangle size={11} />}
          {msg}
        </div>
      )}

      {/* Library */}
      <div style={styles.library}>
        <div style={styles.libHeader}>
          <BookOpen size={13} style={{ color: '#8a4bff' }} />
          <span style={{ fontSize: 12, fontWeight: 700, color: '#e0e0e0' }}>Indicator Library</span>
        </div>

        {/* Category tabs */}
        <div style={styles.libTabs}>
          <button style={{ ...styles.libTab, background: activeLibTab === 'all' ? 'rgba(138,75,255,0.12)' : 'transparent', color: activeLibTab === 'all' ? '#8a4bff' : '#6b7280' }} onClick={() => setActiveLibTab('all')}>
            All
          </button>
          {categories.map(cat => (
            <button key={cat} style={{ ...styles.libTab, background: activeLibTab === cat ? 'rgba(138,75,255,0.12)' : 'transparent', color: activeLibTab === cat ? '#8a4bff' : '#6b7280' }} onClick={() => setActiveLibTab(cat)}>
              {cat}
            </button>
          ))}
          {saved.length > 0 && (
            <button style={{ ...styles.libTab, background: activeLibTab === 'saved' ? 'rgba(138,75,255,0.12)' : 'transparent', color: activeLibTab === 'saved' ? '#8a4bff' : '#6b7280' }} onClick={() => setActiveLibTab('saved')}>
              My Scripts ({saved.length})
            </button>
          )}
        </div>

        {/* Built-in scripts */}
        {activeLibTab !== 'saved' && (
          <div style={styles.scriptGrid}>
            {filteredBuiltins.map(s => (
              <button key={s.name} style={styles.scriptCard} onClick={() => loadScript(s.code, s.label)}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#e0e0e0' }}>{s.label}</div>
                <div style={{ fontSize: 9, color: '#6b7280', textTransform: 'uppercase', marginTop: 2 }}>{s.category}</div>
              </button>
            ))}
          </div>
        )}

        {/* Saved scripts */}
        {activeLibTab === 'saved' && (
          <div style={styles.scriptGrid}>
            {saved.length === 0 && (
              <div style={{ fontSize: 11, color: '#6b7280', padding: 8 }}>No saved scripts yet. Write or generate one, then save it.</div>
            )}
            {saved.map((s: any, i: number) => (
              <button key={i} style={styles.scriptCard} onClick={() => loadScript(s.script || '', s.name)}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#e0e0e0' }}>{s.name}</div>
                <div style={{ fontSize: 9, color: '#6b7280', marginTop: 2 }}>
                  {s.shared ? 'GUILD' : 'private'}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── NL → Pine template fallback (no LLM backend yet) ──────────────────────
function generateTemplateFromNL(prompt: string): string {
  const lower = prompt.toLowerCase()

  if (lower.includes('rsi') && lower.includes('divergence')) {
    return `//@version=5
indicator("RSI Divergence", overlay=true)
length = input.int(14, "RSI Length")
rsi = ta.rsi(close, length)

// Detect higher highs / lower highs
priceHH = close > close[1] and close[1] > close[2]
rsiLH = rsi < rsi[1] and rsi[1] < rsi[2]
priceLL = close < close[1] and close[1] < close[2]
rsiHL = rsi > rsi[1] and rsi[1] > rsi[2]

bearishDiv = priceHH and rsiLH
bullishDiv = priceLL and rsiHL

plotshape(bearishDiv, "Bearish Div", shape.triangledown, location.abovebar, color=color.red)
plotshape(bullishDiv, "Bullish Div", shape.triangleup, location.belowbar, color=color.green)
bgcolor(bearishDiv ? color.new(color.red, 90) : bullishDiv ? color.new(color.green, 90) : na)

// Plot RSI in separate pane
hline(70, "Overbought", color=color.red)
hline(30, "Oversold", color=color.green)
plot(rsi, "RSI", color=color.purple)`
  }

  if (lower.includes('bollinger') || lower.includes('squeeze')) {
    return `//@version=5
indicator("Bollinger Squeeze", overlay=true)
length = input.int(20, "BB Length")
mult = input.float(2.0, "Std Dev")
basis = ta.sma(close, length)
dev = mult * ta.stdev(close, length)
upper = basis + dev
lower = basis - dev

// Squeeze detection — bands are tight
bandWidth = (upper - lower) / basis * 100
squeeze = bandWidth < ta.sma(bandWidth, 20)

plot(basis, "SMA", color=color.blue)
plot(upper, "Upper", color=color.red)
plot(lower, "Lower", color=color.green)
bgcolor(squeeze ? color.new(color.yellow, 90) : na)`
  }

  if (lower.includes('macd') || lower.includes('momentum')) {
    return `//@version=5
indicator("MACD Momentum", overlay=false)
[macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)

// Momentum strength
momentum = macdLine - signalLine
plot(macdLine, "MACD", color=color.blue)
plot(signalLine, "Signal", color=color.orange)
plot(hist, "Histogram", color=hist > 0 ? color.green : color.red, style=plot.style_columns)
hline(0, "Zero", color=color.gray)`
  }

  // Default: EMA crossover
  return `//@version=5
indicator("${prompt.slice(0, 40).replace(/["\\]/g, '') || 'Custom Indicator'}", overlay=true)
fastLen = input.int(10, "Fast EMA")
slowLen = input.int(30, "Slow EMA")
fastEMA = ta.ema(close, fastLen)
slowEMA = ta.ema(close, slowLen)

plot(fastEMA, "Fast EMA", color=color.green)
plot(slowEMA, "Slow EMA", color=color.red)

// Crossover signals
bullish = ta.crossover(fastEMA, slowEMA)
bearish = ta.crossunder(fastEMA, slowEMA)
bgcolor(bullish ? color.new(color.green, 90) : bearish ? color.new(color.red, 90) : na)`
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    borderTop: '1px solid rgba(255,255,255,0.08)',
    background: 'rgba(10,10,20,0.95)',
    maxHeight: 420,
    overflowY: 'auto',
  },
  modeBar: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '8px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    background: 'rgba(10,10,20,0.98)',
  },
  modeBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '4px 10px',
    border: '1px solid transparent',
    borderRadius: 5,
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
  closeBtn: {
    marginLeft: 'auto',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 5,
    color: '#6b7280',
    cursor: 'pointer',
    padding: 3,
    display: 'flex',
  },
  nlSection: {
    padding: '14px',
  },
  nlPrompt: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  nlTextarea: {
    width: '100%',
    minHeight: 80,
    background: 'rgba(0,0,0,0.35)',
    color: '#e0e0e0',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    padding: 10,
    fontFamily: 'monospace',
    fontSize: 11,
    resize: 'vertical',
    outline: 'none',
  },
  generateBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '8px 16px',
    background: '#8a4bff',
    border: 'none',
    borderRadius: 6,
    color: '#fff',
    fontSize: 12,
    fontWeight: 700,
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  codeSection: {
    padding: '14px',
  },
  codeEditor: {
    width: '100%',
    minHeight: 200,
    background: 'rgba(0,0,0,0.4)',
    color: '#e0e0e0',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    padding: 12,
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    fontSize: 11,
    resize: 'vertical',
    outline: 'none',
    lineHeight: 1.6,
    tabSize: 2,
  },
  codeActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginTop: 10,
  },
  nameInput: {
    padding: '5px 10px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 5,
    color: '#e0e0e0',
    fontSize: 11,
    outline: 'none',
    fontFamily: 'inherit',
    maxWidth: 150,
  },
  msg: {
    fontSize: 11,
    padding: '6px 14px',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    borderTop: '1px solid rgba(255,255,255,0.04)',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
  },
  library: {
    padding: '14px',
    borderTop: '1px solid rgba(255,255,255,0.06)',
  },
  libHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    marginBottom: 8,
  },
  libTabs: {
    display: 'flex',
    gap: 3,
    flexWrap: 'wrap',
    marginBottom: 10,
  },
  libTab: {
    padding: '3px 8px',
    border: '1px solid transparent',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
  scriptGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
    gap: 6,
  },
  scriptCard: {
    display: 'flex',
    flexDirection: 'column',
    padding: '8px 10px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 6,
    cursor: 'pointer',
    textAlign: 'left' as const,
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  },
}
