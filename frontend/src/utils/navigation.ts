export const SECTION_PATHS: Record<string, string[]> = {
  agents:   ['/agents', '/guilds', '/heatmap', '/workspace'],
  settings: ['/settings', '/api-docs'],
}

export const SUB_NAV: Record<string, Array<{ to: string; label: string }>> = {
  // Only Agent Dir gets a top SubNav — all other nav lives in sidebar + status bar
  agents: [
    { to: '/agents',    label: 'Agents'    },
    { to: '/guilds',    label: 'Guilds'    },
    { to: '/heatmap',   label: 'Intent'    },
    { to: '/workspace', label: 'Workspace' },
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
