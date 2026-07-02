// Information architecture — 5 top-level sections, everything else is a sub-tab.
// The sidebar shows only sections; each section's pages appear as a SubNav row.
// Old routes all remain valid; this file only decides where nav renders.

export const SECTION_PATHS: Record<string, string[]> = {
  agents: [
    '/dashboard', '/agents', '/guilds', '/vault', '/workspace',
    '/heatmap', '/swarm', '/inbox', '/knowledge', '/collectives', '/analytics',
  ],
  trading: ['/trading', '/market'],
  code: ['/code', '/create', '/pipeline'],
  video: ['/video'],
  settings: ['/settings', '/api-docs'],
}

export const SUB_NAV: Record<string, Array<{ to: string; label: string }>> = {
  agents: [
    { to: '/dashboard', label: 'Dashboard' },
    { to: '/agents',    label: 'Agents'    },
    { to: '/guilds',    label: 'Guilds'    },
    { to: '/vault',     label: 'Vault'     },
    { to: '/workspace', label: 'Workspace' },
    { to: '/heatmap',   label: 'Intent'    },
    { to: '/swarm',     label: 'Swarm'     },
  ],
  trading: [
    { to: '/trading', label: 'Trading' },
    { to: '/market',  label: 'Market'  },
  ],
  code: [
    { to: '/code',   label: 'Code'   },
    { to: '/create', label: 'Create' },
  ],
}

export function getSection(pathname: string): string {
  if (pathname === '/') return 'feed'
  if (pathname.startsWith('/agent/')) return 'agents'
  if (pathname.startsWith('/guild/')) return 'agents'
  if (pathname.startsWith('/series/')) return ''
  for (const [section, paths] of Object.entries(SECTION_PATHS)) {
    if (paths.some(p => pathname === p || pathname.startsWith(p + '/'))) return section
  }
  return ''
}
