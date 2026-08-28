import React, { useEffect, useState, useCallback, useRef } from 'react'
import { TrendingUp, Zap, Wallet, Users, AlertTriangle, DollarSign, Flame, BarChart3, Radio, Rocket } from 'lucide-react'
import { TokenLink, WalletLink } from './EntityProfileCard'

interface Token {
  symbol: string
  score: number
  volume_24h?: number
  price_change_24h?: number
  graduated?: boolean
  reason?: string
  address?: string
  market_cap?: number | null
  sell_buy_ratio?: number
  buys_24h?: number
  sells_24h?: number
}

interface MustBuyToken {
  symbol: string
  ca?: string
  score: number
  volume_24h?: number
  price_change_24h?: number
  source_count: number
  source_types: string[]
  market_cap?: number | null
  summary?: string
}

interface Theme {
  count: number
  tokens: string[]
  theme?: string
  volume_24h?: number
}

interface Wallet {
  wallet: string
  label: string
  edge_count?: number
  hours_since?: number
  status?: 'hot' | 'active' | 'dormant'
}

interface CopyWallet {
  wallet_address: string
  display_name: string
  name_source: string
  name_confidence: number
  copy_trade_score: number
  first_buyer_count: number
  top_trader_count: number
  top_holder_count: number
  deployer_count?: number
  tokens_tracked?: number
  currently_hot_tokens: number
  verified_call_count?: number
  verified_avg_return_pct?: number
  reasoning: string
}

interface ConvictionToken {
  mint: string
  symbol: string
  smart_wallet_count: number
  conviction_score: number
  market_cap?: number | null
  smart_wallets: { wallet: string; display_name: string; copy_trade_score: number }[]
}

interface PlatformLeader {
  platform: string
  available: boolean
  symbol?: string | null
  name?: string | null
  address?: string | null
  metric_label?: string
  metric_value?: number | null
  market_cap?: number | null
  price_usd?: number | string | null
  manipulation_flags?: string[]
  smart_wallet_count?: number
}

// ── Persisted-cache fetch — the fix for "info shows, I navigate away, come
// back and it's gone." Root cause was twofold: (1) this component only ever
// fetched once on mount with no refresh, and (2) a transient upstream
// failure (GeckoTerminal rate limit, etc.) returned an empty array that
// overwrote whatever was showing. Fix: seed state from localStorage
// immediately on mount (so a remount shows last-known-good data instantly,
// not a blank/loading flash), keep polling on an interval, and NEVER let an
// empty/failed response overwrite a non-empty cached value — only a
// genuinely non-empty new response replaces what's shown. ──────────────────
function loadCache<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`vantage_cache_${key}`)
    return raw ? JSON.parse(raw) : fallback
  } catch { return fallback }
}
function saveCache(key: string, value: any) {
  try { localStorage.setItem(`vantage_cache_${key}`, JSON.stringify(value)) } catch { /* quota/full — non-fatal */ }
}

function useCachedList<T>(key: string, endpoint: string, extractKey: string, intervalMs = 45000) {
  const [items, setItems] = useState<T[]>(() => loadCache(key, []))
  const [loading, setLoading] = useState(items.length === 0)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    const k = localStorage.getItem('vantage_api_key') || ''
    try {
      const r = await fetch(endpoint, { headers: { 'X-Agent-Key': k } })
      if (!r.ok) return
      const data = await r.json()
      const next = data?.[extractKey]
      if (Array.isArray(next) && next.length > 0) {
        if (mounted.current) setItems(next)
        saveCache(key, next)
      }
      // an empty/missing result is intentionally ignored — keep showing the
      // last-known-good cached list rather than blanking the section
    } catch { /* offline — keep showing cached data */ }
    if (mounted.current) setLoading(false)
  }, [endpoint, extractKey, key])

  useEffect(() => {
    mounted.current = true
    load()
    const t = setInterval(load, intervalMs)
    return () => { mounted.current = false; clearInterval(t) }
  }, [load, intervalMs])

  return { items, loading, refresh: load }
}

export default function Top5Degen() {
  const mustBuy5 = useCachedList<Token>('top5_mustbuy', '/api/intel/degen/top5?limit=5', 'top_5')
  const mustBuy20 = useCachedList<MustBuyToken>('must_buy_20', '/api/intel/degen/must-buy-20?limit=20', 'must_buy', 60000)
  const smartMoney = useCachedList<Wallet>('smart_wallets', '/api/intel/degen/smart-wallets?limit=5', 'smart_wallets')
  const copyWallets = useCachedList<CopyWallet>('top_wallets_to_copy', '/api/intel/degen/top-wallets-to-copy?limit=10', 'wallets', 120000)
  const convictionTokens = useCachedList<ConvictionToken>('high_conviction', '/api/intel/degen/high-conviction?limit=10', 'tokens', 120000)
  const sellRotations = useCachedList<Token>('sell_rotations', '/api/intel/degen/sell-rotations?limit=5', 'rotations')
  const platformLeaders = useCachedList<PlatformLeader>('platform_leaders', '/api/intel/degen/platform-leaders', 'leaders', 60000)
  const trending = useCachedList<any>('pumpfun_trending', '/api/intel/pumpfun/trending?limit=5', 'trending')

  const [themes, setThemes] = useState<Theme[]>(() => loadCache('themes', []))

  useEffect(() => {
    // volume_24h summed per theme from data already fetched for `trending`
    // (no extra API call) -- a theme's token COUNT alone doesn't say
    // whether it's 5 dead tokens or 5 tokens moving real money.
    const themeMap: Record<string, { count: number; tokens: string[]; volume_24h: number }> = {}
    ;(trending.items || []).forEach((t: any) => {
      const name = (t.name || t.symbol || '').toLowerCase()
      const vol = Number(t.volume_24h) || 0
      const add = (key: string) => {
        themeMap[key] = themeMap[key] || { count: 0, tokens: [], volume_24h: 0 }
        themeMap[key].count++
        themeMap[key].tokens.push(t.symbol || t.name)
        themeMap[key].volume_24h += vol
      }
      if (name.includes('ai') || name.includes('agent')) add('AI Agents')
      else if (name.includes('meme') || name.includes('pepe') || name.includes('bonk')) add('Memes')
      else if (name.includes('bull') || name.includes('moon') || name.includes('100x')) add('Degen')
      else if (name) add('Other')
    })
    if (Object.keys(themeMap).length > 0) {
      const next = Object.entries(themeMap).map(([k, v]) => ({ theme: k, ...v })).sort((a, b) => b.volume_24h - a.volume_24h)
      setThemes(next)
      saveCache('themes', next)
    }
  }, [trending.items])

  const initialLoading = mustBuy5.loading && mustBuy5.items.length === 0 && mustBuy20.loading && mustBuy20.items.length === 0
  if (initialLoading) return <div style={{ padding: 20, color: 'var(--muted)' }}>Loading Top 5...</div>

  return (
    <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 🎯 MUST BUY */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <Flame size={14} color="#ff6b35" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#ff6b35' }}>TOP 5 MUST BUY DEGEN</span>
        </div>
        <div style={{ display: 'grid', gap: 4 }}>
          {mustBuy5.items.map((t, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
              background: `linear-gradient(90deg, rgba(255,107,53,${0.15 - i * 0.03}) 0%, transparent 100%)`,
              borderRadius: 6, border: '1px solid rgba(255,255,255,0.04)',
              fontSize: 11,
            }}>
              <span style={{ color: 'var(--muted)', minWidth: 16 }}>#{i + 1}</span>
              <span style={{ fontWeight: 600, minWidth: 50, color: '#fff' }}><TokenLink symbol={t.symbol} ca={t.address} /></span>
              <span style={{ flex: 1, color: 'var(--muted)', fontSize: 10 }}>{t.reason}</span>
              <span style={{ fontWeight: 600, color: t.score > 70 ? '#22c55e' : '#f59e0b', minWidth: 24, textAlign: 'right' }}>{t.score}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 🚀 20 MUST BUY NOW — cross-source aggregate: trending + persisted trading
          signals + social sentiment + the live intel pool, not just one feed. */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <Rocket size={14} color="#a855f7" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#a855f7' }}>20 MUST BUY NOW</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>cross-source ranked</span>
        </div>
        <div style={{ display: 'grid', gap: 3, maxHeight: 360, overflowY: 'auto' }}>
          {mustBuy20.items.map((t, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px',
              background: `linear-gradient(90deg, rgba(168,85,247,${0.12 - Math.min(i, 20) * 0.005}) 0%, transparent 100%)`,
              borderRadius: 6, border: '1px solid rgba(255,255,255,0.04)',
              fontSize: 11,
            }}>
              <span style={{ color: 'var(--muted)', minWidth: 20 }}>#{i + 1}</span>
              <span style={{ fontWeight: 600, minWidth: 60, color: '#fff' }}><TokenLink symbol={t.symbol} ca={t.ca} /></span>
              <span style={{ display: 'flex', gap: 3 }}>
                {t.source_types.map(s => (
                  <span key={s} title={s} style={{
                    fontSize: 8, padding: '1px 4px', borderRadius: 3,
                    background: s === 'signal' ? 'rgba(34,197,94,0.15)' : s === 'social' ? 'rgba(59,130,246,0.15)' : s === 'pool' ? 'rgba(234,179,8,0.15)' : 'rgba(168,85,247,0.15)',
                    color: s === 'signal' ? '#22c55e' : s === 'social' ? '#3b82f6' : s === 'pool' ? '#eab308' : '#a855f7',
                  }}>{s}</span>
                ))}
              </span>
              <span style={{ flex: 1, textAlign: 'right', color: 'var(--muted)', fontSize: 10 }} title={t.summary}>
                {t.market_cap ? `MC $${(t.market_cap / 1000).toFixed(0)}k` : t.market_cap === null ? 'MC n/a' : ''}
                {t.volume_24h ? ` · Vol $${(t.volume_24h / 1000).toFixed(0)}k` : ''}
                {t.price_change_24h != null ? ` · ${t.price_change_24h > 0 ? '+' : ''}${t.price_change_24h.toFixed(0)}%` : ''}
              </span>
              <span style={{ fontWeight: 700, color: '#a855f7', minWidth: 32, textAlign: 'right' }}>{t.score.toFixed(0)}</span>
            </div>
          ))}
          {mustBuy20.items.length === 0 && (
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>No cross-source candidates yet — fills in as signals/social/trending data accumulates.</div>
          )}
        </div>
      </div>

      {/* 🏦 SMART MONEY — exchanges excluded, both by address_type='exchange'
          tag and a known-CEX-label pattern match server-side. */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <Zap size={14} color="#8b5cf6" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#8b5cf6' }}>TOP 5 SMART MONEY</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>exchanges excluded</span>
        </div>
        <div style={{ display: 'grid', gap: 4 }}>
          {smartMoney.items.map((w, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px',
              borderRadius: 4, border: '1px solid rgba(139,92,246,0.15)', fontSize: 11,
            }}>
              <span style={{ color: 'var(--muted)', fontSize: 10 }}>#{i + 1}</span>
              <span style={{ fontWeight: 600, color: '#8b5cf6', flex: 1, fontSize: 10 }}><WalletLink address={w.wallet} /></span>
              <span style={{ color: 'var(--muted)', fontSize: 10 }}>{w.label}</span>
              {w.status && (
                <span style={{
                  fontSize: 8, padding: '1px 5px', borderRadius: 3,
                  background: w.status === 'hot' ? 'rgba(239,68,68,0.15)' : w.status === 'active' ? 'rgba(34,197,94,0.15)' : 'rgba(148,163,184,0.15)',
                  color: w.status === 'hot' ? '#ef4444' : w.status === 'active' ? '#22c55e' : 'var(--muted)',
                }} title={w.hours_since != null ? `last active ${w.hours_since.toFixed(1)}h ago` : ''}>
                  {w.status}
                </span>
              )}
              <span style={{ fontWeight: 600, fontSize: 10 }} title="wallet-graph edges">{w.edge_count ?? ''}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 🎓 TOP WALLETS TO COPY — wallet_learner.py studies token_wallet_roles
          + social_wallet_links all day: first-buyer/top-trader positioning
          weighted by how many of those tokens are hot right now. Name shown
          only when actually verified (a social self-claim or a human-set
          watchlist label) — blank means genuinely unattributed, not a bug. */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <Users size={14} color="#22d3ee" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#22d3ee' }}>TOP WALLETS TO COPY</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>learned from the money-flow graph</span>
        </div>
        {copyWallets.items.length === 0 ? (
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>No scored wallets yet — fills in as wallet_learner.py studies the graph (runs every 30 min).</div>
        ) : (
          <div style={{ display: 'grid', gap: 4 }}>
            {copyWallets.items.map((w, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
                borderRadius: 6, border: '1px solid rgba(34,211,238,0.15)', fontSize: 11,
              }}>
                <span style={{ color: 'var(--muted)', minWidth: 16 }}>#{i + 1}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontWeight: 600, color: '#22d3ee', fontSize: 10 }}><WalletLink address={w.wallet_address} /></span>
                    {w.display_name && (
                      <span style={{ fontSize: 9, color: '#fff', background: 'rgba(34,211,238,0.15)', borderRadius: 4, padding: '1px 5px' }} title={`${w.name_source} · confidence ${w.name_confidence}`}>
                        {w.display_name}
                      </span>
                    )}
                    {w.currently_hot_tokens > 0 && (
                      <span style={{ fontSize: 9, color: '#f59e0b' }}>🔥{w.currently_hot_tokens}</span>
                    )}
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>{w.reasoning}</div>
                  <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {!!w.verified_call_count && (
                      <span title="verified calls vs their realized outcome">
                        📈 {w.verified_call_count} calls, avg {(w.verified_avg_return_pct ?? 0) > 0 ? '+' : ''}{(w.verified_avg_return_pct ?? 0).toFixed(0)}%
                      </span>
                    )}
                    {!!w.tokens_tracked && <span>{w.tokens_tracked} tokens tracked</span>}
                    {!!w.deployer_count && <span style={{ color: '#f59e0b' }} title="also deployed tokens -- weigh copy-trades accordingly">⚠️ {w.deployer_count} deploys</span>}
                  </div>
                </div>
                <span style={{ fontWeight: 700, color: '#22d3ee', fontSize: 12, minWidth: 32, textAlign: 'right' }}>{w.copy_trade_score.toFixed(0)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 💎 HIGH CONVICTION TOKENS — pure join of token_wallet_roles against
          wallet_reputation, zero new external dependency: which tokens
          already have known-good wallets positioned in them, right now. */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <DollarSign size={14} color="#22d3ee" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#22d3ee' }}>HIGH CONVICTION TOKENS</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>smart-money overlap</span>
        </div>
        {convictionTokens.items.length === 0 ? (
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>No overlap detected yet — fills in as more wallets get scored and connected to tokens.</div>
        ) : (
          <div style={{ display: 'grid', gap: 4 }}>
            {convictionTokens.items.map((t, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
                borderRadius: 6, border: '1px solid rgba(34,211,238,0.15)', fontSize: 11,
              }}>
                <span style={{ color: 'var(--muted)', minWidth: 16 }}>#{i + 1}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600, color: '#fff' }}><TokenLink symbol={t.symbol} ca={t.mint} /></span>
                  <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 1 }} title={t.smart_wallets.map(w => w.display_name || w.wallet.slice(0, 6)).join(', ')}>
                    {t.market_cap ? `MC $${t.market_cap.toLocaleString(undefined, { maximumFractionDigits: 0 })} · ` : t.market_cap === null ? 'MC n/a · ' : ''}
                    {t.smart_wallet_count} wallet{t.smart_wallet_count === 1 ? '' : 's'}
                    {t.smart_wallets.length > 0 && `: ${t.smart_wallets.slice(0, 2).map(w => w.display_name || w.wallet.slice(0, 4) + '…').join(', ')}${t.smart_wallets.length > 2 ? ` +${t.smart_wallets.length - 2}` : ''}`}
                  </div>
                </div>
                <span style={{ fontWeight: 700, color: '#22d3ee', fontSize: 12, minWidth: 36, textAlign: 'right' }}>{t.conviction_score.toFixed(0)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 🎨 THEMES */}
      {themes.length > 0 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <Radio size={14} color="#06b6d4" />
            <span style={{ fontWeight: 700, fontSize: 13, color: '#06b6d4' }}>TOP THEMES</span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {themes.map((th, i) => (
              <div key={i} style={{
                padding: '4px 10px', borderRadius: 12,
                background: `rgba(6,182,212,${0.2 - i * 0.04})`,
                border: '1px solid rgba(6,182,212,0.2)', fontSize: 10,
                display: 'flex', alignItems: 'center', gap: 4,
              }}>
                <span style={{ fontWeight: 600, color: '#06b6d4' }}>{th.theme}</span>
                <span style={{ color: 'var(--muted)' }}>{th.count}</span>
                {!!th.volume_24h && (
                  <span style={{ color: 'var(--muted)', fontSize: 9 }} title="combined 24h volume">
                    ${th.volume_24h >= 1000 ? `${(th.volume_24h / 1000).toFixed(0)}k` : th.volume_24h.toFixed(0)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 📊 GRADUATED */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <TrendingUp size={14} color="#22c55e" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#22c55e' }}>RECENTLY GRADUATED</span>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {mustBuy5.items.filter(t => t.graduated).slice(0, 3).map((t, i) => (
            <div key={i} style={{
              padding: '4px 10px', borderRadius: 6,
              background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.2)',
              fontSize: 10, display: 'flex', alignItems: 'center', gap: 4,
            }}>
              <span>🎓</span>
              <span style={{ fontWeight: 600, color: '#22c55e' }}><TokenLink symbol={t.symbol} ca={t.address} /></span>
              <span style={{ color: 'var(--muted)' }}>
                {t.market_cap ? `MC $${t.market_cap.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : `Vol $${(t.volume_24h || 0).toLocaleString()}`}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* 🔴 ROTATIONS */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <AlertTriangle size={14} color="#ef4444" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#ef4444' }}>SELL ROTATIONS</span>
        </div>
        {sellRotations.items.length > 0 ? (
          <div style={{ display: 'grid', gap: 4 }}>
            {sellRotations.items.map((t, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px',
                borderRadius: 4, border: '1px solid rgba(239,68,68,0.15)', fontSize: 11,
              }}>
                <span style={{ fontWeight: 600, minWidth: 50, color: '#ef4444' }}><TokenLink symbol={t.symbol} ca={t.address} /></span>
                <span style={{ flex: 1, color: 'var(--muted)', fontSize: 10 }}>
                  {t.reason || `Sell/Buy: ${t.sell_buy_ratio ?? '?'}x — Vol $${(t.volume_24h || 0).toLocaleString()}`}
                </span>
                <span style={{ color: t.price_change_24h && t.price_change_24h < 0 ? '#ef4444' : '#22c55e', fontSize: 10 }}>
                  {t.price_change_24h != null ? (t.price_change_24h > 0 ? '+' : '') + t.price_change_24h + '%' : ''}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>No significant sell rotations detected</div>
        )}
      </div>

      {/* 🏆 PLATFORM LEADERS — #1 top-ranked token from each of the 6 real
          platforms Vantage gathers token intel from, each platform's OWN
          native ranking metric (not a Vantage cross-platform score — see
          the aggregate-score banner elsewhere on this page for that). A
          platform showing "unavailable" means that source genuinely had no
          data or was unreachable at request time, not a fabricated row. */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
          <Zap size={14} color="#eab308" />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#eab308' }}>PLATFORM LEADERS</span>
          <span style={{ fontSize: 9, color: 'var(--muted)' }}>#1 pick per platform, native ranking</span>
        </div>
        <div style={{ display: 'grid', gap: 4 }}>
          {platformLeaders.items.map((p, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
              borderRadius: 6, border: '1px solid rgba(234,179,8,0.15)', fontSize: 11,
              opacity: p.available ? 1 : 0.45,
            }}>
              <span style={{ fontWeight: 700, minWidth: 130, color: '#eab308' }}>{p.platform}</span>
              {p.available ? (
                <>
                  <span style={{ fontWeight: 600, minWidth: 70, color: '#fff' }}>
                    {/* Real bug: CoinGecko's leader has no Solana address
                        (global-market lookup, not Solana-specific), so this
                        fell back to plain unclickable text while all 5 other
                        platforms rendered a clickable TokenLink. TokenLink's
                        `ca` prop is already optional -- EntityProfileCard
                        gracefully shows just symbol info (no trade panel)
                        when ca is absent -- so there was never a reason to
                        special-case the no-address case here at all. */}
                    {(p.symbol || p.name) ? <TokenLink symbol={p.symbol || p.name || '?'} ca={p.address || undefined} /> : '—'}
                  </span>
                  <span style={{ flex: 1, color: 'var(--muted)', fontSize: 10 }}>
                    {p.metric_label}: <b style={{ color: '#fff' }}>{p.metric_value != null ? p.metric_value : '—'}</b>
                    {p.market_cap != null && <> · MC ${Number(p.market_cap).toLocaleString(undefined, { maximumFractionDigits: 0 })}</>}
                  </span>
                  {p.manipulation_flags && p.manipulation_flags.length > 0 && (
                    <span title={p.manipulation_flags.join(', ')} style={{ color: '#ef4444', fontSize: 9, fontWeight: 700 }}>⚠ FLAGGED</span>
                  )}
                </>
              ) : (
                <span style={{ flex: 1, color: 'var(--muted)', fontSize: 10, fontStyle: 'italic' }}>unavailable — source unreachable or no data</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
