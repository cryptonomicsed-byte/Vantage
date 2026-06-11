export type PresenceStatus = 'online' | 'recent' | 'offline'

export function getPresenceStatus(lastSeenAt: string | null | undefined): PresenceStatus {
  if (!lastSeenAt) return 'offline'
  const diff = Date.now() - new Date(lastSeenAt).getTime()
  if (diff < 5 * 60 * 1000) return 'online'
  if (diff < 60 * 60 * 1000) return 'recent'
  return 'offline'
}
