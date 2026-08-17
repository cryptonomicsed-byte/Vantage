import { describe, expect, it, vi } from 'vitest'
import {
  base64ToBytes,
  bytesToBase64,
  decodePcmChunk,
  encodePcmChunk,
  floatTo16BitPCM,
  int16ToFloat32,
  rms,
} from './pcm'
import { VoiceSessionClient, type SocketLike } from './voiceSessionClient'

describe('pcm codec', () => {
  it('round-trips a frame through base64 without drift', () => {
    const original = new Float32Array([0, 0.5, -0.5, 0.25, -0.25])
    const decoded = decodePcmChunk(encodePcmChunk(original))

    expect(decoded.length).toBe(original.length)
    decoded.forEach((v, i) => expect(v).toBeCloseTo(original[i], 4))
  })

  it('clips instead of wrapping when samples exceed [-1, 1]', () => {
    // Wrapping would turn a loud passage into noise; clipping just clips.
    const loud = floatTo16BitPCM(new Float32Array([2, -2]))
    expect(loud[0]).toBe(32767)
    expect(loud[1]).toBe(-32768)
  })

  it('maps the extremes to full scale', () => {
    expect(floatTo16BitPCM(new Float32Array([1]))[0]).toBe(32767)
    expect(floatTo16BitPCM(new Float32Array([-1]))[0]).toBe(-32768)
    expect(int16ToFloat32(new Int16Array([-32768]))[0]).toBe(-1)
  })

  it('survives a truncated frame instead of emitting a garbage sample', () => {
    // An odd byte count cannot form a whole 16-bit sample.
    const odd = bytesToBase64(new Uint8Array([1, 2, 3]))
    expect(decodePcmChunk(odd).length).toBe(1)
  })

  it('handles an empty frame', () => {
    expect(decodePcmChunk('').length).toBe(0)
    expect(rms(new Float32Array(0))).toBe(0)
  })

  it('encodes chunks larger than the fromCharCode argument limit', () => {
    // 64k samples = 128KB, well past the spread-argument limit that a naive
    // String.fromCharCode(...bytes) would hit.
    const big = new Float32Array(65536).fill(0.5)
    const decoded = decodePcmChunk(encodePcmChunk(big))
    expect(decoded.length).toBe(65536)
    expect(decoded[0]).toBeCloseTo(0.5, 3)
  })

  it('round-trips arbitrary bytes through base64', () => {
    const bytes = new Uint8Array([0, 1, 127, 128, 255])
    expect(Array.from(base64ToBytes(bytesToBase64(bytes)))).toEqual([0, 1, 127, 128, 255])
  })

  it('computes rms', () => {
    expect(rms(new Float32Array([1, -1, 1, -1]))).toBeCloseTo(1, 6)
    expect(rms(new Float32Array([0, 0]))).toBe(0)
  })
})

// ── Session client ───────────────────────────────────────────────────────────

function fakeSocket(): SocketLike & { sent: string[]; closed: boolean } {
  return {
    sent: [],
    closed: false,
    send(data: string) {
      this.sent.push(data)
    },
    close() {
      this.closed = true
      this.onclose?.()
    },
    onmessage: null,
    onclose: null,
    onerror: null,
    onopen: null,
  }
}

function okFetch(body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }) as unknown as typeof fetch
}

async function connected(events = {}) {
  const socket = fakeSocket()
  const client = new VoiceSessionClient(events, {
    fetchImpl: okFetch({ session_id: 'vsess_abc', token: 'vvoice_tok' }),
    connect: (url: string) => {
      ;(socket as any).url = url
      queueMicrotask(() => socket.onopen?.())
      return socket
    },
  })
  await client.open()
  socket.onmessage?.({ data: JSON.stringify({ type: 'connected' }) })
  return { client, socket }
}

describe('VoiceSessionClient', () => {
  it('mints a session then connects with the scoped token, not the agent key', async () => {
    const { socket, client } = await connected()
    const url = (socket as any).url as string

    expect(url).toContain('/api/agents/me/voice/sessions/vsess_abc/ws')
    expect(url).toContain('key=vvoice_tok')
    expect(client.getSessionId()).toBe('vsess_abc')
    expect(client.getStatus()).toBe('connected')
  })

  it('omits metadata by default, and sends allow_destructive_tools only when asked', async () => {
    const fetchImpl = okFetch({ session_id: 'vsess_abc', token: 'vvoice_tok' })
    const socket = fakeSocket()
    const client = new VoiceSessionClient(
      {},
      {
        fetchImpl,
        connect: (url: string) => {
          ;(socket as any).url = url
          queueMicrotask(() => socket.onopen?.())
          return socket
        },
      }
    )

    await client.open({ allowDestructive: true })
    const body = JSON.parse((fetchImpl as any).mock.calls[0][1].body)
    expect(body.metadata).toEqual({ allow_destructive_tools: true })

    await client.open()
    const secondBody = JSON.parse((fetchImpl as any).mock.calls[1][1].body)
    // Not merely false/absent-flag -- the key itself must be gone, since the
    // backend's "safe default" is "no metadata", not "metadata saying no".
    expect(secondBody.metadata).toBeUndefined()
  })

  it('fails loudly when the session cannot be created', async () => {
    const client = new VoiceSessionClient(
      {},
      {
        fetchImpl: vi.fn().mockResolvedValue({
          ok: false,
          status: 402,
          text: async () => 'no credit',
        }) as unknown as typeof fetch,
        connect: () => fakeSocket(),
      }
    )

    // Never a silent degrade to a fake session — the caller must see this.
    await expect(client.open()).rejects.toThrow(/HTTP 402/)
    expect(client.getStatus()).toBe('error')
  })

  it('reports transcripts, audio, tool calls and interruptions', async () => {
    const transcripts: any[] = []
    const audio: string[] = []
    const tools: any[] = []
    let interrupted = 0
    const { socket } = await connected({
      onTranscript: (t: any) => transcripts.push(t),
      onAudio: (a: string) => audio.push(a),
      onToolCall: (name: string, args: unknown) => tools.push([name, args]),
      onInterrupted: () => (interrupted += 1),
    })

    socket.onmessage?.({ data: JSON.stringify({ type: 'transcript', sender: 'user', text: 'hi', isFinal: true }) })
    socket.onmessage?.({ data: JSON.stringify({ type: 'audio', audio: 'AAA=' }) })
    socket.onmessage?.({ data: JSON.stringify({ type: 'tool_call', toolName: 'whoami', toolArgs: { a: 1 } }) })
    socket.onmessage?.({ data: JSON.stringify({ type: 'interrupted' }) })

    expect(transcripts).toHaveLength(1)
    expect(transcripts[0]).toMatchObject({ sender: 'user', text: 'hi', isFinal: true })
    expect(audio).toEqual(['AAA='])
    expect(tools).toEqual([['whoami', { a: 1 }]])
    expect(interrupted).toBe(1)
  })

  it('does not emit a blank entry for the turn-complete marker', async () => {
    const transcripts: any[] = []
    const { socket } = await connected({ onTranscript: (t: any) => transcripts.push(t) })

    // The relay signals end-of-turn as an empty final model transcript.
    socket.onmessage?.({ data: JSON.stringify({ type: 'transcript', sender: 'model', text: '', isFinal: true }) })
    expect(transcripts).toHaveLength(0)
  })

  it('ignores malformed frames rather than throwing', async () => {
    const { socket, client } = await connected()
    expect(() => socket.onmessage?.({ data: 'not json' })).not.toThrow()
    expect(client.getStatus()).toBe('connected')
  })

  it('surfaces a server error frame', async () => {
    const statuses: string[] = []
    const { socket } = await connected({ onStatus: (s: string) => statuses.push(s) })
    socket.onmessage?.({ data: JSON.stringify({ type: 'error', message: 'engine down' }) })
    expect(statuses).toContain('error')
  })

  it('sends audio only once connected', async () => {
    const socket = fakeSocket()
    const client = new VoiceSessionClient(
      {},
      {
        fetchImpl: okFetch({ session_id: 'vsess_x', token: 'tok' }),
        connect: () => {
          queueMicrotask(() => socket.onopen?.())
          return socket
        },
      }
    )
    await client.open()

    // Still 'opening' until the server's `connected` frame arrives — audio sent
    // before then would be dropped by the relay anyway.
    client.sendAudio('AAA=')
    expect(socket.sent).toHaveLength(0)

    socket.onmessage?.({ data: JSON.stringify({ type: 'connected' }) })
    client.sendAudio('AAA=')
    expect(JSON.parse(socket.sent[0])).toEqual({ type: 'audio', audio: 'AAA=' })
  })

  it('does not send empty text', async () => {
    const { client, socket } = await connected()
    client.sendText('   ')
    expect(socket.sent).toHaveLength(0)
    client.sendText('hello')
    expect(JSON.parse(socket.sent[0])).toEqual({ type: 'text', text: 'hello' })
  })

  it('closes the socket and reports closed', async () => {
    const { client, socket } = await connected()
    client.close()
    expect(socket.closed).toBe(true)
    expect(client.getStatus()).toBe('closed')
  })
})
