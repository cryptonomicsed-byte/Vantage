import React from 'react'

export type FeedTabId = 'all' | 'video' | 'text' | 'audio' | 'image' | 'graph' | 'debate' | 'following' | 'trending' | 'recommended' | 'federated'

interface Props {
  active: FeedTabId
  onChange: (tab: FeedTabId) => void
  hasApiKey?: boolean
}

const SOURCES: { id: FeedTabId; label: string; requiresKey?: boolean }[] = [
  { id: 'all',         label: 'All'       },
  { id: 'trending',    label: 'Trending'  },
  { id: 'following',   label: 'Following', requiresKey: true },
  { id: 'recommended', label: 'For You',   requiresKey: true },
  { id: 'federated',   label: 'Network'   },
]

const TYPES: { id: FeedTabId; icon: string; label: string }[] = [
  { id: 'video',  icon: '🎬', label: 'Video'   },
  { id: 'text',   icon: '📝', label: 'Text'    },
  { id: 'audio',  icon: '🎵', label: 'Audio'   },
  { id: 'image',  icon: '🖼️', label: 'Gallery' },
  { id: 'graph',  icon: '🕸️', label: 'Graph'   },
  { id: 'debate', icon: '⚔️', label: 'Debates' },
]

const TYPE_IDS = new Set(TYPES.map(t => t.id))

export default function FeedTabs({ active, onChange, hasApiKey = false }: Props) {
  const activeIsType = TYPE_IDS.has(active)

  return (
    <div className="feed-tabs">
      {/* ── Source tabs (left) ── */}
      <div className="ft-sources">
        {SOURCES.filter(s => !s.requiresKey || hasApiKey).map(s => (
          <button
            key={s.id}
            className={`ft-src-btn${active === s.id || (s.id === 'all' && activeIsType) ? ' active' : ''}`}
            onClick={() => onChange(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* ── Type filter icons (right) ── */}
      <div className="ft-types">
        {TYPES.map(t => (
          <button
            key={t.id}
            className={`ft-type-btn${active === t.id ? ' active' : ''}`}
            onClick={() => onChange(t.id)}
            title={t.label}
          >
            {t.icon}
          </button>
        ))}
      </div>
    </div>
  )
}
