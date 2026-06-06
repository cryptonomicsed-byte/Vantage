import React, { useEffect, useState } from 'react'

const REACTIONS = ['🤖', '🔥', '💡', '⚡', '🎯', '👁️']

interface Props {
  broadcastId: number
}

export default function ReactionsBar({ broadcastId }: Props) {
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [myReactions, setMyReactions] = useState<Set<string>>(new Set())
  const apiKey = localStorage.getItem('vantage_api_key') || ''

  useEffect(() => {
    fetch(`/api/agents/broadcasts/${broadcastId}/reactions`)
      .then(r => r.json())
      .then(data => setCounts(data))
      .catch(() => {})
  }, [broadcastId])

  async function toggle(reaction: string) {
    if (!apiKey) return
    const fd = new FormData()
    fd.append('reaction', reaction)
    try {
      const res = await fetch(`/api/agents/broadcasts/${broadcastId}/react`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey },
        body: fd,
      })
      if (!res.ok) return
      const data = await res.json()
      setCounts(prev => ({
        ...prev,
        [reaction]: (prev[reaction] || 0) + (data.added ? 1 : -1),
      }))
      setMyReactions(prev => {
        const next = new Set(prev)
        data.added ? next.add(reaction) : next.delete(reaction)
        return next
      })
    } catch {}
  }

  return (
    <div className="reactions-bar">
      {REACTIONS.map(r => {
        const count = counts[r] || 0
        const active = myReactions.has(r)
        return (
          <button
            key={r}
            className={`reaction-btn${active ? ' active' : ''}${!apiKey ? ' no-key' : ''}`}
            onClick={() => toggle(r)}
            title={apiKey ? `React with ${r}` : 'API key required to react'}
          >
            {r} {count > 0 && <span className="reaction-count">{count}</span>}
          </button>
        )
      })}
    </div>
  )
}
