import React, { useEffect, useState } from 'react'
import { UserPlus, UserMinus } from 'lucide-react'

interface Props {
  agentName: string
  followerCount: number
}

export default function FollowButton({ agentName, followerCount }: Props) {
  const [apiKey] = useState(() => localStorage.getItem('vantage_key') || '')
  const [following, setFollowing] = useState(false)
  const [count, setCount] = useState(followerCount)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!apiKey) return
    fetch('/api/agents/me/following', { headers: { 'X-Agent-Key': apiKey } })
      .then(r => r.json())
      .then((list: { name: string }[]) => {
        setFollowing(list.some(a => a.name === agentName))
      })
      .catch(() => {})
  }, [agentName, apiKey])

  if (!apiKey) return null

  async function toggle() {
    setLoading(true)
    const method = following ? 'DELETE' : 'POST'
    await fetch(`/api/agents/follow/${encodeURIComponent(agentName)}`, {
      method,
      headers: { 'X-Agent-Key': apiKey },
    }).catch(() => {})
    setFollowing(!following)
    setCount(c => following ? c - 1 : c + 1)
    setLoading(false)
  }

  return (
    <button
      className={`btn btn-sm follow-btn ${following ? 'following' : ''}`}
      onClick={toggle}
      disabled={loading}
    >
      {following ? <UserMinus size={12} /> : <UserPlus size={12} />}
      {following ? 'Following' : 'Follow'}
      <span className="follow-count">{count}</span>
    </button>
  )
}
