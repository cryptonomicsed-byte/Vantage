import React from 'react'
import { Link } from 'react-router-dom'
import { List } from 'lucide-react'

interface Series {
  id: number
  title: string
  description: string
  thumbnail_url: string
  episode_count: number
}

export default function SeriesCard({ series: s, agentName }: { series: Series; agentName: string }) {
  return (
    <Link to={`/series/${s.id}`} className="series-card">
      <div className="series-card-thumb">
        {s.thumbnail_url
          ? <img src={s.thumbnail_url} alt={s.title} />
          : <div className="series-no-thumb"><List size={28} /></div>
        }
        <div className="series-ep-badge">{s.episode_count} ep</div>
      </div>
      <div className="series-card-body">
        <div className="series-card-title">{s.title}</div>
        {s.description && <div className="series-card-desc">{s.description}</div>}
      </div>
    </Link>
  )
}
