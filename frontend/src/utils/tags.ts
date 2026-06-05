export function parseTags(bio: string): string[] {
  return (bio.match(/#\w+/g) || []).map(t => t.slice(1))
}
