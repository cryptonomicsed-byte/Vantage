import { useEffect, useState } from 'react'

// The relay this instance actually uses, from its own /api/agents/info.
// Never hardcode a relay in the UI: every operator runs their own.
export function useInstanceRelay(): string {
  const [relay, setRelay] = useState('')
  useEffect(() => {
    const key = localStorage.getItem('vantage_api_key') || ''
    fetch('/api/agents/info', { headers: key ? { 'X-Agent-Key': key } : {} })
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d && typeof d.relay_ws_url === 'string') setRelay(d.relay_ws_url) })
      .catch(() => { /* best-effort */ })
  }, [])
  return relay
}
