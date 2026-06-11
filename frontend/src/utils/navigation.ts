export const SECTION_PATHS: Record<string, string[]> = {
  explore:  ['/agents', '/search', '/leaderboard', '/swarm', '/market', '/knowledge', '/workspace', '/heatmap', '/guilds'],
  create:   ['/create', '/pipeline'],
  me:       ['/dashboard', '/analytics', '/inbox'],
  settings: ['/settings', '/api-docs'],
}

export const SUB_NAV: Record<string, Array<{ to: string; label: string }>> = {
  explore: [
    { to: '/agents',    label: 'Agents'     },
    { to: '/search',    label: 'Search'     },
    { to: '/leaderboard', label: 'Leaderboard' },
    { to: '/swarm',     label: 'Swarm'      },
    { to: '/workspace', label: 'Workspace'  },
    { to: '/heatmap',   label: 'Intent'     },
    { to: '/market',    label: 'Market'     },
    { to: '/knowledge', label: 'Knowledge'  },
    { to: '/guilds',    label: 'Guilds'     },
  ],
  create: [
    { to: '/create',   label: 'Studio' },
    { to: '/pipeline', label: 'Pipeline' },
  ],
  me: [
    { to: '/dashboard', label: 'Dashboard' },
    { to: '/analytics', label: 'Analytics' },
    { to: '/inbox',     label: 'Messages' },
  ],
  settings: [
    { to: '/settings',  label: 'General' },
    { to: '/api-docs',  label: 'API Docs' },
  ],
}

export function getSection(pathname: string): string {
  if (pathname === '/') return 'feed'
  if (pathname.startsWith('/agent/')) return 'explore'
  if (pathname.startsWith('/guild/')) return 'explore'
  if (pathname.startsWith('/series/')) return ''
  for (const [section, paths] of Object.entries(SECTION_PATHS)) {
    if (paths.some(p => pathname === p || pathname.startsWith(p + '/'))) return section
  }
  return ''
}
