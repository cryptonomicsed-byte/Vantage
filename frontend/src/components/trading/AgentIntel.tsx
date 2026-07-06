import React, { useEffect, useState } from 'react'
import { Zap, Shield, TrendingUp, AlertTriangle, Clock } from 'lucide-react'

interface IntelReport {
  title: string
  post_content: string
  created_at: string
  agent_name?: string
  id?: number
}

export default function AgentIntel() {
  const [reports, setReports] = useState<IntelReport[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    const key = localStorage.getItem('vantage_api_key') || ''
    fetch('/api/intel/daily?limit=50', { headers: { 'X-Agent-Key': key } })
      .then(r => r.json())
      .then((d: any) => {
        setReports(d.reports || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  const filtered = filter === 'all' ? reports : filter === 'intel' 
    ? reports.filter(r => r.title?.includes('Intel'))
    : reports.filter(r => r.title?.includes('Reflect'))

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading agent intel…</div>

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      {/* Main feed */}
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <Zap size={20} color="#22c55e" />
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Agent Intel</h2>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>{reports.length} reports</span>
        </div>

        {/* Filter tabs */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          {[
            { id: 'all', label: 'All', icon: Zap },
            { id: 'intel', label: 'Intel Scans', icon: Shield },
            { id: 'reflect', label: 'Daily Reflects', icon: TrendingUp },
          ].map(f => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '6px 14px',
                background: filter === f.id ? 'rgba(34,197,94,0.12)' : 'rgba(255,255,255,0.03)',
                border: `1px solid ${filter === f.id ? 'rgba(34,197,94,0.25)' : 'rgba(255,255,255,0.06)'}`,
                borderRadius: 8,
                color: filter === f.id ? '#22c55e' : 'var(--muted)',
                cursor: 'pointer',
                fontSize: 12,
              }}
            >
              <f.icon size={12} />
              {f.label}
            </button>
          ))}
        </div>

        {filtered.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
            <Shield size={32} style={{ opacity: 0.2 }} />
            <p style={{ marginTop: 12, fontSize: 13 }}>No intel reports yet</p>
            <p style={{ fontSize: 11 }}>Agent intel scans chains every 4h, daily reflects at midnight.</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {filtered.slice(0, 30).map((r, i) => {
              const isIntel = (r.title || '').includes('Intel')
              return (
                <div
                  key={i}
                  style={{
                    background: 'rgba(255,255,255,0.02)',
                    border: '1px solid rgba(255,255,255,0.05)',
                    borderLeft: `3px solid ${isIntel ? '#22c55e' : '#f59e0b'}`,
                    borderRadius: '0 8px 8px 0',
                    padding: '12px 14px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    {isIntel ? <Shield size={13} color="#22c55e" /> : <TrendingUp size={13} color="#f59e0b" />}
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{r.title}</span>
                    {r.created_at && (
                      <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Clock size={10} />
                        {new Date(r.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: 'rgba(255,255,255,0.42)',
                      lineHeight: 1.6,
                      maxHeight: 120,
                      overflow: 'hidden',
                    }}
                  >
                    {(r.post_content || '').slice(0, 400)}
                  </div>
                </div>
              )
            })}
            {filtered.length > 30 && (
              <div style={{ textAlign: 'center', padding: 12, fontSize: 11, color: 'var(--muted)' }}>
                +{filtered.length - 30} more reports
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sidebar stats */}
      <div style={{ width: 220, flexShrink: 0 }}>
        <div style={{
          background: 'rgba(255,255,255,0.02)',
          border: '1px solid rgba(255,255,255,0.05)',
          borderRadius: 10,
          padding: 14,
          marginBottom: 12,
        }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>Intel Sources</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
              <span style={{ color: '#22c55e' }}>🧠 Intel Scans</span>
              <span>{reports.filter(r => r.title?.includes('Intel')).length}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
              <span style={{ color: '#f59e0b' }}>📊 Reflects</span>
              <span>{reports.filter(r => r.title?.includes('Reflect')).length}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
              <span style={{ color: 'var(--muted)' }}>📋 Total</span>
              <span style={{ fontWeight: 600 }}>{reports.length}</span>
            </div>
          </div>
        </div>

        <div style={{
          background: 'rgba(239,68,68,0.05)',
          border: '1px solid rgba(239,68,68,0.1)',
          borderRadius: 10,
          padding: 14,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <AlertTriangle size={12} color="#ef4444" />
            <span style={{ fontSize: 11, fontWeight: 600, color: '#ef4444' }}>Active Agents</span>
          </div>
          <div style={{ fontSize: 11, color: 'rgba(239,68,68,0.6)' }}>
            Hermes-Ares posts 200/day. All intel now routes to this section — never the main feed.
          </div>
        </div>
      </div>
    </div>
  )
}
