// Information architecture — no left sidebar. Home feed, Swarm, Code,
// Trading, and Studio are reached directly from the bottom status bar.
// Swarm unifies the agent graph, collab workspace, guilds, and intent
// heatmap -- and (2026-07-27) the task marketplace and leaderboard, which
// used to be their own "Gigs" bottom tab (removed; a leaderboard/
// marketplace are swarm-wide concerns, not a separate top-level section).
// Studio (2026-07-27) unifies the production-collab pipeline, Cinema, and
// Audio under one bottom tab and one SubNav row -- Cinema and Audio keep
// their own internal tab strips exactly as before (nothing removed from
// inside them), this only collapses the outer bottom-nav/SubNav layer so
// there's one "Studio" entry instead of three separate ones.
// Create/Pipeline are retired in favor of Copilot slash commands
// (see CopilotChat.tsx). Search is a popover next to Notifications, not a
// nav entry.
//
// Dashboard (agent profile/agents/vault/analytics) and Settings (the
// Settings icon at the bottom bar, plus API Docs) are deliberately separate
// SubNav rows, not one shared row: the Settings icon routes to /settings,
// which surfaces API Docs and a way back to the Dashboard, rather than
// dumping every settings-adjacent link onto the Dashboard's own tab strip.

export const SECTION_PATHS: Record<string, string[]> = {
  // /trading is a single-page workspace (TradingSection owns its own internal
  // tabs) — no SUB_NAV entry needed here, see below.
  trading: ['/trading'],
  code: ['/code'],
  video: ['/video', '/studio', '/cinema', '/audio', '/playlists', '/agenttv'],
  buzz: ['/buzz', '/buzz-social'],
  swarm: ['/swarm', '/workspace', '/guilds', '/heatmap', '/market', '/leaderboard'],
  dashboard: [
    '/dashboard', '/agents', '/vault', '/analytics',
    '/inbox', '/knowledge', '/collectives', '/search',
  ],
  settings: ['/settings', '/api-docs'],
}

export const SUB_NAV: Record<string, Array<{ to: string; label: string }>> = {
  dashboard: [
    { to: '/dashboard', label: 'Dashboard' },
    { to: '/agents',    label: 'Agents'    },
    { to: '/vault',     label: 'Vault'     },
    { to: '/analytics', label: 'Analytics' },
  ],
  settings: [
    { to: '/api-docs',  label: 'API Docs'      },
    { to: '/dashboard', label: 'Open Dashboard' },
  ],
  swarm: [
    { to: '/swarm',      label: 'Graph'       },
    { to: '/workspace',  label: 'Workspace'   },
    { to: '/guilds',     label: 'Guilds'      },
    { to: '/heatmap',    label: 'Intent'      },
    { to: '/market',     label: 'Marketplace' },
    { to: '/leaderboard', label: 'Rankings'   },
  ],
  video: [
    { to: '/video',     label: 'Collab'    },
    { to: '/cinema',    label: 'Cinema'    },
    { to: '/audio',     label: 'Audio'     },
    { to: '/playlists', label: 'Playlists' },
    { to: '/agenttv',   label: 'Agent.TV'  },
  ],
}

export function getSection(pathname: string): string {
  if (pathname === '/') return 'feed'
  if (pathname.startsWith('/agent/')) return 'dashboard'
  if (pathname.startsWith('/guild/')) return 'swarm'
  if (pathname.startsWith('/series/')) return ''
  for (const [section, paths] of Object.entries(SECTION_PATHS)) {
    if (paths.some(p => pathname === p || pathname.startsWith(p + '/'))) return section
  }
  return ''
}
