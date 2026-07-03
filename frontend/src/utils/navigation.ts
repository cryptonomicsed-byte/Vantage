// Information architecture — no left sidebar. Home feed, Trading, Code, and
// Video are reached directly from the bottom status bar; everything else
// (agent dashboard, directory, guilds, vault, workspace, intent, swarm,
// gigs, analytics, search, settings, docs) lives together under Settings,
// surfaced as one shared SubNav row.

export const SECTION_PATHS: Record<string, string[]> = {
  // /trading is a single-page workspace (TradingSection owns its own internal
  // tabs) — no SUB_NAV entry needed here, see below.
  trading: ['/trading'],
  code: ['/code', '/create', '/pipeline'],
  video: ['/video'],
  settings: [
    '/settings', '/api-docs', '/dashboard', '/agents', '/guilds', '/vault',
    '/workspace', '/heatmap', '/swarm', '/market', '/inbox', '/knowledge',
    '/collectives', '/analytics', '/search',
  ],
}

export const SUB_NAV: Record<string, Array<{ to: string; label: string }>> = {
  settings: [
    { to: '/dashboard', label: 'Dashboard' },
    { to: '/agents',    label: 'Agents'    },
    { to: '/guilds',    label: 'Guilds'    },
    { to: '/vault',     label: 'Vault'     },
    { to: '/workspace', label: 'Workspace' },
    { to: '/heatmap',   label: 'Intent'    },
    { to: '/swarm',     label: 'Swarm'     },
    // /market is the agent task/bidding marketplace (gig economy), not crypto
    // market data — it belongs with the other agent-economy pages, not Trading.
    { to: '/market',    label: 'Gigs'      },
    { to: '/analytics', label: 'Analytics' },
    { to: '/search',    label: 'Search'    },
    { to: '/settings',  label: 'Settings'  },
    { to: '/api-docs',  label: 'API Docs'  },
  ],
  code: [
    { to: '/pipeline', label: 'Pipeline' },
    { to: '/code',     label: 'Code'     },
    { to: '/create',   label: 'Create'   },
  ],
}

export function getSection(pathname: string): string {
  if (pathname === '/') return 'feed'
  if (pathname.startsWith('/agent/')) return 'settings'
  if (pathname.startsWith('/guild/')) return 'settings'
  if (pathname.startsWith('/series/')) return ''
  for (const [section, paths] of Object.entries(SECTION_PATHS)) {
    if (paths.some(p => pathname === p || pathname.startsWith(p + '/'))) return section
  }
  return ''
}
