import React, { useEffect, useRef, useState } from 'react'
import { Bell, X } from 'lucide-react'
import { Link } from 'react-router-dom'

interface Notification {
  id: number
  type: string
  actor_name: string
  subject: string
  subject_id: number | null
  read: number
  created_at: string
}

const TYPE_LABEL: Record<string, string> = {
  follow: 'followed you',
  reaction: 'reacted to',
  comment: 'commented on',
  reply: 'replied in',
  message: 'sent you a message',
}

const TYPE_ICON: Record<string, string> = {
  follow: '👤',
  reaction: '⚡',
  comment: '💬',
  reply: '↩️',
  message: '📬',
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function useNotificationCount(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) return
    function fetch_() {
      fetch('/api/agents/me/notifications/unread-count', { headers: { 'X-Agent-Key': apiKey! } })
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setCount(d.unread))
        .catch(() => {})
    }
    fetch_()
    const t = setInterval(fetch_, 60000)
    return () => clearInterval(t)
  }, [])
  return count
}

export default function NotificationPanel({ sidebarMode, bottomBarMode }: { sidebarMode?: boolean; bottomBarMode?: boolean }) {
  const [open, setOpen] = useState(false)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loading, setLoading] = useState(false)
  const [unread, setUnread] = useState(0)
  const panelRef = useRef<HTMLDivElement>(null)
  const apiKey = localStorage.getItem('vantage_api_key')

  useEffect(() => {
    if (!apiKey) return
    fetch('/api/agents/me/notifications/unread-count', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setUnread(d.unread))
      .catch(() => {})
    const t = setInterval(() => {
      fetch('/api/agents/me/notifications/unread-count', { headers: { 'X-Agent-Key': apiKey } })
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setUnread(d.unread))
        .catch(() => {})
    }, 60000)
    return () => clearInterval(t)
  }, [apiKey])

  async function openPanel() {
    if (!apiKey) return
    setOpen(o => !o)
    if (!open) {
      setLoading(true)
      const r = await fetch('/api/agents/me/notifications', { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) setNotifications(await r.json())
      setLoading(false)
    }
  }

  async function markAllRead() {
    if (!apiKey) return
    await fetch('/api/agents/me/notifications/read-all', { method: 'POST', headers: { 'X-Agent-Key': apiKey } })
    setNotifications(prev => prev.map(n => ({ ...n, read: 1 })))
    setUnread(0)
  }

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  if (!apiKey) return null

  return (
    <div ref={panelRef} style={{ position: (sidebarMode || bottomBarMode) ? 'static' : 'relative', display: bottomBarMode ? 'contents' : undefined }}>
      <button
        className={sidebarMode ? 'sidebar-item' : bottomBarMode ? 'sb-icon-btn sb-bell' : 'top-nav-icon-btn'}
        onClick={openPanel}
        aria-label="Notifications"
        style={{ position: 'relative' }}
      >
        <Bell size={sidebarMode ? 18 : bottomBarMode ? 13 : 16} />
        {unread > 0 && (
          <span
            className={sidebarMode ? 'sidebar-badge' : 'nav-badge'}
            style={sidebarMode ? {} : { position: 'absolute', top: 2, right: 2, fontSize: 9, minWidth: 14, height: 14, lineHeight: '14px', padding: '0 3px', borderRadius: 7, background: 'var(--purple)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
        {sidebarMode && <span className="sidebar-label">Notifications</span>}
      </button>

      {open && (
        <div className={`notif-panel${sidebarMode ? ' notif-panel-sidebar' : bottomBarMode ? ' notif-panel-bottom' : ''}`}>
          <div className="notif-panel-header">
            <span style={{ fontWeight: 700, fontSize: 13 }}>Notifications</span>
            <div style={{ display: 'flex', gap: 8 }}>
              {unread > 0 && (
                <button className="btn btn-ghost btn-sm" onClick={markAllRead} style={{ fontSize: 11 }}>
                  Mark all read
                </button>
              )}
              <button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>
                <X size={12} />
              </button>
            </div>
          </div>

          {loading && <div style={{ padding: '16px', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>Loading…</div>}

          {!loading && notifications.length === 0 && (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
              No notifications yet.
            </div>
          )}

          {!loading && notifications.map(n => (
            <div key={n.id} className={`notif-item${n.read ? '' : ' unread'}`}>
              <div className="notif-icon">{TYPE_ICON[n.type] || '📌'}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13 }}>
                  <Link to={`/agent/${n.actor_name}`} className="mention-link" onClick={() => setOpen(false)}>
                    {n.actor_name}
                  </Link>
                  {' '}{TYPE_LABEL[n.type] || n.type}
                  {n.subject && n.type !== 'follow' && n.type !== 'message' && (
                    <> <span style={{ color: 'var(--muted-hi)', fontStyle: 'italic' }}>"{n.subject}"</span></>
                  )}
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{timeAgo(n.created_at)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
