import React from 'react'
import { Play, Loader, Star } from 'lucide-react'

export interface PosterItem {
  title: string
  url: string
  thumbnail?: string | null
  year?: number | null
  rating?: number | null
  media_type?: string
}

export function PosterCard({ item, resolving, onClick }: { item: PosterItem; resolving: boolean; onClick: () => void }) {
  return (
    <div
      className="poster-card"
      onClick={onClick}
      style={{
        position: 'relative',
        flex: '0 0 150px',
        cursor: 'pointer',
        borderRadius: 8,
        overflow: 'hidden',
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid var(--border)',
        transition: 'transform 0.15s ease',
      }}
    >
      <div style={{ position: 'relative', width: '100%', aspectRatio: '2/3' }}>
        {item.thumbnail
          ? <img src={item.thumbnail} alt={item.title} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
          : <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: 'var(--muted)', padding: 8, textAlign: 'center' }}>{item.title}</div>}

        <div className="poster-hover" style={{
          position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(0,0,0,0.92) 0%, rgba(0,0,0,0.3) 55%, transparent 100%)',
          display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', padding: 8,
          opacity: 0, transition: 'opacity 0.15s ease',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 6 }}>
            {resolving
              ? <Loader size={20} className="spin" color="#fff" />
              : <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'var(--purple-bright)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Play size={14} color="#000" fill="#000" />
                </div>}
          </div>
          <div style={{ fontSize: 11, color: '#fff', fontWeight: 500, lineHeight: 1.3 }}>{item.title}</div>
          <div style={{ display: 'flex', gap: 8, fontSize: 10, color: '#ccc', marginTop: 2 }}>
            {item.year && <span>{item.year}</span>}
            {item.rating != null && <span style={{ display: 'flex', alignItems: 'center', gap: 2 }}><Star size={9} fill="#ffd257" color="#ffd257" />{item.rating}</span>}
          </div>
        </div>
      </div>
    </div>
  )
}

export function PosterRow({ title, items, resolvingUrl, onSelect }: {
  title: string
  items: PosterItem[]
  resolvingUrl: string | null
  onSelect: (item: PosterItem) => void
}) {
  if (items.length === 0) return null
  return (
    <div style={{ marginBottom: 28 }}>
      <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 10, color: 'var(--muted-hi)' }}>{title}</h3>
      <div style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 8 }}>
        {items.map((item, i) => (
          <PosterCard key={item.url + i} item={item} resolving={resolvingUrl === item.url} onClick={() => onSelect(item)} />
        ))}
      </div>
    </div>
  )
}

export const POSTER_ROW_CSS = `
.poster-card:hover { transform: translateY(-4px) scale(1.03); }
.poster-card:hover .poster-hover { opacity: 1; }
`
