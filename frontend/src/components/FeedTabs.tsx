import React from 'react'

export type FeedTabId = 'all' | 'video' | 'text' | 'audio' | 'image' | 'graph' | 'following' | 'trending'

interface Props {
  active: FeedTabId
  onChange: (tab: FeedTabId) => void
  hasApiKey?: boolean
}

const TABS: { id: FeedTabId; label: string; icon: string; requiresKey?: boolean }[] = [
  { id: 'all',       label: 'All',       icon: '📡' },
  { id: 'video',     label: 'Video',     icon: '🎬' },
  { id: 'text',      label: 'Text',      icon: '📝' },
  { id: 'audio',     label: 'Audio',     icon: '🎵' },
  { id: 'image',     label: 'Gallery',   icon: '🖼️' },
  { id: 'graph',     label: 'Graph',     icon: '🕸️' },
  { id: 'following', label: 'Following', icon: '⭐', requiresKey: true },
  { id: 'trending',  label: 'Trending',  icon: '🔥', requiresKey: false },
]

export default function FeedTabs({ active, onChange, hasApiKey = false }: Props) {
  return (
    <div className="feed-tabs">
      {TABS.filter(t => !t.requiresKey || hasApiKey).map(t => (
        <button
          key={t.id}
          className={`feed-tab${active === t.id ? ' active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.icon} {t.label}
        </button>
      ))}
    </div>
  )
}
