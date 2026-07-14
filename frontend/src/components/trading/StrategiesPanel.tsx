import React, { useState, useEffect, useCallback } from 'react'
import { Zap, Wallet, ShieldCheck, ShieldOff, Trash2, Plus, X, Edit3 } from 'lucide-react'

type TradingApiFn = (path: string, opts?: RequestInit) => Promise<Response>

// ══════════════════════════════════════════════════════════════════════════════
// StrategiesPanel — full CRUD over trading_strategies. Every strategy is
// clickable to edit (wallet, position size, stop-loss/take-profit, config).
// Create from one of the 8 canned templates (4 have real execution
// processors in strategy_bots.py — scalper_5020/bighit_40_800/
// accumulator_tiered/doubler_flip; the other 4 — swing_momentum/
// moonbag_tiered/copytrade_mirror/balanced_alloc — are editable/creatable
// but have no executor yet, flagged clearly rather than pretending they run).
// ══════════════════════════════════════════════════════════════════════════════

const LIVE_TYPES = new Set(['scalper_5020', 'bighit_40_800', 'accumulator_tiered', 'doubler_flip'])

// ── Daemon Settings — wallet pickers for the two standalone daemons
// (ares_pumpfun_trader.py, degen_alpha_fusion.py's snipe_token) that run
// outside this app entirely. They poll GET /daemon-settings/{key} each
// cycle, so a change here takes effect within one cycle — no SSH, no
// systemd restart, no env file editing.
const DAEMON_SETTINGS = [
  { key: 'pumpfun_trader_wallet_id', label: 'Pumpfun Trader', desc: 'ares_pumpfun_trader.py — auto-buys pump.fun tokens flagged safe by degen_alpha_fusion/ogun_multiscan' },
  { key: 'snipe_wallet_id', label: 'Degen Snipe', desc: "degen_alpha_fusion.py's moonshot auto-snipe (score > 60)" },
]

function DaemonSettingsPanel({ wallets, tradingApi }: { wallets: any[]; tradingApi: TradingApiFn }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})

  useEffect(() => {
    (async () => {
      const r = await tradingApi('/daemon-settings')
      if (r.ok) {
        const d = await r.json()
        const next: Record<string, string> = {}
        for (const k of Object.keys(d)) next[k] = d[k].value || ''
        setValues(next)
      }
    })()
  }, [tradingApi])

  async function save(key: string, value: string) {
    setValues(v => ({ ...v, [key]: value }))
    setSaved(s => ({ ...s, [key]: false }))
    if (!value) return
    const r = await tradingApi(`/daemon-settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) })
    setSaved(s => ({ ...s, [key]: r.ok }))
  }

  return (
    <div style={{ marginBottom: 16, padding: 12, background: 'rgba(138,75,255,0.05)', border: '1px solid rgba(138,75,255,0.15)', borderRadius: 10 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#8a4bff', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
        Auto-Trading Daemon Wallets
      </div>
      <div style={{ display: 'grid', gap: 8 }}>
        {DAEMON_SETTINGS.map(ds => (
          <div key={ds.key}>
            <div style={{ fontSize: 11, color: '#e0e0e0', fontWeight: 600, marginBottom: 2 }}>{ds.label}</div>
            <div style={{ fontSize: 9, color: 'rgba(255,255,255,.4)', marginBottom: 4 }}>{ds.desc}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <select value={values[ds.key] || ''} onChange={e => save(ds.key, e.target.value)}
                style={{ flex: 1, background: 'rgba(255,255,255,.05)', border: '1px solid rgba(255,255,255,.1)', borderRadius: 6, color: '#fff', fontSize: 11, padding: '5px 6px' }}>
                <option value="">No wallet — execution disabled</option>
                {wallets.map(w => <option key={w.id} value={w.id}>{w.label} ({w.address.slice(0, 4)}…{w.address.slice(-4)})</option>)}
              </select>
              {saved[ds.key] && <span style={{ fontSize: 10, color: '#39ff14' }}>✓ saved</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function StrategiesPanel({ tradingApi }: { tradingApi: TradingApiFn }) {
  const [strategies, setStrategies] = useState<any[]>([])
  const [templates, setTemplates] = useState<Record<string, any>>({})
  const [wallets, setWallets] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<any | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(async () => {
    try {
      const [sr, tr, wr] = await Promise.all([
        tradingApi('/strategies'),
        tradingApi('/strategies/templates'),
        tradingApi('/wallets'),
      ])
      if (sr.ok) setStrategies(await sr.json())
      if (tr.ok) setTemplates((await tr.json()).templates || {})
      if (wr.ok) {
        const d = await wr.json()
        setWallets(Array.isArray(d) ? d : (d.wallets || []))
      }
    } catch { /* best-effort */ }
    setLoading(false)
  }, [tradingApi])

  useEffect(() => { load() }, [load])

  async function armToggle(s: any) {
    const r = await tradingApi(`/strategies/${s.id}/${s.armed ? 'disarm' : 'arm'}`, { method: 'POST' })
    const d = await r.json().catch(() => ({}))
    if (r.ok) {
      if (d.warning) alert(d.warning)
      load()
    } else {
      alert(d.detail || 'Failed')
    }
  }

  async function deleteStrategy(id: number) {
    if (!confirm('Delete this strategy? This cannot be undone.')) return
    await tradingApi(`/strategies/${id}`, { method: 'DELETE' })
    load()
  }

  if (loading) return <div style={{ padding: 20, color: '#6b7280' }}>Loading strategies...</div>

  return (
    <div style={{ padding: '0 16px' }}>
      <DaemonSettingsPanel wallets={wallets.filter(w => w.chain === 'solana')} tradingApi={tradingApi} />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#e0e0e0' }}>Strategies</div>
        <button onClick={() => setShowCreate(true)}
          style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 10px', background: 'rgba(0,245,255,0.12)', border: '1px solid rgba(0,245,255,0.3)', borderRadius: 6, color: '#00f5ff', fontSize: 11, cursor: 'pointer' }}>
          <Plus size={12} /> New from template
        </button>
      </div>

      {strategies.length === 0 ? (
        <div style={{ color: '#6b7280', fontSize: 12 }}>No strategies yet — create one from a template.</div>
      ) : (
        <div style={{ display: 'grid', gap: 8 }}>
          {strategies.map(s => {
            const wallet = wallets.find(w => w.id === s.wallet_id)
            const hasExecutor = LIVE_TYPES.has(s.strategy_type)
            return (
              <div key={s.id} style={{
                background: 'rgba(255,255,255,0.03)', border: `1px solid ${s.armed ? 'rgba(34,197,94,0.3)' : 'rgba(255,255,255,0.08)'}`,
                borderRadius: 8, padding: 12,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#fff' }}>{s.name}</span>
                  <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(255,255,255,0.06)', color: '#9ca3af' }}>{s.strategy_type}</span>
                  {!hasExecutor && (
                    <span title="No execution processor yet — editable but won't trade" style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(255,170,0,0.12)', color: '#ffaa00' }}>
                      no executor
                    </span>
                  )}
                  {s.armed ? (
                    <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(34,197,94,0.15)', color: '#39ff14' }}>ARMED</span>
                  ) : null}
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                    <button onClick={() => setEditing(s)} title="Edit" style={iconBtnStyle}><Edit3 size={13} /></button>
                    <button onClick={() => armToggle(s)} title={s.armed ? 'Disarm' : 'Arm'} style={{ ...iconBtnStyle, color: s.armed ? '#ff2d4a' : '#39ff14' }}>
                      {s.armed ? <ShieldOff size={13} /> : <ShieldCheck size={13} />}
                    </button>
                    <button onClick={() => deleteStrategy(s.id)} title="Delete" style={{ ...iconBtnStyle, color: '#ff2d4a' }}><Trash2 size={13} /></button>
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 14, fontSize: 11, color: '#9ca3af', flexWrap: 'wrap' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Wallet size={11} /> {wallet ? wallet.label : <span style={{ color: '#ff2d4a' }}>no wallet linked</span>}
                  </span>
                  <span>Max: ${s.max_position_size_usd || 0}</span>
                  {s.stop_loss_pct != null && <span style={{ color: '#ff2d4a' }}>SL {s.stop_loss_pct}%</span>}
                  {s.take_profit_pct != null && <span style={{ color: '#39ff14' }}>TP +{s.take_profit_pct}%</span>}
                  <span>Risk {s.risk_per_trade_pct}%/trade</span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {editing && (
        <EditStrategyModal strategy={editing} wallets={wallets} tradingApi={tradingApi} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />
      )}
      {showCreate && (
        <CreateStrategyModal templates={templates} wallets={wallets} tradingApi={tradingApi} onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); load() }} />
      )}
    </div>
  )
}

const iconBtnStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 5,
  color: '#9ca3af', cursor: 'pointer', padding: '4px 6px', display: 'flex', alignItems: 'center',
}

function EditStrategyModal({ strategy, wallets, tradingApi, onClose, onSaved }: { strategy: any; wallets: any[]; tradingApi: TradingApiFn; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(strategy.name)
  const [walletId, setWalletId] = useState<string>(strategy.wallet_id ? String(strategy.wallet_id) : '')
  const [maxUsd, setMaxUsd] = useState(String(strategy.max_position_size_usd || 0))
  const [riskPct, setRiskPct] = useState(String(strategy.risk_per_trade_pct || 2))
  const [sl, setSl] = useState(strategy.stop_loss_pct != null ? String(strategy.stop_loss_pct) : '')
  const [tp, setTp] = useState(strategy.take_profit_pct != null ? String(strategy.take_profit_pct) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    setBusy(true); setError('')
    try {
      const r = await tradingApi(`/strategies/${strategy.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          wallet_id: walletId ? Number(walletId) : null,
          max_position_size_usd: Number(maxUsd),
          risk_per_trade_pct: Number(riskPct),
          stop_loss_pct: sl === '' ? null : Number(sl),
          take_profit_pct: tp === '' ? null : Number(tp),
        }),
      })
      if (r.ok) onSaved()
      else { const d = await r.json(); setError(d.detail || 'Save failed') }
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setBusy(false)
  }

  return (
    <ModalShell title={`Edit: ${strategy.name}`} onClose={onClose}>
      <Field label="Name"><input value={name} onChange={e => setName(e.target.value)} style={inputStyle} /></Field>
      <Field label="Wallet (required to arm)">
        <select value={walletId} onChange={e => setWalletId(e.target.value)} style={inputStyle}>
          <option value="">No wallet</option>
          {wallets.filter(w => w.chain === 'solana').map(w => <option key={w.id} value={w.id}>{w.label} ({w.address.slice(0, 6)}…)</option>)}
        </select>
      </Field>
      <div style={{ display: 'flex', gap: 8 }}>
        <Field label="Max position ($)"><input type="number" value={maxUsd} onChange={e => setMaxUsd(e.target.value)} style={inputStyle} /></Field>
        <Field label="Risk % / trade"><input type="number" value={riskPct} onChange={e => setRiskPct(e.target.value)} style={inputStyle} /></Field>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Field label="Stop-loss %"><input type="number" value={sl} onChange={e => setSl(e.target.value)} placeholder="none" style={inputStyle} /></Field>
        <Field label="Take-profit %"><input type="number" value={tp} onChange={e => setTp(e.target.value)} placeholder="none" style={inputStyle} /></Field>
      </div>
      {error && <div style={{ fontSize: 11, color: '#ff2d4a', marginBottom: 8 }}>{error}</div>}
      <button onClick={save} disabled={busy} style={saveBtnStyle}>{busy ? 'Saving…' : 'Save changes'}</button>
    </ModalShell>
  )
}

type CreateMode = 'template' | 'ai' | 'custom'

function CreateStrategyModal({ templates, wallets, tradingApi, onClose, onCreated }: { templates: Record<string, any>; wallets: any[]; tradingApi: TradingApiFn; onClose: () => void; onCreated: () => void }) {
  const [mode, setMode] = useState<CreateMode>('template')
  const solWallets = wallets.filter(w => w.chain === 'solana')

  return (
    <ModalShell title="New Strategy" onClose={onClose}>
      <div style={{ display: 'flex', gap: 4, marginBottom: 14, background: 'rgba(255,255,255,.04)', borderRadius: 8, padding: 3 }}>
        {(['template', 'ai', 'custom'] as CreateMode[]).map(m => (
          <button key={m} onClick={() => setMode(m)} style={{
            flex: 1, padding: '6px 0', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 11, fontWeight: 700,
            background: mode === m ? 'rgba(0,245,255,0.15)' : 'transparent', color: mode === m ? '#00f5ff' : '#9ca3af',
          }}>
            {m === 'template' ? 'From Template' : m === 'ai' ? 'Describe It (AI)' : 'Full Custom'}
          </button>
        ))}
      </div>
      {mode === 'template' && <TemplateMode templates={templates} wallets={solWallets} tradingApi={tradingApi} onCreated={onCreated} />}
      {mode === 'ai' && <AiDescribeMode templates={templates} wallets={solWallets} tradingApi={tradingApi} onCreated={onCreated} />}
      {mode === 'custom' && <CustomMode templates={templates} wallets={solWallets} tradingApi={tradingApi} onCreated={onCreated} />}
    </ModalShell>
  )
}

function TemplateMode({ templates, wallets, tradingApi, onCreated }: { templates: Record<string, any>; wallets: any[]; tradingApi: TradingApiFn; onCreated: () => void }) {
  const templateKeys = Object.keys(templates)
  const [templateKey, setTemplateKey] = useState(templateKeys[0] || '')
  const [name, setName] = useState('')
  const [walletId, setWalletId] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const tpl = templates[templateKey]

  async function create() {
    if (!walletId) { setError('Pick a wallet'); return }
    setBusy(true); setError('')
    try {
      const r = await tradingApi(`/strategies/from-template?wallet_id=${walletId}`, {
        method: 'POST',
        body: JSON.stringify({ template: templateKey, name: name || undefined }),
      })
      if (r.ok) onCreated()
      else { const d = await r.json(); setError(d.detail || 'Create failed') }
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setBusy(false)
  }

  return (
    <>
      <Field label="Template">
        <select value={templateKey} onChange={e => setTemplateKey(e.target.value)} style={inputStyle}>
          {templateKeys.map(k => <option key={k} value={k}>{templates[k].label}</option>)}
        </select>
      </Field>
      {tpl && <div style={{ fontSize: 10, color: 'rgba(255,255,255,.5)', marginBottom: 10 }}>{tpl.description}</div>}
      {tpl && !LIVE_TYPES.has(tpl.strategy_type) && (
        <div style={{ fontSize: 10, color: '#ffaa00', marginBottom: 10, padding: '6px 8px', background: 'rgba(255,170,0,0.08)', borderRadius: 6 }}>
          ⚠️ No execution processor yet for this type — you can create/edit it, but arming it won't trade until one is built.
        </div>
      )}
      <Field label="Name (optional)"><input value={name} onChange={e => setName(e.target.value)} placeholder={tpl?.label} style={inputStyle} /></Field>
      <Field label="Wallet">
        <select value={walletId} onChange={e => setWalletId(e.target.value)} style={inputStyle}>
          <option value="">Select wallet…</option>
          {wallets.map(w => <option key={w.id} value={w.id}>{w.label} ({w.address.slice(0, 6)}…)</option>)}
        </select>
      </Field>
      {error && <div style={{ fontSize: 11, color: '#ff2d4a', marginBottom: 8 }}>{error}</div>}
      <button onClick={create} disabled={busy} style={saveBtnStyle}>{busy ? 'Creating…' : 'Create strategy'}</button>
    </>
  )
}

// ── Describe It (AI) — natural language -> best-fit REAL engine + params,
// via POST /strategies/generate (Instructor+DeepSeek). Always maps onto one
// of the 4 executable engines, never invents a non-executable type.
function AiDescribeMode({ templates, wallets, tradingApi, onCreated }: { templates: Record<string, any>; wallets: any[]; tradingApi: TradingApiFn; onCreated: () => void }) {
  const [description, setDescription] = useState('')
  const [walletId, setWalletId] = useState<string>('')
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<any | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function generate() {
    if (!description.trim()) { setError('Describe the strategy you want'); return }
    setGenerating(true); setError(''); setResult(null)
    try {
      const r = await tradingApi('/strategies/generate', { method: 'POST', body: JSON.stringify({ description }) })
      const d = await r.json()
      if (r.ok) setResult(d)
      else setError(d.detail || 'Generation failed')
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setGenerating(false)
  }

  async function confirmCreate() {
    if (!walletId) { setError('Pick a wallet'); return }
    if (!result) return
    setBusy(true); setError('')
    try {
      const cr = await tradingApi(`/strategies/from-template?wallet_id=${walletId}`, {
        method: 'POST',
        body: JSON.stringify({ template: result.strategy_type, name: result.name }),
      })
      const created = await cr.json()
      if (!cr.ok) { setError(created.detail || 'Create failed'); setBusy(false); return }
      const pr = await tradingApi(`/strategies/${created.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          config: result.config,
          stop_loss_pct: result.stop_loss_pct,
          take_profit_pct: result.take_profit_pct,
          risk_per_trade_pct: result.risk_per_trade_pct,
          max_position_size_usd: result.max_position_size_usd,
          target_tiers: result.target_tiers,
        }),
      })
      if (pr.ok) onCreated()
      else { const d = await pr.json(); setError(d.detail || 'Save failed') }
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setBusy(false)
  }

  return (
    <>
      <Field label="Describe the strategy you want">
        <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3}
          placeholder="e.g. snipe brand new pump.fun tokens right at launch, take a big swing, hold a moonbag if it rips"
          style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }} />
      </Field>
      <button onClick={generate} disabled={generating} style={{ ...saveBtnStyle, marginBottom: 12, background: generating ? 'rgba(0,245,255,0.3)' : '#00f5ff' }}>
        {generating ? 'Thinking…' : 'Generate'}
      </button>

      {result && (
        <div style={{ background: 'rgba(0,245,255,0.05)', border: '1px solid rgba(0,245,255,0.2)', borderRadius: 8, padding: 10, marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#fff', marginBottom: 4 }}>{result.name}</div>
          <div style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(255,255,255,0.06)', color: '#9ca3af', display: 'inline-block', marginBottom: 6 }}>
            {result.strategy_type} → {result.template_label}
          </div>
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,.6)', marginBottom: 8, fontStyle: 'italic' }}>{result.reasoning}</div>
          <div style={{ display: 'flex', gap: 12, fontSize: 11, color: '#9ca3af', flexWrap: 'wrap' }}>
            <span>Max: ${result.max_position_size_usd}</span>
            <span style={{ color: '#ff2d4a' }}>SL {result.stop_loss_pct}%</span>
            <span style={{ color: '#39ff14' }}>TP +{result.take_profit_pct}%</span>
            <span>Risk {result.risk_per_trade_pct}%/trade</span>
          </div>
        </div>
      )}

      {result && (
        <Field label="Wallet">
          <select value={walletId} onChange={e => setWalletId(e.target.value)} style={inputStyle}>
            <option value="">Select wallet…</option>
            {wallets.map(w => <option key={w.id} value={w.id}>{w.label} ({w.address.slice(0, 6)}…)</option>)}
          </select>
        </Field>
      )}
      {error && <div style={{ fontSize: 11, color: '#ff2d4a', marginBottom: 8 }}>{error}</div>}
      {result && (
        <button onClick={confirmCreate} disabled={busy} style={saveBtnStyle}>{busy ? 'Creating…' : 'Create this strategy'}</button>
      )}
    </>
  )
}

// ── Full Custom — pick a base engine (must be one of the 4 executable
// types to actually trade), then hand-edit every parameter including raw
// config JSON. Scaffolds via from-template then immediately PATCHes with
// the custom values, so it reuses the exact same persistence path as
// editing an existing strategy.
function CustomMode({ templates, wallets, tradingApi, onCreated }: { templates: Record<string, any>; wallets: any[]; tradingApi: TradingApiFn; onCreated: () => void }) {
  const templateKeys = Object.keys(templates)
  const [templateKey, setTemplateKey] = useState(templateKeys[0] || '')
  const [name, setName] = useState('')
  const [walletId, setWalletId] = useState<string>('')
  const [maxUsd, setMaxUsd] = useState('20')
  const [riskPct, setRiskPct] = useState('2')
  const [sl, setSl] = useState('')
  const [tp, setTp] = useState('')
  const [targetTiers, setTargetTiers] = useState('just_launch,pumpfun_10k_20k,pre_migration')
  const [configText, setConfigText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const tpl = templates[templateKey]

  useEffect(() => {
    if (tpl?.config) setConfigText(JSON.stringify(tpl.config, null, 2))
  }, [templateKey])

  async function create() {
    if (!walletId) { setError('Pick a wallet'); return }
    let config: any = undefined
    if (configText.trim()) {
      try { config = JSON.parse(configText) } catch { setError('Config is not valid JSON'); return }
    }
    setBusy(true); setError('')
    try {
      const cr = await tradingApi(`/strategies/from-template?wallet_id=${walletId}`, {
        method: 'POST',
        body: JSON.stringify({ template: templateKey, name: name || undefined }),
      })
      const created = await cr.json()
      if (!cr.ok) { setError(created.detail || 'Create failed'); setBusy(false); return }
      const pr = await tradingApi(`/strategies/${created.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          config,
          max_position_size_usd: Number(maxUsd),
          risk_per_trade_pct: Number(riskPct),
          stop_loss_pct: sl === '' ? null : Number(sl),
          take_profit_pct: tp === '' ? null : Number(tp),
          target_tiers: targetTiers,
        }),
      })
      if (pr.ok) onCreated()
      else { const d = await pr.json(); setError(d.detail || 'Save failed') }
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setBusy(false)
  }

  return (
    <>
      <Field label="Base engine (determines execution logic)">
        <select value={templateKey} onChange={e => setTemplateKey(e.target.value)} style={inputStyle}>
          {templateKeys.map(k => <option key={k} value={k}>{templates[k].label}</option>)}
        </select>
      </Field>
      {tpl && !LIVE_TYPES.has(tpl.strategy_type) && (
        <div style={{ fontSize: 10, color: '#ffaa00', marginBottom: 10, padding: '6px 8px', background: 'rgba(255,170,0,0.08)', borderRadius: 6 }}>
          ⚠️ No execution processor yet for this type — editable/creatable, but arming it won't trade until one is built.
        </div>
      )}
      <Field label="Name"><input value={name} onChange={e => setName(e.target.value)} placeholder={tpl?.label} style={inputStyle} /></Field>
      <Field label="Wallet">
        <select value={walletId} onChange={e => setWalletId(e.target.value)} style={inputStyle}>
          <option value="">Select wallet…</option>
          {wallets.map(w => <option key={w.id} value={w.id}>{w.label} ({w.address.slice(0, 6)}…)</option>)}
        </select>
      </Field>
      <div style={{ display: 'flex', gap: 8 }}>
        <Field label="Max position ($)"><input type="number" value={maxUsd} onChange={e => setMaxUsd(e.target.value)} style={inputStyle} /></Field>
        <Field label="Risk % / trade"><input type="number" value={riskPct} onChange={e => setRiskPct(e.target.value)} style={inputStyle} /></Field>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Field label="Stop-loss %"><input type="number" value={sl} onChange={e => setSl(e.target.value)} placeholder="none" style={inputStyle} /></Field>
        <Field label="Take-profit %"><input type="number" value={tp} onChange={e => setTp(e.target.value)} placeholder="none" style={inputStyle} /></Field>
      </div>
      <Field label="Target tiers (comma-separated)"><input value={targetTiers} onChange={e => setTargetTiers(e.target.value)} style={inputStyle} /></Field>
      <Field label="Config (raw JSON — engine-specific)">
        <textarea value={configText} onChange={e => setConfigText(e.target.value)} rows={6}
          style={{ ...inputStyle, resize: 'vertical', fontFamily: 'monospace', fontSize: 11 }} />
      </Field>
      {error && <div style={{ fontSize: 11, color: '#ff2d4a', marginBottom: 8 }}>{error}</div>}
      <button onClick={create} disabled={busy} style={saveBtnStyle}>{busy ? 'Creating…' : 'Create custom strategy'}</button>
    </>
  )
}

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 'min(420px, 92vw)', background: 'rgba(10,10,20,0.98)', border: '1px solid rgba(0,245,255,0.25)', borderRadius: 14, padding: '18px 20px', maxHeight: '85vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: '#fff' }}>{title}</span>
          <button onClick={onClose} className="btn btn-ghost btn-sm"><X size={14} /></button>
        </div>
        {children}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ flex: 1, marginBottom: 10 }}>
      <div style={{ fontSize: 10, color: 'rgba(255,255,255,.5)', marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', background: 'rgba(255,255,255,.05)', border: '1px solid rgba(255,255,255,.1)',
  borderRadius: 6, color: '#fff', fontSize: 12, padding: '7px 8px',
}

const saveBtnStyle: React.CSSProperties = {
  width: '100%', padding: '9px 0', background: '#00f5ff', border: 'none', borderRadius: 6,
  color: '#0a0a14', fontWeight: 700, fontSize: 12, cursor: 'pointer',
}
