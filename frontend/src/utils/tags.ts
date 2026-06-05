export function parseTags(bio: string): string[] {
  return (bio.match(/#\w+/g) || []).map(t => t.slice(1))
}

export function parseMentions(text: string): string[] {
  return (text.match(/@\w+/g) || []).map(t => t.slice(1))
}

export function renderWithMentions(text: string): { type: 'text' | 'mention'; value: string }[] {
  const parts = text.split(/(@\w+)/g)
  return parts.map(p => p.startsWith('@') ? { type: 'mention', value: p.slice(1) } : { type: 'text', value: p })
}
