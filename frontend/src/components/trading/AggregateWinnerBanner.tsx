import React, { useEffect, useState, useCallback, useRef } from 'react'
import { Crown, ChevronDown, ChevronUp } from 'lucide-react'
import { TokenLink } from './EntityProfileCard'

/**
 * Aggregate Winner Banner — task (b): a genuinely whole-app aggregate
 * score per token, DISTINCT from Platform Leaders (task a, each
 * platform's own top pick). Pulls from every real signal source in the
 * backend (smart-money conviction, cross-platform breadth, volume,
 * social sentiment, whale/Money-Flow presence) via
 * GET /api/intel/degen/aggregate-score. Full weighting methodology is
 * documented in backend/aggregate_score.py's module docstring and echoed
 * here via the `methodology` field in the response — no black box: every
 * weight and every raw input is visible on click-to-expand.
 *
 * Any token with a manipulation/rug flag anywhere in the data is
 * disqualified server-side before this component ever sees it — the
 * `disqualified` list (shown when expanded) is for transparency, not
 * because this component does any filtering itself.
 */

interface SmartMoneyRaw {
  copy_trade_score: number
  nansen_smart_money_usd: number | null
}

interface ScoreComponent {
  raw: number | boolean | SmartMoneyRaw
  normalized: number
  weight: number
  sources?: {
    copy_trade_normalized: number
    nansen_normalized: number | null
    nansen_available: boolean
  }
}

function isSmartMoneyRaw(raw: ScoreComponent['raw']): raw is SmartMoneyRaw {
  return typeof raw === 'object' && raw !== null && 'copy_trade_score' in raw
}

interface NarrativeFlag {
  theme_labels: string[]
  detected_at: string
}

interface RankedToken {
  address: string
  symbol: string | null
  total_score: number
  narrative_flag?: NarrativeFlag | null
  components: Record<string, ScoreComponent>
}

interface AggregateScoreResponse {
  ranked: RankedToken[]
  disqualified: { address: string; symbol: string | null; reason: string }[]
  methodology: Record<string, number>
  candidates_considered: number
  generated_at: number
}

const COMPONENT_LABEL: Record<string, string> = {
  smart_money: 'Smart-Money Conviction',
  platform_breadth: 'Platform Breadth',
  volume_momentum: 'Volume / Momentum',
  social_sentiment: 'Social Sentiment',
  whale_presence: 'Whale / Money-Flow Presence',
  narrative_combo: 'Narrative Combo (2+ hot themes)',
}

export default function AggregateWinnerBanner() {
  const [data, setData] = useState<AggregateScoreResponse | null>(null)
  const [expanded, setExpanded] = useState(false)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    const key = localStorage.getItem('vantage_api_key') || ''
    try {
      const r = await fetch('/api/intel/degen/aggregate-score', { headers: { 'X-Agent-Key': key } })
      if (!r.ok) return
      const d = await r.json()
      if (mounted.current) setData(d)
    } catch { /* offline — keep showing last-known-good */ }
  }, [])

  useEffect(() => {
    mounted.current = true
    load()
    const t = setInterval(load, 90000)
    return () => { mounted.current = false; clearInterval(t) }
  }, [load])

  if (!data || data.ranked.length === 0) return null
  const winner = data.ranked[0]

  return (
    <div style={{
      marginBottom: 16, borderRadius: 10,
      border: '1px solid rgba(234,179,8,0.35)',
      background: 'linear-gradient(90deg, rgba(234,179,8,0.12) 0%, rgba(234,179,8,0.02) 100%)',
      overflow: 'hidden',
    }}>
      <div
        onClick={() => setExpanded(e => !e)}
        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', cursor: 'pointer' }}
      >
        <Crown size={18} color="#eab308" />
        <span style={{ fontWeight: 800, fontSize: 12, color: '#eab308', letterSpacing: 0.5 }}>AGGREGATE WINNER</span>
        <span style={{ fontWeight: 700, fontSize: 14, color: '#fff' }}>
          <TokenLink symbol={winner.symbol || winner.address.slice(0, 6)} ca={winner.address} />
        </span>
        {winner.narrative_flag && (
          <span
            title={winner.narrative_flag.theme_labels.join(' + ')}
            style={{
              fontSize: 9, fontWeight: 700, color: '#c026d3', padding: '2px 6px',
              borderRadius: 4, border: '1px solid rgba(192,38,211,0.4)', background: 'rgba(192,38,211,0.08)',
            }}
          >
            🔥 NARRATIVE COMBO
          </span>
        )}
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>
          score <b style={{ color: '#eab308' }}>{winner.total_score.toFixed(3)}</b> / 1.000
        </span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          ({data.candidates_considered} candidates considered
          {data.disqualified.length > 0 && `, ${data.disqualified.length} disqualified`})
        </span>
        <span style={{ flex: 1 }} />
        {expanded ? <ChevronUp size={14} color="var(--muted)" /> : <ChevronDown size={14} color="var(--muted)" />}
      </div>

      {expanded && (
        <div style={{ padding: '0 14px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>
            Fully auditable methodology — every weight fixed and disclosed, every raw input traceable to a real
            backend signal. Scores below are 0..1 min-max normalized against the current candidate pool.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 6 }}>
            {Object.entries(winner.components).map(([key, comp]) => (
              <div key={key} style={{
                padding: '6px 8px', borderRadius: 6, background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)', fontSize: 10,
              }}>
                <div style={{ color: 'var(--muted)', marginBottom: 2 }}>{COMPONENT_LABEL[key] || key} ({(comp.weight * 100).toFixed(0)}%)</div>
                {isSmartMoneyRaw(comp.raw) ? (
                  <div style={{ color: '#fff', fontWeight: 600 }}>
                    <div>
                      copy-trade: {comp.raw.copy_trade_score}
                      {comp.sources && ` (norm ${comp.sources.copy_trade_normalized.toFixed(3)})`}
                    </div>
                    <div style={{ color: comp.sources?.nansen_available ? '#fff' : 'var(--muted)' }}>
                      Nansen: {comp.raw.nansen_smart_money_usd != null
                        ? `$${comp.raw.nansen_smart_money_usd.toLocaleString()}`
                        : (comp.sources?.nansen_available ? 'no data for this token' : 'unavailable — not configured or no data')}
                      {comp.sources?.nansen_available && comp.sources.nansen_normalized != null && ` (norm ${comp.sources.nansen_normalized.toFixed(3)})`}
                    </div>
                    <div>norm: {comp.normalized.toFixed(3)} · contrib: {(comp.normalized * comp.weight).toFixed(4)}</div>
                  </div>
                ) : (
                  <div style={{ color: '#fff', fontWeight: 600 }}>
                    raw: {typeof comp.raw === 'boolean' ? (comp.raw ? 'yes' : 'no') : comp.raw}
                    {' · '}norm: {comp.normalized.toFixed(3)}
                    {' · '}contrib: {(comp.normalized * comp.weight).toFixed(4)}
                  </div>
                )}
              </div>
            ))}
          </div>

          {winner.narrative_flag && (
            <div style={{ fontSize: 10, color: '#c026d3' }}>
              🔥 Combines: {winner.narrative_flag.theme_labels.join(' + ')} — both independently trending
              (see Hot Narratives panel). Detected {new Date(winner.narrative_flag.detected_at + 'Z').toLocaleString()}.
            </div>
          )}

          {data.ranked.length > 1 && (
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>
              Runner-up{data.ranked.length > 2 ? 's' : ''}: {data.ranked.slice(1, 4).map(r => (
                <span key={r.address} style={{ marginRight: 10 }}>
                  <TokenLink symbol={r.symbol || r.address.slice(0, 6)} ca={r.address} /> ({r.total_score.toFixed(3)})
                </span>
              ))}
            </div>
          )}

          {data.disqualified.length > 0 && (
            <div style={{ fontSize: 10, color: '#ef4444' }}>
              Disqualified (manipulation/rug risk, never eligible regardless of score): {data.disqualified.map(d => (
                <span key={d.address} title={d.reason} style={{ marginRight: 10 }}>
                  {d.symbol || d.address.slice(0, 6)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
