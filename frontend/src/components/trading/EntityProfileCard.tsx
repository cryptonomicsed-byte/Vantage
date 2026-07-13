import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react'
import { X, ExternalLink, Wallet as WalletIcon, Coins } from 'lucide-react'

/**
 * Entity Profile Card — one shared, reusable "click any token or wallet
 * anywhere in Trading and get a profile card with links and info" surface.
 *
 * Usage:
 *   <ProfileCardProvider> ...rest of the trading UI... </ProfileCardProvider>
 *   <TokenLink symbol="WIF" ca={mint} chain="solana" />
 *   <WalletLink address={addr} chain="solana" label="Binance 4 (SOL)" />
 *
 * Both are drop-in replacements for plain text — same visual weight by
 * default, just clickable. The card itself is a single global overlay (one
 * instance for the whole Trading section, mounted once via the provider) so
 * every tab/section shares the same component instead of each re-building
 * its own popover.
 */

type TokenTarget = { kind: 'token'; symbol: string; ca?: string; chain?: string }
type WalletTarget = { kind: 'wallet'; address: string; chain?: string; label?: string }
type Target = TokenTarget | WalletTarget

interface Ctx {
  open: (t: Target) => void
}
const ProfileCardCtx = createContext<Ctx | null>(null)

function useProfileCard(): Ctx {
  const ctx = useContext(ProfileCardCtx)
  if (!ctx) throw new Error('TokenLink/WalletLink used outside <ProfileCardProvider>')
  return ctx
}

// ── External link generation (mirrors MoneyFlowGraph.tsx's externalLinks) ──
function tokenLinks(chain: string, ca?: string, symbol?: string): { label: string; url: string }[] {
  const c = (chain || 'solana').toLowerCase()
  if (ca) {
    if (c === 'solana') {
      return [
        { label: 'DexScreener', url: `https://dexscreener.com/solana/${ca}` },
        { label: 'Birdeye', url: `https://birdeye.so/token/${ca}?chain=solana` },
        { label: 'Solscan', url: `https://solscan.io/token/${ca}` },
        { label: 'Pump.fun', url: `https://pump.fun/${ca}` },
      ]
    }
    if (c === 'ethereum' || c === 'eth') {
      return [
        { label: 'DexScreener', url: `https://dexscreener.com/ethereum/${ca}` },
        { label: 'Etherscan', url: `https://etherscan.io/token/${ca}` },
      ]
    }
    if (c === 'base') {
      return [
        { label: 'DexScreener', url: `https://dexscreener.com/base/${ca}` },
        { label: 'Basescan', url: `https://basescan.org/token/${ca}` },
      ]
    }
  }
  // No CA resolved yet — fall back to a symbol search, still useful.
  if (symbol) {
    return [{ label: 'DexScreener Search', url: `https://dexscreener.com/search?q=${encodeURIComponent(symbol)}` }]
  }
  return []
}

function walletLinks(chain: string, address: string): { label: string; url: string }[] {
  const c = (chain || 'solana').toLowerCase()
  if (c === 'solana') {
    return [
      { label: 'Solscan', url: `https://solscan.io/account/${address}` },
      { label: 'Birdeye', url: `https://birdeye.so/profile/${address}?chain=solana` },
    ]
  }
  if (c === 'bitcoin' || c === 'btc') {
    return [{ label: 'mempool.space', url: `https://mempool.space/address/${address}` }]
  }
  if (c === 'ethereum' || c === 'eth') {
    return [{ label: 'Etherscan', url: `https://etherscan.io/address/${address}` }]
  }
  if (c === 'base') {
    return [{ label: 'Basescan', url: `https://basescan.org/address/${address}` }]
  }
  return [{ label: 'Solscan', url: `https://solscan.io/account/${address}` }]
}

// ── Data fetched on open — best-effort, never blocks the card from showing.
function useEntityInfo(target: Target | null) {
  const [info, setInfo] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!target) { setInfo(null); return }
    setInfo(null)
    setLoading(true)
    const key = localStorage.getItem('vantage_api_key') || ''
    const controller = new AbortController()
    ;(async () => {
      try {
        if (target.kind === 'token') {
          const q = target.ca || target.symbol
          const r = await fetch(`/api/intel/dex?q=${encodeURIComponent(q)}`, { signal: controller.signal })
          const info: any = {}
          if (r.ok) {
            const d = await r.json()
            const best = (d.pairs || [])[0]
            if (best) Object.assign(info, { price_usd: best.price_usd, liquidity_usd: best.liquidity_usd, volume_24h: best.volume_24h, change_24h: best.change_24h, dex: best.dex })
          }
          // Smart-money overlap — real wallets wallet_learner.py has scored,
          // that are also connected to this specific token.
          if (target.ca) {
            try {
              const cr = await fetch(`/api/intel/degen/conviction/${encodeURIComponent(target.ca)}`, {
                signal: controller.signal, headers: { 'X-Agent-Key': key },
              })
              if (cr.ok) {
                const cd = await cr.json()
                if (cd.smart_wallet_count > 0) Object.assign(info, { conviction_score: cd.conviction_score, smart_wallet_count: cd.smart_wallet_count, smart_wallets: cd.smart_wallets })
              }
            } catch { /* best-effort */ }
          }
          if (Object.keys(info).length > 0) setInfo(info)
        } else {
          const chain = target.chain === 'ethereum' || target.chain === 'base' ? null : (target.chain || 'solana')
          if (chain) {
            // Real SOL balance + every SPL token held, live-priced — not
            // just a truncated trace summary.
            const r = await fetch(`/api/intel/wallet-holdings/${chain}/${encodeURIComponent(target.address)}`, {
              signal: controller.signal, headers: { 'X-Agent-Key': key },
            })
            if (r.ok) {
              const d = await r.json()
              if (d.supported) {
                setInfo({
                  sol_balance: d.sol_balance, sol_value_usd: d.sol_value_usd,
                  tokens: d.tokens, token_count: d.token_count, total_value_usd: d.total_value_usd,
                  display_name: d.display_name, name_source: d.name_source,
                })
              }
            }
          }
        }
      } catch { /* best-effort — card still shows with just links */ }
      setLoading(false)
    })()
    return () => controller.abort()
  }, [target && (target.kind === 'token' ? target.ca || target.symbol : target.address)])
  return { info, loading }
}

function ProfileCardOverlay({ target, onClose }: { target: Target; onClose: () => void }) {
  const { info, loading } = useEntityInfo(target)
  const isToken = target.kind === 'token'
  const links = isToken
    ? tokenLinks(target.chain || 'solana', target.ca, target.symbol)
    : walletLinks(target.chain || 'solana', target.address)
  const title = isToken ? target.symbol : (target.label || `${target.address.slice(0, 6)}…${target.address.slice(-4)}`)

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 'min(380px, 92vw)', background: 'rgba(8,8,18,0.98)', border: '1px solid rgba(139,92,246,0.3)',
          borderRadius: 14, padding: '18px 20px', backdropFilter: 'blur(16px)', boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isToken ? <Coins size={16} color="#a855f7" /> : <WalletIcon size={16} color="#3b82f6" />}
            <div>
              <div style={{ fontSize: 10, color: isToken ? '#a855f7' : '#3b82f6', letterSpacing: 1, textTransform: 'uppercase', fontWeight: 700 }}>
                {isToken ? 'Token' : 'Wallet'}
              </div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#fff', fontFamily: isToken ? 'inherit' : 'monospace' }}>{title}</div>
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm" style={{ padding: '2px 6px' }}><X size={14} /></button>
        </div>

        {isToken && target.ca && (
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,.4)', fontFamily: 'monospace', wordBreak: 'break-all', marginBottom: 10 }}>{target.ca}</div>
        )}
        {!isToken && (
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,.4)', fontFamily: 'monospace', wordBreak: 'break-all', marginBottom: 10 }}>{target.address}</div>
        )}
        {!isToken && info?.display_name && (
          <div style={{ fontSize: 12, color: '#22d3ee', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontWeight: 700 }}>{info.display_name}</span>
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,.4)' }}>({info.name_source === 'sns_domain' ? 'SNS domain' : info.name_source === 'known_program' ? 'known program' : info.name_source})</span>
          </div>
        )}

        {loading && <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>Loading live info…</div>}

        {isToken && info && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'grid', gap: 4 }}>
              {info.price_usd != null && <Row label="Price" value={`$${Number(info.price_usd).toPrecision(4)}`} />}
              {info.liquidity_usd != null && <Row label="Liquidity" value={`$${(info.liquidity_usd / 1e3).toFixed(0)}K`} />}
              {info.volume_24h != null && <Row label="Volume 24h" value={`$${(info.volume_24h / 1e3).toFixed(0)}K`} />}
              {info.change_24h != null && <Row label="24h Change" value={`${info.change_24h > 0 ? '+' : ''}${info.change_24h.toFixed(1)}%`} color={info.change_24h >= 0 ? '#22c55e' : '#ef4444'} />}
              {info.dex && <Row label="DEX" value={info.dex} />}
            </div>
            {info.smart_wallet_count > 0 && (
              <div style={{ marginTop: 10, padding: '8px 10px', background: 'rgba(34,211,238,0.08)', border: '1px solid rgba(34,211,238,0.2)', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#22d3ee', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
                  Smart Money Exposure: {info.smart_wallet_count} wallet{info.smart_wallet_count === 1 ? '' : 's'}
                </div>
                {info.smart_wallets?.slice(0, 3).map((w: any, i: number) => (
                  <div key={i} style={{ fontSize: 10, color: 'rgba(255,255,255,.7)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{w.display_name || `${w.wallet.slice(0, 4)}…${w.wallet.slice(-4)}`}</span>
                    <span style={{ color: '#22d3ee' }}>{w.copy_trade_score.toFixed(0)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {!isToken && info && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'grid', gap: 4, marginBottom: 8 }}>
              {info.sol_balance != null && <Row label="SOL Balance" value={`${info.sol_balance} SOL${info.sol_value_usd != null ? ` ($${info.sol_value_usd})` : ''}`} />}
              {info.total_value_usd != null && <Row label="Total Value" value={`$${info.total_value_usd.toLocaleString()}`} color="#22c55e" />}
              {info.token_count != null && <Row label="Tokens Held" value={String(info.token_count)} />}
            </div>
            {info.tokens && info.tokens.length > 0 && (
              <div style={{ maxHeight: 160, overflowY: 'auto', border: '1px solid rgba(255,255,255,.06)', borderRadius: 8, padding: '6px 8px' }}>
                {info.tokens.map((t: any, i: number) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, padding: '3px 0', borderBottom: i < info.tokens.length - 1 ? '1px solid rgba(255,255,255,.04)' : 'none' }}>
                    <span style={{ color: '#fff' }}>{t.symbol}</span>
                    <span style={{ fontFamily: 'monospace', color: 'var(--muted)' }}>
                      {Number(t.amount).toLocaleString(undefined, { maximumFractionDigits: 4 })}
                      {t.value_usd != null && <span style={{ color: '#22c55e', marginLeft: 6 }}>${t.value_usd.toLocaleString()}</span>}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {!loading && !info && (
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12 }}>No live data available — use the links below.</div>
        )}

        {links.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {links.map(l => (
              <a key={l.url} href={l.url} target="_blank" rel="noreferrer"
                 style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 10px', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', borderRadius: 6, color: '#b9a8ff', textDecoration: 'none', fontSize: 11 }}>
                {l.label} <ExternalLink size={10} />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
      <span style={{ color: 'var(--muted)' }}>{label}</span>
      <span style={{ fontWeight: 600, color: color || '#fff', fontFamily: 'monospace' }}>{value}</span>
    </div>
  )
}

export function ProfileCardProvider({ children }: { children: React.ReactNode }) {
  const [target, setTarget] = useState<Target | null>(null)
  const open = useCallback((t: Target) => setTarget(t), [])
  const close = useCallback(() => setTarget(null), [])
  return (
    <ProfileCardCtx.Provider value={{ open }}>
      {children}
      {target && <ProfileCardOverlay target={target} onClose={close} />}
    </ProfileCardCtx.Provider>
  )
}

// ── Drop-in clickable components ────────────────────────────────────────────
export function TokenLink({ symbol, ca, chain = 'solana', style }: { symbol: string; ca?: string; chain?: string; style?: React.CSSProperties }) {
  const { open } = useProfileCard()
  if (!symbol) return null
  return (
    <span
      onClick={e => { e.stopPropagation(); open({ kind: 'token', symbol, ca, chain }) }}
      style={{ cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'rgba(168,85,247,0.35)', textUnderlineOffset: 2, ...style }}
      title={`View ${symbol} profile`}
    >
      {symbol}
    </span>
  )
}

export function WalletLink({ address, chain = 'solana', label, style, truncate = true }: { address: string; chain?: string; label?: string; style?: React.CSSProperties; truncate?: boolean }) {
  const { open } = useProfileCard()
  if (!address) return null
  const display = label || (truncate ? `${address.slice(0, 6)}…${address.slice(-4)}` : address)
  return (
    <span
      onClick={e => { e.stopPropagation(); open({ kind: 'wallet', address, chain, label }) }}
      style={{ cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'rgba(59,130,246,0.35)', textUnderlineOffset: 2, fontFamily: truncate && !label ? 'monospace' : 'inherit', ...style }}
      title={`View ${address} profile`}
    >
      {display}
    </span>
  )
}

// For places where the primary click is already used for something else
// (e.g. loading a chart) — a small non-intrusive info affordance instead.
export function TokenInfoIcon({ symbol, ca, chain = 'solana' }: { symbol: string; ca?: string; chain?: string }) {
  const { open } = useProfileCard()
  return (
    <button
      onClick={e => { e.stopPropagation(); open({ kind: 'token', symbol, ca, chain }) }}
      className="btn btn-ghost btn-sm"
      style={{ padding: '1px 4px', fontSize: 9, opacity: 0.6 }}
      title={`View ${symbol} profile`}
    >
      ⓘ
    </button>
  )
}
