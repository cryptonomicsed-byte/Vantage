import { useEffect, useState } from 'react'

export interface TraceEntry {
  id: number
  event_type: string
  payload: string
  created_at: string
}

export function useAgentTrace(agentName: string | undefined) {
  const [entries, setEntries] = useState<TraceEntry[]>([])

  useEffect(() => {
    if (!agentName) return
    function load() {
      fetch(`/api/agents/${encodeURIComponent(agentName!)}/observer-trace`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setEntries(d.entries || []))
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [agentName])

  return entries
}
