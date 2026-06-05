import { useEffect } from 'react'

export function useFeedSocket(onNew: (b: any) => void) {
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/feed`)
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'new_broadcast') onNew(msg)
      } catch {}
    }
    return () => ws.close()
  }, [])
}
