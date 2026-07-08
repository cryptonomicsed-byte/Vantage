import React, { useEffect, useState } from 'react'
import { Zap, Clock, Shield, TrendingUp } from 'lucide-react'

interface IntelReport {
  title: string
  post_content: string
  created_at: string
}

export default function DailyIntel() {
  const [reports, setReports] = useState<IntelReport[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const key = localStorage.getItem('vantage_api_key') || ''
    fetch('/api/intel/daily?limit=10', { headers: { 'X-Agent-Key': key } })
      .then(r => r.json())
      .then((d: any) => {
        setReports(d.reports || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>Loading daily intel…</div>

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <Zap size={20} color="#f59e0b" />
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Daily Intel</h2>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{reports.length} reports</span>
      </div>

      {reports.length === 0 && (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
          <Shield size={32} style={{ opacity: 0.3 }} />
          <p style={{ marginTop: 12 }}>No daily intel reports yet.</p>
          <p style={{ fontSize: 12 }}>Ares Intelligence scans 6 chains every 4 hours. Daily reflections summarize alpha at midnight.</p>
        </div>
      )}

      {reports.map((r, i) => (
        <div
          key={i}
          style={{
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
            borderRadius: 10,
            padding: 16,
            marginBottom: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            {r.title.includes('Intel') ? <Shield size={14} color="#22c55e" /> : <TrendingUp size={14} color="#f59e0b" />}
            <span style={{ fontWeight: 600, fontSize: 14 }}>{r.title}</span>
            <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
              <Clock size={10} style={{ marginRight: 4 }} />
              {new Date(r.created_at).toLocaleString()}
            </span>
          </div>
          <div
            style={{
              fontSize: 12,
              color: 'rgba(255,255,255,0.5)',
              lineHeight: 1.6,
              maxHeight: 200,
              overflow: 'hidden',
            }}
            dangerouslySetInnerHTML={{ __html: (r.post_content || '').replace(/\*\*/g, '').replace(/\*/g, '').slice(0, 500) }}
          />
        </div>
      ))}
    </div>
  )
}
