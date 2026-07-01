import React from 'react'

export default function NetworkBar({ agentCount, feedCount, online }: { agentCount: number; feedCount: number; online: boolean }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 20,
      padding: '8px 20px', borderBottom: '1px solid rgba(0,255,200,0.08)',
      background: 'rgba(0,0,0,0.3)', fontSize: 11,
      fontFamily: 'monospace', color: '#445566',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: online ? '#00ffcc' : '#ff3333',
          boxShadow: online ? '0 0 6px #00ffcc66' : '0 0 6px #ff333366',
        }} />
        <span style={{ color: online ? '#00ccaa' : '#ff4444' }}>{online ? 'LIVE' : 'OFFLINE'}</span>
      </div>

      <span>▸ AGENTS <span style={{ color: '#8899aa' }}>{agentCount}</span></span>
      <span>▸ FEED <span style={{ color: '#8899aa' }}>{feedCount}</span></span>
      <span>▸ LATENCY <span style={{ color: '#8899aa' }}>~23ms</span></span>

      <div style={{ marginLeft: 'auto', display: 'flex', gap: 10 }}>
        <span style={{ color: '#334455', fontSize: 10 }}>VANTAGE v0.2.1</span>
        <span style={{ color: '#223344' }}>|</span>
        <span style={{ color: '#334455', fontSize: 10 }}>ỌMỌ KỌ́DÀ ECOSYSTEM</span>
      </div>
    </div>
  )
}
