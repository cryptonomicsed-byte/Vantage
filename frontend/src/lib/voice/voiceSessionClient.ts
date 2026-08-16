// Client for Vantage's own voice relay.
//
// Two steps, deliberately: open a session over REST (which mints a scoped
// vvoice_ token), then connect the WebSocket with that token. The agent key
// never travels on the socket — only the session token, which can do nothing
// but append to the one session it belongs to.
//
// The transport is injected so the whole flow can be exercised in tests
// without a browser WebSocket or a server.

export type TranscriptEntry = {
  id: string
  sender: 'user' | 'model'
  text: string
  isFinal: boolean
  at: number
}

export type VoiceStatus = 'idle' | 'opening' | 'connected' | 'closed' | 'error'

export type VoiceClientEvents = {
  onStatus?: (status: VoiceStatus, detail?: string) => void
  onTranscript?: (entry: TranscriptEntry) => void
  onAudio?: (base64Pcm: string) => void
  onToolCall?: (toolName: string, args: unknown) => void
  onInterrupted?: () => void
}

export type SocketLike = {
  send(data: string): void
  close(): void
  onmessage: ((ev: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((ev?: unknown) => void) | null
  onopen: (() => void) | null
}

export type VoiceClientOptions = {
  /** Injected for tests; defaults to a real WebSocket. */
  connect?: (url: string) => SocketLike
  fetchImpl?: typeof fetch
}

export type OpenOptions = {
  voice?: string
  persona?: string
  ttlSeconds?: number
}

export class VoiceSessionClient {
  private socket: SocketLike | null = null
  private sessionId: string | null = null
  private status: VoiceStatus = 'idle'
  private readonly connect: (url: string) => SocketLike
  private readonly fetchImpl: typeof fetch
  private seq = 0

  constructor(private events: VoiceClientEvents = {}, options: VoiceClientOptions = {}) {
    this.connect =
      options.connect ??
      ((url: string) => new WebSocket(url) as unknown as SocketLike)
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args))
  }

  getSessionId(): string | null {
    return this.sessionId
  }

  getStatus(): VoiceStatus {
    return this.status
  }

  private setStatus(status: VoiceStatus, detail?: string) {
    this.status = status
    this.events.onStatus?.(status, detail)
  }

  /** Create the session, then open the socket. Resolves once connected. */
  async open(options: OpenOptions = {}): Promise<void> {
    this.setStatus('opening')

    let body: { session_id: string; token: string }
    try {
      // X-Agent-Key is attached by the app's fetch interceptor.
      const res = await this.fetchImpl('/api/agents/me/voice/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          engine: 'gemini_live',
          voice: options.voice ?? '',
          persona: options.persona ?? '',
          ttl_seconds: options.ttlSeconds ?? 1800,
        }),
      })
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        throw new Error(`could not start a voice session (HTTP ${res.status}) ${detail}`.trim())
      }
      body = await res.json()
    } catch (err) {
      // Explicit failure, never a silent degrade to a fake session.
      this.setStatus('error', err instanceof Error ? err.message : String(err))
      throw err
    }

    this.sessionId = body.session_id
    const scheme = typeof location !== 'undefined' && location.protocol === 'https:' ? 'wss' : 'ws'
    const host = typeof location !== 'undefined' ? location.host : ''
    const url = `${scheme}://${host}/api/agents/me/voice/sessions/${body.session_id}/ws?key=${encodeURIComponent(body.token)}`

    await new Promise<void>((resolve, reject) => {
      const socket = this.connect(url)
      this.socket = socket

      socket.onopen = () => resolve()
      socket.onerror = () => {
        this.setStatus('error', 'voice socket failed')
        reject(new Error('voice socket failed'))
      }
      socket.onclose = () => {
        this.socket = null
        if (this.status !== 'error') this.setStatus('closed')
      }
      socket.onmessage = (ev) => this.handleMessage(ev.data)
    })
  }

  private handleMessage(raw: string) {
    let msg: any
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }

    switch (msg.type) {
      case 'connected':
        this.setStatus('connected')
        break
      case 'audio':
        if (msg.audio) this.events.onAudio?.(msg.audio)
        break
      case 'transcript':
        // A turn-complete marker arrives as an empty final model transcript;
        // forwarding it as an entry would put a blank line in the UI.
        if (msg.text) {
          this.events.onTranscript?.({
            id: `t${this.seq++}`,
            sender: msg.sender === 'user' ? 'user' : 'model',
            text: msg.text,
            isFinal: Boolean(msg.isFinal),
            at: Date.now(),
          })
        }
        break
      case 'tool_call':
        this.events.onToolCall?.(msg.toolName, msg.toolArgs)
        break
      case 'interrupted':
        this.events.onInterrupted?.()
        break
      case 'error':
        this.setStatus('error', msg.message)
        break
      default:
        break
    }
  }

  sendAudio(base64Pcm: string): void {
    if (!this.socket || this.status !== 'connected') return
    this.socket.send(JSON.stringify({ type: 'audio', audio: base64Pcm }))
  }

  sendText(text: string): void {
    if (!this.socket || !text.trim()) return
    this.socket.send(JSON.stringify({ type: 'text', text }))
  }

  close(): void {
    this.socket?.close()
    this.socket = null
    if (this.status !== 'error') this.setStatus('closed')
  }
}
