import React from 'react'

type Tab = 'all' | 'video' | 'text' | 'audio'

interface Props {
  active: Tab
  onChange: (tab: Tab) => void
}

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'all',   label: 'All',   icon: '📡' },
  { id: 'video', label: 'Video', icon: '🎬' },
  { id: 'text',  label: 'Text',  icon: '📝' },
  { id: 'audio', label: 'Audio', icon: '🎵' },
]

export default function FeedTabs({ active, onChange }: Props) {
  return (
    <div className="feed-tabs">
      {TABS.map(t => (
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
