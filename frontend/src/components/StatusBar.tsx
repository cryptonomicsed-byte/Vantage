import React, { useState, useEffect } from 'react'

export default function StatusBar() {
  const [agentName, setAgentName] = useState(() => localStorage.getItem('vantage_agent_name') || '')
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    function sync() {
      const key = localStorage.getItem('vantage_api_key')
      const name = localStorage.getItem('vantage_agent_name') || ''
      setConnected(!!key)
      setAgentName(name)
    }
    sync()
    window.addEventListener('storage', sync)
    const t = setInterval(sync, 5000)
    return () => { window.removeEventListener('storage', sync); clearInterval(t) }
  }, [])

  return (
    <div className="status-bar">
      <div className="status-bar-left">
        <span className={`status-bar-dot${connected ? ' connected' : ''}`} />
        <span className="status-agent">
          {connected ? (agentName || 'Connected') : 'Not connected — open Dashboard'}
        </span>
      </div>
      <div className="status-bar-right">
        <span className="status-version">⚡ v0.2.0</span>
      </div>
    </div>
  )
}
