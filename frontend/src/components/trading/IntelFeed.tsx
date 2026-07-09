import React, { useMemo } from 'react'
import { TrendingUp, TrendingDown, Activity, Zap, AlertTriangle, Newspaper, Brain, Filter } from 'lucide-react'
import { useTradingStore, Signal, AlphaMover, WhaleTx, Threat, NewsItem, DebateSummary, FeedFilter } from './tradingStore'

// ══════════════════════════════════════════════════════════════════════════════
// IntelFeed — left panel. Scrolling feed of signals, alpha, whales, threats,
// news, debate. Every item is clickable and navigates the chart.
// ══════════════════════════════════════════════════════════════════════════════

const FILTERS: { key: FeedFilter; label: string; icon: React.ReactNode }[] = [
  { key: 'all', label: 'All', icon: <Filter size={12} /> },
  { key: 'signals', label: 'Signals', icon: <Zap size={12} /> },
  { key: 'alpha', label: 'Alpha', icon: <TrendingUp size={12} /> },
  { key: 'whales', label: 'Whales', icon: <Activity size={12} /> },
  { key: 'threats', label: 'Threats', icon: <AlertTriangle size={12} /> },
  { key: 'news', label: 'News', icon: <Newspaper size={12} /> },
  { key: 'debate', label: 'Debate', icon: <Brain size={12} /> },
]

export default function IntelFeed() {
  const { state, dispatch, navigateTo } = useTradingStore()

  const items = useMemo(() => {
    const all: { type: string; data: any; timestamp: number; sortKey: number }[] = []

    if (state.feedFilter === 'all' || state.feedFilter === 'signals') {
      state.signals.forEach(s => all.push({ type: 'signal', data: s, timestamp: new Date(s.timestamp).getTime() || Date.now(), sortKey: s.conviction }))
    }
    if (state.feedFilter === 'all' || state.feedFilter === 'alpha') {
      state.alphaMovers.forEach(m => all.push({ type: 'alpha', data: m, timestamp: Date.now(), sortKey: Math.abs(m.change_pct) }))
    }
    if (state.feedFilter === 'all' || state.feedFilter === 'whales') {
      state.whaleTransactions.forEach(w => all.push({ type: 'whale', data: w, timestamp: new Date(w.timestamp).getTime() || Date.now(), sortKey: w.amount_usd }))
    }
    if (state.feedFilter === 'all' || state.feedFilter === 'threats') {
      state.activeThreats.forEach(t => all.push({ type: 'threat', data: t, timestamp: new Date(t.timestamp).getTime() || Date.now(), sortKey: t.conviction }))
    }
    if (state.feedFilter === 'all' || state.feedFilter === 'news') {
      state.newsItems.forEach(n => all.push({ type: 'news', data: n, timestamp: new Date(n.timestamp).getTime() || Date.now(), sortKey: n.confidence }))
    }
    if (state.feedFilter === 'all' || state.feedFilter === 'debate') {
      state.debateSummaries.forEach(d => all.push({ type: 'debate', data: d, timestamp: new Date(d.timestamp).getTime() || Date.now(), sortKey: d.conviction }))
    }

    return all.sort((a, b) => b.timestamp - a.timestamp).slice(0, 60)
  }, [state])

  function handleSignalClick(signal: Signal) {
    navigateTo(signal.symbol, signal.timeframe, new Date(signal.timestamp).getTime() / 1000)
  }

  return (
    <div style={styles.container}>
      {/* Filter Bar */}
      <div style={styles.filterBar}>
        {FILTERS.map(f => (
          <button
            key={f.key}
            style={{
              ...styles.filterBtn,
              background: state.feedFilter === f.key ? 'rgba(0,245,255,0.12)' : 'transparent',
              color: state.feedFilter === f.key ? '#00f5ff' : '#6b7280',
              borderColor: state.feedFilter === f.key ? 'rgba(0,245,255,0.3)' : 'transparent',
            }}
            onClick={() => dispatch({ type: 'SET_FEED_FILTER', filter: f.key })}
          >
            {f.icon} {f.label}
          </button>
        ))}
      </div>

      {/* Feed Items */}
      <div style={styles.feed}>
        {items.length === 0 && (
          <div style={styles.empty}>
            <Zap size={20} style={{ opacity: 0.2, marginBottom: 8 }} />
            <div style={{ fontSize: 11, color: '#6b7280' }}>No intel items yet</div>
          </div>
        )}
        {items.map((item, i) => {
          if (item.type === 'signal') return <SignalCard key={`s-${i}`} signal={item.data} onClick={() => handleSignalClick(item.data)} />
          if (item.type === 'alpha') return <AlphaCard key={`a-${i}`} mover={item.data} onClick={() => navigateTo(item.data.symbol)} />
          if (item.type === 'whale') return <WhaleCard key={`w-${i}`} tx={item.data} onClick={() => navigateTo(item.data.symbol)} />
          if (item.type === 'threat') return <ThreatBadge key={`t-${i}`} threat={item.data} />
          if (item.type === 'news') return <NewsCard key={`n-${i}`} item={item.data} onClick={() => item.data.symbols?.[0] && navigateTo(item.data.symbols[0])} />
          if (item.type === 'debate') return <DebateCard key={`d-${i}`} summary={item.data} />
          return null
        })}
      </div>
    </div>
  )
}

// ── Signal Card ────────────────────────────────────────────────────────────────
function SignalCard({ signal, onClick }: { signal: Signal; onClick: () => void }) {
  const isUp = ['BUY', 'LONG', 'BULLISH'].includes(signal.direction)
  const isDown = ['SELL', 'SHORT', 'BEARISH'].includes(signal.direction)
  const color = isUp ? '#39ff14' : isDown ? '#ff2d4a' : '#ffaa00'
  const arrow = isUp ? '↑' : isDown ? '↓' : '→'

  return (
    <div style={styles.signalCard} onClick={onClick}>
      <div style={styles.signalHeader}>
        <span style={{ ...styles.signalSymbol, color }}>{signal.symbol}</span>
        <span style={{ ...styles.signalDir, color }}>{arrow} {signal.direction}</span>
      </div>
      <div style={styles.signalMeta}>
        <span style={{ ...styles.convictionBar, width: `${signal.conviction * 100}%`, background: color }} />
        <span style={{ fontSize: 10, color: '#9ca3af' }}>{(signal.conviction * 100).toFixed(0)}% conviction</span>
        <span style={{ fontSize: 10, color: '#6b7280' }}>· {signal.source}</span>
      </div>
      {signal.reasoning && <div style={styles.signalReason}>{signal.reasoning.slice(0, 80)}</div>}
      {signal.is_anomaly && <span style={styles.anomalyPulse}>● ANOMALY</span>}
      {signal.is_predictive && <span style={styles.predictiveGlow}>◆ PREDICTIVE</span>}
    </div>
  )
}

// ── Alpha Card ─────────────────────────────────────────────────────────────────
function AlphaCard({ mover, onClick }: { mover: AlphaMover; onClick: () => void }) {
  const color = mover.change_pct >= 0 ? '#39ff14' : '#ff2d4a'
  return (
    <div style={styles.alphaCard} onClick={onClick}>
      <div style={styles.alphaHeader}>
        <span style={{ ...styles.alphaSymbol, color }}>{mover.symbol}</span>
        <span style={{ ...styles.alphaChange, color }}>
          {mover.change_pct >= 0 ? '+' : ''}{mover.change_pct.toFixed(1)}%
        </span>
      </div>
      <div style={{ fontSize: 10, color: '#6b7280' }}>Vol: ${(mover.volume / 1e6).toFixed(1)}M</div>
    </div>
  )
}

// ── Whale Card ─────────────────────────────────────────────────────────────────
function WhaleCard({ tx, onClick }: { tx: WhaleTx; onClick: () => void }) {
  // Tracked smart-money wallet (from the watchlist).
  if (tx.kind === 'wallet') {
    const short = tx.address ? `${tx.address.slice(0, 4)}…${tx.address.slice(-4)}` : ''
    return (
      <div style={styles.whaleCard} onClick={onClick}>
        <div style={styles.whaleHeader}>
          <span style={{ fontSize: 13 }}>🐋</span>
          <span style={styles.whaleSymbol}>{tx.label}</span>
          <span style={{ marginLeft: 'auto', fontSize: 9, color: '#00f5ff', background: 'rgba(0,245,255,0.1)', border: '1px solid rgba(0,245,255,0.25)', borderRadius: 4, padding: '1px 5px', textTransform: 'uppercase' }}>
            {tx.chain || tx.symbol}
          </span>
        </div>
        <div style={{ fontSize: 10, color: '#6b7280', fontFamily: 'monospace' }}>{short}</div>
      </div>
    )
  }
  // Mempool transaction.
  const isIn = tx.direction === 'inflow'
  const color = isIn ? '#ff2d4a' : '#39ff14'
  return (
    <div style={styles.whaleCard} onClick={onClick}>
      <div style={styles.whaleHeader}>
        <span style={{ color, fontSize: 14 }}>{isIn ? '⬇' : '⬆'}</span>
        <span style={styles.whaleSymbol}>{tx.symbol}</span>
        <span style={styles.whaleAmt}>{tx.amount.toLocaleString()}</span>
      </div>
      <div style={{ fontSize: 10, color: '#6b7280' }}>
        {tx.amount_usd > 0 ? `$${tx.amount_usd.toLocaleString()} ` : ''}{tx.exchange ? `· ${tx.exchange}` : ''}
      </div>
    </div>
  )
}

// ── Threat Badge ───────────────────────────────────────────────────────────────
function ThreatBadge({ threat }: { threat: Threat }) {
  return (
    <div style={styles.threatCard}>
      <div style={styles.threatHeader}>
        <span style={styles.threatDot}>🔴</span>
        <span style={styles.threatName}>{threat.name}</span>
      </div>
      <div style={{ fontSize: 10, color: '#ff2d4a', fontWeight: 600 }}>
        Impact: {threat.impact} · {(threat.conviction * 100).toFixed(0)}%
      </div>
      {threat.related_events && (
        <div style={{ fontSize: 10, color: '#6b7280', marginTop: 2 }}>
          Related: {threat.related_events.slice(0, 2).join(', ')}
        </div>
      )}
    </div>
  )
}

// ── News Card ──────────────────────────────────────────────────────────────────
function NewsCard({ item, onClick }: { item: NewsItem; onClick: () => void }) {
  const sentColor = item.sentiment === 'positive' ? '#39ff14' : item.sentiment === 'negative' ? '#ff2d4a' : '#6b7280'
  return (
    <div style={styles.newsCard} onClick={onClick}>
      <div style={styles.newsHeader}>
        <span style={{ ...styles.newsDot, background: sentColor }} />
        <span style={styles.newsTitle}>{item.title.slice(0, 80)}</span>
      </div>
      <div style={{ fontSize: 10, color: '#6b7280' }}>
        {item.source} · {(item.confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}

// ── Debate Card ────────────────────────────────────────────────────────────────
function DebateCard({ summary }: { summary: DebateSummary }) {
  const color = summary.consensus.toLowerCase().includes('long') ? '#39ff14' : summary.consensus.toLowerCase().includes('short') ? '#ff2d4a' : '#ffaa00'
  return (
    <div style={styles.debateCard}>
      <div style={styles.debateHeader}>
        <Brain size={14} style={{ color: '#8a4bff' }} />
        <span style={{ ...styles.debateConsensus, color }}>{summary.consensus}</span>
      </div>
      <div style={{ fontSize: 11, color: '#9ca3af' }}>{summary.topic}</div>
      <div style={styles.debateAgents}>
        {summary.agents.map((a, i) => (
          <span key={i} style={styles.debateAgent}>{a.name}: {a.stance}</span>
        ))}
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
  filterBar: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 3,
    padding: '8px 10px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    flexShrink: 0,
  },
  filterBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 3,
    padding: '3px 8px',
    border: '1px solid transparent',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: 'inherit',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    transition: 'all 0.15s',
  },
  feed: {
    flex: 1,
    overflowY: 'auto',
    padding: '6px 8px',
  },
  empty: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 40,
    textAlign: 'center',
  },
  signalCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 8,
    padding: '10px 12px',
    marginBottom: 6,
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  signalHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  signalSymbol: {
    fontSize: 13,
    fontWeight: 700,
    fontFamily: 'monospace',
  },
  signalDir: {
    fontSize: 11,
    fontWeight: 700,
  },
  signalMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    position: 'relative',
  },
  convictionBar: {
    position: 'absolute',
    left: 0,
    top: '50%',
    transform: 'translateY(-50%)',
    height: 3,
    borderRadius: 2,
    opacity: 0.3,
  },
  signalReason: {
    fontSize: 10,
    color: '#6b7280',
    marginTop: 4,
    lineHeight: 1.4,
  },
  anomalyPulse: {
    fontSize: 9,
    fontWeight: 700,
    color: '#ff2d4a',
    letterSpacing: 1,
    marginTop: 4,
    animation: 'pulse 1.5s infinite',
  },
  predictiveGlow: {
    fontSize: 9,
    fontWeight: 700,
    color: '#00f5ff',
    letterSpacing: 1,
    marginTop: 4,
  },
  alphaCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 8,
    padding: '10px 12px',
    marginBottom: 6,
    cursor: 'pointer',
  },
  alphaHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  alphaSymbol: {
    fontSize: 13,
    fontWeight: 700,
    fontFamily: 'monospace',
  },
  alphaChange: {
    fontSize: 14,
    fontWeight: 700,
  },
  whaleCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 8,
    padding: '10px 12px',
    marginBottom: 6,
    cursor: 'pointer',
  },
  whaleHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  whaleSymbol: {
    fontSize: 12,
    fontWeight: 600,
    color: '#e0e0e0',
  },
  whaleAmt: {
    fontSize: 13,
    fontWeight: 700,
    fontFamily: 'monospace',
    color: '#ffaa00',
  },
  threatCard: {
    background: 'rgba(255,45,74,0.08)',
    border: '1px solid rgba(255,45,74,0.3)',
    borderRadius: 8,
    padding: '10px 12px',
    marginBottom: 6,
    animation: 'threatPulse 2s infinite',
  },
  threatHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  threatDot: {
    fontSize: 10,
  },
  threatName: {
    fontSize: 12,
    fontWeight: 700,
    color: '#ff2d4a',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  newsCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 8,
    padding: '10px 12px',
    marginBottom: 6,
    cursor: 'pointer',
  },
  newsHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 8,
  },
  newsDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    flexShrink: 0,
    marginTop: 4,
  },
  newsTitle: {
    fontSize: 12,
    color: '#e0e0e0',
    lineHeight: 1.4,
  },
  debateCard: {
    background: 'rgba(138,75,255,0.06)',
    border: '1px solid rgba(138,75,255,0.2)',
    borderRadius: 8,
    padding: '10px 12px',
    marginBottom: 6,
  },
  debateHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  debateConsensus: {
    fontSize: 12,
    fontWeight: 700,
  },
  debateAgents: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
    marginTop: 6,
  },
  debateAgent: {
    fontSize: 10,
    color: '#6b7280',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: 4,
    padding: '2px 6px',
  },
}
