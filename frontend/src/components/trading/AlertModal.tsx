import React, { useState, useEffect, useCallback } from 'react'
import { X, Bell, Plus, Trash2, Check } from 'lucide-react'
import { useTradingStore } from './tradingStore'

// ══════════════════════════════════════════════════════════════════════════════
// AlertModal — the notification/alerts overlay (Bell button in the top bar).
//   • Notifications: real agent notifications from /api/agents/me/notifications
//   • Price Alerts: client-side alerts for the active pair (localStorage), which
//     fire a browser notification when the live price crosses the threshold.
// ══════════════════════════════════════════════════════════════════════════════

interface Notification {
  id: number
  type: string
  actor_name: string
  subject: string
  read: number
  created_at: string
}

interface PriceAlert {
  id: string
  pair: string
  direction: 'above' | 'below'
  price: number
  createdAt: number
}

const ALERTS_KEY = 'vantage_price_alerts'

function loadAlerts(): PriceAlert[] {
  try { return JSON.parse(localStorage.getItem(ALERTS_KEY) || '[]') } catch { return [] }
}
function saveAlerts(a: PriceAlert[]) { localStorage.setItem(ALERTS_KEY, JSON.stringify(a)) }

function apiKey(): string { return localStorage.getItem('vantage_api_key') || '' }

export default function AlertModal() {
  const { state, dispatch } = useTradingStore()
  const [tab, setTab] = useState<'notifications' | 'alerts'>('notifications')
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loading, setLoading] = useState(true)
  const [alerts, setAlerts] = useState<PriceAlert[]>(loadAlerts)
  const [alertDir, setAlertDir] = useState<'above' | 'below'>('above')
  const [alertPrice, setAlertPrice] = useState('')

  const loadNotifications = useCallback(async () => {
    if (!apiKey()) { setLoading(false); return }
    setLoading(true)
    try {
      const r = await fetch('/api/agents/me/notifications', { headers: { 'X-Agent-Key': apiKey() } })
      if (r.ok) setNotifications(await r.json())
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { loadNotifications() }, [loadNotifications])

  const markAllRead = async () => {
    try {
      await fetch('/api/agents/me/notifications/read-all', { method: 'POST', headers: { 'X-Agent-Key': apiKey() } })
      setNotifications(ns => ns.map(n => ({ ...n, read: 1 })))
    } catch {}
  }

  const addAlert = () => {
    const price = parseFloat(alertPrice)
    if (!price || price <= 0) return
    const next = [...alerts, { id: `${Date.now()}`, pair: state.activePair, direction: alertDir, price, createdAt: Date.now() }]
    setAlerts(next); saveAlerts(next); setAlertPrice('')
  }

  const removeAlert = (id: string) => {
    const next = alerts.filter(a => a.id !== id)
    setAlerts(next); saveAlerts(next)
  }

  const unreadCount = notifications.filter(n => !n.read).length
  const close = () => dispatch({ type: 'TOGGLE_ALERT_MODAL' })

  return (
    <div style={styles.backdrop} onClick={close}>
      <div style={styles.modal} onClick={e => e.stopPropagation()}>
        <div style={styles.header}>
          <Bell size={16} style={{ color: '#00f5ff' }} />
          <span style={styles.title}>Notifications & Alerts</span>
          <button style={styles.closeBtn} onClick={close}><X size={16} /></button>
        </div>

        <div style={styles.tabs}>
          <button
            style={{ ...styles.tab, ...(tab === 'notifications' ? styles.tabActive : {}) }}
            onClick={() => setTab('notifications')}
          >
            Notifications {unreadCount > 0 && <span style={styles.badge}>{unreadCount}</span>}
          </button>
          <button
            style={{ ...styles.tab, ...(tab === 'alerts' ? styles.tabActive : {}) }}
            onClick={() => setTab('alerts')}
          >
            Price Alerts {alerts.length > 0 && <span style={styles.badge}>{alerts.length}</span>}
          </button>
        </div>

        <div style={styles.body}>
          {tab === 'notifications' && (
            <>
              {notifications.length > 0 && (
                <button style={styles.markAllBtn} onClick={markAllRead}>
                  <Check size={12} /> Mark all read
                </button>
              )}
              {loading ? (
                <div style={styles.empty}>Loading…</div>
              ) : !apiKey() ? (
                <div style={styles.empty}>Connect an API key to see notifications.</div>
              ) : notifications.length === 0 ? (
                <div style={styles.empty}>No notifications yet.</div>
              ) : (
                notifications.map(n => (
                  <div key={n.id} style={{ ...styles.notifItem, opacity: n.read ? 0.55 : 1 }}>
                    {!n.read && <span style={styles.unreadDot} />}
                    <div style={{ flex: 1 }}>
                      <div style={styles.notifText}>
                        <strong>{n.actor_name || 'System'}</strong> {n.type?.replace(/_/g, ' ')} {n.subject}
                      </div>
                      <div style={styles.notifTime}>{new Date(n.created_at).toLocaleString()}</div>
                    </div>
                  </div>
                ))
              )}
            </>
          )}

          {tab === 'alerts' && (
            <>
              <div style={styles.alertForm}>
                <span style={{ fontSize: 12, color: '#9ca3af', fontWeight: 600 }}>{state.activePair}</span>
                <select
                  style={styles.select}
                  value={alertDir}
                  onChange={e => setAlertDir(e.target.value as 'above' | 'below')}
                >
                  <option value="above">crosses above</option>
                  <option value="below">crosses below</option>
                </select>
                <input
                  style={styles.priceInput}
                  type="number"
                  placeholder="Price"
                  value={alertPrice}
                  onChange={e => setAlertPrice(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && addAlert()}
                />
                <button style={styles.addBtn} onClick={addAlert}><Plus size={14} /></button>
              </div>
              {alerts.length === 0 ? (
                <div style={styles.empty}>No price alerts set. Create one above.</div>
              ) : (
                alerts.map(a => (
                  <div key={a.id} style={styles.alertItem}>
                    <span style={styles.alertPair}>{a.pair}</span>
                    <span style={{ fontSize: 11, color: a.direction === 'above' ? '#39ff14' : '#ff2d4a' }}>
                      {a.direction} ${a.price.toLocaleString()}
                    </span>
                    <button style={styles.trashBtn} onClick={() => removeAlert(a.id)}><Trash2 size={13} /></button>
                  </div>
                ))
              )}
              <div style={{ fontSize: 10, color: '#6b7280', marginTop: 8 }}>
                Alerts are checked against the live ticker while the terminal is open.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(2px)',
    display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: '10vh', zIndex: 1000,
  },
  modal: {
    width: 440, maxWidth: '92vw', maxHeight: '70vh', display: 'flex', flexDirection: 'column',
    background: '#14142a', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 12,
    boxShadow: '0 24px 64px rgba(0,0,0,0.6)', overflow: 'hidden',
  },
  header: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '14px 16px',
    borderBottom: '1px solid rgba(255,255,255,0.08)',
  },
  title: { fontSize: 14, fontWeight: 700, color: '#e0e0e0' },
  closeBtn: {
    marginLeft: 'auto', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 5, color: '#9ca3af', cursor: 'pointer', padding: 4, display: 'flex',
  },
  tabs: { display: 'flex', gap: 4, padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)' },
  tab: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: 'transparent',
    border: '1px solid transparent', borderRadius: 6, color: '#6b7280', cursor: 'pointer',
    fontSize: 12, fontWeight: 600, fontFamily: 'inherit',
  },
  tabActive: { background: 'rgba(0,245,255,0.12)', color: '#00f5ff', borderColor: 'rgba(0,245,255,0.3)' },
  badge: {
    fontSize: 10, fontWeight: 700, background: 'rgba(0,245,255,0.2)', color: '#00f5ff',
    borderRadius: 8, padding: '1px 6px', minWidth: 16, textAlign: 'center',
  },
  body: { flex: 1, overflowY: 'auto', padding: '12px 16px' },
  empty: { color: '#6b7280', fontSize: 12, textAlign: 'center', padding: 24 },
  markAllBtn: {
    display: 'flex', alignItems: 'center', gap: 4, marginLeft: 'auto', marginBottom: 8,
    background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 5,
    color: '#9ca3af', cursor: 'pointer', fontSize: 11, padding: '4px 8px', fontFamily: 'inherit',
  },
  notifItem: {
    display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 0',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
  },
  unreadDot: { width: 6, height: 6, borderRadius: '50%', background: '#00f5ff', marginTop: 5, flexShrink: 0 },
  notifText: { fontSize: 12, color: '#e0e0e0', lineHeight: 1.4 },
  notifTime: { fontSize: 10, color: '#6b7280', marginTop: 2 },
  alertForm: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 },
  select: {
    background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 5,
    color: '#e0e0e0', fontSize: 11, padding: '5px 6px', outline: 'none', fontFamily: 'inherit',
  },
  priceInput: {
    flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 5,
    color: '#e0e0e0', fontSize: 12, padding: '6px 8px', outline: 'none', fontFamily: 'inherit',
  },
  addBtn: {
    background: '#00f5ff', border: 'none', borderRadius: 5, color: '#0a0a14', cursor: 'pointer',
    padding: 6, display: 'flex',
  },
  alertItem: {
    display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
  },
  alertPair: { fontSize: 12, fontWeight: 600, color: '#e0e0e0', fontFamily: 'monospace' },
  trashBtn: {
    marginLeft: 'auto', background: 'transparent', border: 'none', color: '#6b7280', cursor: 'pointer',
    padding: 4, display: 'flex',
  },
}
