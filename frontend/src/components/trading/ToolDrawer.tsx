import React, { useState } from 'react'
import { X, BarChart3, GitBranch, Brain, Coins, Layers, Activity, Shield, Sparkles, Gauge, Layout, RotateCcw, BookOpen } from 'lucide-react'
import { useTradingStore, ToolType } from './tradingStore'
import BacktestEngine from './BacktestEngine'

// ══════════════════════════════════════════════════════════════════════════════
// ToolDrawer — bottom drawer. Replaces all 15 sub-tabs. Slides up to 400px.
// Primary tools always visible, secondary in "More" menu.
// ══════════════════════════════════════════════════════════════════════════════

interface ToolDef {
  id: ToolType
  label: string
  icon: React.ReactNode
  primary: boolean
}

const TOOLS: ToolDef[] = [
  { id: 'backtest', label: 'Backtest', icon: <BarChart3 size={13} />, primary: true },
  { id: 'arbitrage', label: 'Arb', icon: <GitBranch size={13} />, primary: true },
  { id: 'debate', label: 'Debate', icon: <Brain size={13} />, primary: true },
  { id: 'yields', label: 'Yields', icon: <Coins size={13} />, primary: true },
  { id: 'dex', label: 'DEX', icon: <Layers size={13} />, primary: true },
  { id: 'sim', label: 'Sim', icon: <Activity size={13} />, primary: true },
  { id: 'stress', label: 'Stress', icon: <Shield size={13} />, primary: false },
  { id: 'risk', label: 'Risk', icon: <Gauge size={13} />, primary: false },
  { id: 'swarm', label: 'Swarm', icon: <Sparkles size={13} />, primary: false },
  { id: 'journal', label: 'Journal', icon: <BookOpen size={13} />, primary: false },
  { id: 'chains', label: 'Chains', icon: <Layout size={13} />, primary: false },
  { id: 'eco', label: 'Eco Map', icon: <RotateCcw size={13} />, primary: false },
]

export default function ToolDrawer() {
  const { state, dispatch, toggleDrawer } = useTradingStore()
  const [showMore, setShowMore] = useState(false)

  const primaryTools = TOOLS.filter(t => t.primary)
  const secondaryTools = TOOLS.filter(t => !t.primary)

  return (
    <div style={styles.container}>
      {/* Tab Bar */}
      <div style={styles.tabBar}>
        <div style={styles.tabs}>
          {primaryTools.map(tool => (
            <button
              key={tool.id}
              style={{
                ...styles.tab,
                background: state.drawerTool === tool.id ? 'rgba(0,245,255,0.12)' : 'transparent',
                color: state.drawerTool === tool.id ? '#00f5ff' : '#6b7280',
                borderColor: state.drawerTool === tool.id ? 'rgba(0,245,255,0.3)' : 'transparent',
              }}
              onClick={() => toggleDrawer(tool.id)}
            >
              {tool.icon} {tool.label}
            </button>
          ))}

          {/* More button */}
          <div style={{ position: 'relative' }}>
            <button
              style={{
                ...styles.tab,
                background: secondaryTools.some(t => t.id === state.drawerTool) ? 'rgba(0,245,255,0.12)' : 'transparent',
                color: secondaryTools.some(t => t.id === state.drawerTool) ? '#00f5ff' : '#6b7280',
              }}
              onClick={() => setShowMore(!showMore)}
            >
              More ▾
            </button>
            {showMore && (
              <div style={styles.moreDropdown}>
                {secondaryTools.map(tool => (
                  <button
                    key={tool.id}
                    style={{
                      ...styles.moreOption,
                      background: state.drawerTool === tool.id ? 'rgba(0,245,255,0.1)' : 'transparent',
                      color: state.drawerTool === tool.id ? '#00f5ff' : '#e0e0e0',
                    }}
                    onClick={() => { toggleDrawer(tool.id); setShowMore(false) }}
                  >
                    {tool.icon} {tool.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Close button */}
        <button style={styles.closeBtn} onClick={() => dispatch({ type: 'CLOSE_DRAWER' })}>
          <X size={16} />
        </button>
      </div>

      {/* Tool Content */}
      <div style={styles.content}>
        {state.drawerTool === 'backtest' && <BacktestEngine />}
        {state.drawerTool === 'arbitrage' && <ArbitragePlaceholder />}
        {state.drawerTool === 'debate' && <DebatePlaceholder />}
        {state.drawerTool === 'yields' && <YieldsPlaceholder />}
        {state.drawerTool === 'dex' && <DexPlaceholder />}
        {state.drawerTool === 'sim' && <SimPlaceholder />}
        {!state.drawerTool && <div style={{ padding: 20, color: '#6b7280', textAlign: 'center' }}>Select a tool from the tab bar</div>}
      </div>
    </div>
  )
}

// ── Placeholder components for tools (to be fully implemented) ─────────────────

function ArbitragePlaceholder() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  React.useEffect(() => {
    setLoading(true)
    fetch('/api/intel')
      .then(r => r.json())
      .then(d => { setData(d?.arbitrage); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const opps = data?.opportunities || []

  return (
    <div style={{ padding: '0 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: '#e0e0e0' }}>
        Cross-Exchange Arbitrage
        {loading && <span style={{ fontSize: 11, color: '#6b7280', marginLeft: 8 }}>loading...</span>}
      </div>
      {opps.length === 0 ? (
        <div style={{ color: '#6b7280', fontSize: 12 }}>No arbitrage opportunities found</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              <th style={styles.th}>Route</th>
              <th style={styles.th}>Pair</th>
              <th style={styles.th}>Spread</th>
              <th style={styles.th}>Buy At</th>
              <th style={styles.th}>Sell At</th>
            </tr>
          </thead>
          <tbody>
            {opps.slice(0, 10).map((o: any, i: number) => (
              <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={styles.td}>{o.route}</td>
                <td style={styles.td}>{o.pair}</td>
                <td style={{ ...styles.td, color: '#ffaa00', fontWeight: 700 }}>{o.spread_pct?.toFixed(2)}%</td>
                <td style={styles.td}>{o.buy_exchange || '—'}</td>
                <td style={styles.td}>{o.sell_exchange || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function DebatePlaceholder() {
  const [data, setData] = useState<any>(null)
  React.useEffect(() => {
    fetch('/api/debate').then(r => r.json()).then(setData).catch(() => {})
  }, [])

  if (!data) return <div style={{ padding: 20, color: '#6b7280' }}>Loading debate...</div>

  return (
    <div style={{ padding: '0 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: '#e0e0e0' }}>
        Multi-Agent Debate
      </div>
      <div style={{ background: 'rgba(138,75,255,0.08)', border: '1px solid rgba(138,75,255,0.2)', borderRadius: 8, padding: 16, marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: '#8a4bff', marginBottom: 8 }}>Consensus: {data.consensus || '—'}</div>
        <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 8 }}>{data.topic || 'Market analysis'}</div>
        {data.agents && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {data.agents.map((a: any, i: number) => (
              <div key={i} style={{ background: 'rgba(255,255,255,0.05)', borderRadius: 6, padding: '6px 10px' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#e0e0e0' }}>{a.name}</div>
                <div style={{ fontSize: 10, color: '#6b7280' }}>{a.stance}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function YieldsPlaceholder() {
  const [data, setData] = useState<any>(null)
  React.useEffect(() => {
    fetch('/api/intel/yields').then(r => r.json()).then(setData).catch(() => {})
  }, [])

  const pools = data?.pools || data || []

  return (
    <div style={{ padding: '0 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: '#e0e0e0' }}>Cross-Chain Yields</div>
      {pools.length === 0 ? (
        <div style={{ color: '#6b7280', fontSize: 12 }}>Loading yield data...</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
          {pools.slice(0, 12).map((p: any, i: number) => (
            <div key={i} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#e0e0e0' }}>{p.project || p.name || 'Unknown'}</div>
              <div style={{ fontSize: 11, color: '#6b7280' }}>{p.chain || '—'}</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#39ff14', marginTop: 4 }}>{p.apy?.toFixed(1) || '—'}%</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DexPlaceholder() {
  const [query, setQuery] = useState('bitcoin')
  const [data, setData] = useState<any>(null)

  React.useEffect(() => {
    fetch(`/api/intel/dex?q=${query}`).then(r => r.json()).then(setData).catch(() => {})
  }, [query])

  const pairs = data?.pairs || data || []

  return (
    <div style={{ padding: '0 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#e0e0e0' }}>DEX Explorer</div>
        <input
          style={{ padding: '5px 10px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 4, color: '#e0e0e0', fontSize: 12, outline: 'none', width: 200 }}
          placeholder="Search tokens..."
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>
      {pairs.length === 0 ? (
        <div style={{ color: '#6b7280', fontSize: 12 }}>Search for DEX pairs</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
          {pairs.slice(0, 10).map((p: any, i: number) => (
            <div key={i} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#e0e0e0' }}>{p.base_token?.symbol || p.name}</div>
              <div style={{ fontSize: 11, color: '#6b7280' }}>{p.dex_id || p.chain || '—'}</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#00f5ff', marginTop: 4 }}>
                ${Number(p.price_usd || p.price || 0).toFixed(4)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SimPlaceholder() {
  return (
    <div style={{ padding: '0 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: '#e0e0e0' }}>Scenario Simulator</div>
      <div style={{ background: 'rgba(255,170,0,0.08)', border: '1px solid rgba(255,170,0,0.2)', borderRadius: 8, padding: 16 }}>
        <div style={{ fontSize: 12, color: '#ffaa00', fontWeight: 600, marginBottom: 8 }}>Coming Soon</div>
        <div style={{ fontSize: 12, color: '#6b7280' }}>
          What-if simulator: "If SOL goes to $300..." or "If BTC drops 20%..."
          <br />Full scenario modeling with portfolio impact analysis.
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  },
  tabBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 12px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    flexShrink: 0,
  },
  tabs: {
    display: 'flex',
    alignItems: 'center',
    gap: 3,
  },
  tab: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '5px 10px',
    border: '1px solid transparent',
    borderRadius: 5,
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
    whiteSpace: 'nowrap' as const,
  },
  moreDropdown: {
    position: 'absolute',
    top: '100%',
    left: 0,
    marginTop: 4,
    background: '#14142a',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    padding: 4,
    minWidth: 140,
    zIndex: 100,
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
  },
  moreOption: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    width: '100%',
    textAlign: 'left',
    padding: '6px 10px',
    border: 'none',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: 'inherit',
  },
  closeBtn: {
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 5,
    color: '#6b7280',
    cursor: 'pointer',
    padding: 4,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    padding: '12px 0',
  },
  th: {
    textAlign: 'left' as const,
    padding: '6px 8px',
    color: '#6b7280',
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
  },
  td: {
    padding: '6px 8px',
    color: '#e0e0e0',
  },
}
