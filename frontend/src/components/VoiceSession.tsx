import { useCallback, useEffect, useRef, useState } from 'react'
import { MicRecorder, PcmPlayer } from '../lib/voice/audio'
import {
  VoiceSessionClient,
  type TranscriptEntry,
  type VoiceStatus,
} from '../lib/voice/voiceSessionClient'

// Voice, served by Vantage itself: the session, the transcript and the model
// connection all live on this origin, so there is no second deployment to keep
// in sync and no cross-origin hop for the audio.

// Presets rather than a free-text pattern box: the allowlist decides how much
// of the agent's authority a spoken sentence can reach, so it should be a
// deliberate choice from a short list, not something typed in a hurry.
const TOOL_PRESETS: { id: string; label: string; patterns: string[] | undefined; note: string }[] = [
  { id: 'none', label: 'No tools', patterns: undefined, note: 'Conversation only.' },
  {
    id: 'memory',
    label: 'Memory & copilot',
    patterns: ['tag:memory_vault', 'tag:copilot'],
    note: 'Read and write the vault, ask the copilot.',
  },
  {
    id: 'readonly',
    label: 'Read-only platform',
    patterns: ['tag:agents', 'tag:analytics', 'tag:memory_vault', 'tag:copilot'],
    note: 'Look things up across the platform.',
  },
  {
    id: 'composio',
    label: 'Memory + Composio',
    patterns: ['tag:memory_vault', 'tag:copilot', 'composio_execute', 'tag:composio'],
    note: 'Adds connected third-party apps (Gmail, GitHub, Slack…).',
  },
  {
    id: 'engineering',
    label: 'Engineering (runs code)',
    patterns: ['tag:workspace', 'tag:code', 'tag:memory_vault', 'tag:copilot'],
    note: 'Clone repos, run commands and edit files in a sandbox container.',
  },
  { id: 'all', label: 'Everything', patterns: ['*'], note: 'All non-admin Vantage tools.' },
]

const STATUS_LABEL: Record<VoiceStatus, string> = {
  idle: 'Not connected',
  opening: 'Connecting…',
  connected: 'Live',
  closed: 'Ended',
  error: 'Error',
}

const STATUS_COLOR: Record<VoiceStatus, string> = {
  idle: '#6b7280',
  opening: '#d97706',
  connected: '#059669',
  closed: '#6b7280',
  error: '#dc2626',
}

export default function VoiceSession() {
  const [status, setStatus] = useState<VoiceStatus>('idle')
  const [detail, setDetail] = useState('')
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([])
  const [toolCalls, setToolCalls] = useState<
    { name: string; at: number; status?: string; durationMs?: number }[]
  >([])
  const [level, setLevel] = useState(0)
  const [typed, setTyped] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  // 'all': now that a large scope is reached via vantage_find_tools /
  // vantage_call_tool instead of being silently capped, defaulting to the
  // narrowest preset would just recreate the old under-delivery by a
  // different route. Destructive actions stay opt-in regardless — see
  // allowDestructive below.
  const [presetId, setPresetId] = useState('all')
  const [allowDestructive, setAllowDestructive] = useState(false)

  const clientRef = useRef<VoiceSessionClient | null>(null)
  const recorderRef = useRef<MicRecorder | null>(null)
  const playerRef = useRef<PcmPlayer | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript])

  const teardown = useCallback(() => {
    recorderRef.current?.stop()
    playerRef.current?.stop()
    clientRef.current?.close()
    recorderRef.current = null
    playerRef.current = null
    clientRef.current = null
  }, [])

  // Always release the microphone when this page goes away, including on a
  // route change — a mic left open is both a privacy problem and a visible
  // browser indicator the user cannot explain.
  useEffect(() => teardown, [teardown])

  const start = useCallback(async () => {
    setTranscript([])
    setToolCalls([])
    setDetail('')

    const player = new PcmPlayer()
    playerRef.current = player

    const client = new VoiceSessionClient({
      onStatus: (s, d) => {
        setStatus(s)
        if (d) setDetail(d)
      },
      onTranscript: (entry) => setTranscript((prev) => [...prev, entry]),
      onAudio: (chunk) => player.play(chunk),
      onInterrupted: () => player.interrupt(),
      onToolCall: (name) => setToolCalls((prev) => [...prev, { name, at: Date.now() }]),
      onToolResult: (name, toolStatus, durationMs) =>
        setToolCalls((prev) => {
          // Attach the outcome to the most recent unresolved call of that name.
          const next = [...prev]
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].name === name && !next[i].status) {
              next[i] = { ...next[i], status: toolStatus, durationMs }
              break
            }
          }
          return next
        }),
    })
    clientRef.current = client

    try {
      await client.open({
        tools: TOOL_PRESETS.find((p) => p.id === presetId)?.patterns,
        allowDestructive,
      })
      setSessionId(client.getSessionId())
    } catch (err) {
      setDetail(err instanceof Error ? err.message : String(err))
      teardown()
      return
    }

    const recorder = new MicRecorder({
      onChunk: (b64) => client.sendAudio(b64),
      onLevel: setLevel,
    })
    recorderRef.current = recorder
    try {
      await recorder.start()
    } catch (err) {
      // Almost always a denied mic permission. Say so instead of sitting in a
      // "Live" state that will never produce audio.
      setStatus('error')
      setDetail(
        err instanceof Error && err.name === 'NotAllowedError'
          ? 'Microphone access was denied. Allow it in your browser to speak.'
          : `Could not start the microphone: ${err instanceof Error ? err.message : String(err)}`
      )
      teardown()
    }
  }, [teardown, presetId])

  const stop = useCallback(() => {
    teardown()
    setStatus('closed')
    setLevel(0)
  }, [teardown])

  const sendTyped = useCallback(() => {
    if (!typed.trim()) return
    clientRef.current?.sendText(typed)
    setTranscript((prev) => [
      ...prev,
      { id: `typed-${Date.now()}`, sender: 'user', text: typed, isFinal: true, at: Date.now() },
    ])
    setTyped('')
  }, [typed])

  const live = status === 'connected' || status === 'opening'

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '24px 16px' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>Voice</h1>
        <span
          aria-live="polite"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 13,
            color: STATUS_COLOR[status],
          }}
        >
          <span
            aria-hidden
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: STATUS_COLOR[status],
            }}
          />
          {STATUS_LABEL[status]}
        </span>
      </header>
      <p style={{ color: '#6b7280', fontSize: 13, marginTop: 0 }}>
        Talk to your agent. The conversation is recorded on Vantage as a voice session, so the
        transcript is searchable afterwards.
      </p>

      {detail && (
        <div
          role="alert"
          style={{
            background: status === 'error' ? '#fef2f2' : '#f9fafb',
            border: `1px solid ${status === 'error' ? '#fecaca' : '#e5e7eb'}`,
            color: status === 'error' ? '#991b1b' : '#374151',
            borderRadius: 8,
            padding: '10px 12px',
            fontSize: 13,
            marginBottom: 12,
          }}
        >
          {detail}
        </div>
      )}

      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 12, color: '#374151', display: 'block', marginBottom: 4 }}>
          Tool access
        </label>
        <select
          value={presetId}
          onChange={(e) => setPresetId(e.target.value)}
          disabled={live}
          style={{ padding: '6px 8px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13 }}
        >
          {TOOL_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.label}
            </option>
          ))}
        </select>
        <span style={{ fontSize: 12, color: '#6b7280', marginLeft: 8 }}>
          {TOOL_PRESETS.find((p) => p.id === presetId)?.note}
        </span>
      </div>

      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 13, color: '#374151', display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="checkbox"
            checked={allowDestructive}
            onChange={(e) => setAllowDestructive(e.target.checked)}
            disabled={live}
          />
          Allow destructive actions this session
        </label>
        <span style={{ fontSize: 12, color: '#6b7280', marginLeft: 24 }}>
          Off by default: orders, deletions and wallet operations get refused with a
          confirmation-needed message instead of running. Turn this on before starting if you
          intend to ask for one of those out loud.
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        {!live ? (
          <button onClick={start} style={buttonStyle('#111827')}>
            Start talking
          </button>
        ) : (
          <button onClick={stop} style={buttonStyle('#dc2626')}>
            End session
          </button>
        )}

        {/* Mic level: the quickest way to tell "it is listening" from
            "it is connected but hearing nothing". */}
        <div
          aria-label="microphone level"
          style={{ flex: 1, height: 6, background: '#e5e7eb', borderRadius: 3, overflow: 'hidden' }}
        >
          <div
            style={{
              width: `${Math.min(100, level * 300)}%`,
              height: '100%',
              background: '#059669',
              transition: 'width 80ms linear',
            }}
          />
        </div>
      </div>

      <div
        style={{
          border: '1px solid #e5e7eb',
          borderRadius: 10,
          padding: 14,
          minHeight: 220,
          maxHeight: 420,
          overflowY: 'auto',
          background: '#fff',
        }}
      >
        {transcript.length === 0 ? (
          <p style={{ color: '#9ca3af', fontSize: 13, margin: 0 }}>
            {live ? 'Listening…' : 'Nothing yet.'}
          </p>
        ) : (
          transcript.map((entry) => (
            <div key={entry.id} style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', color: '#9ca3af' }}>
                {entry.sender === 'user' ? 'You' : 'Agent'}
              </div>
              <div style={{ fontSize: 14, color: '#111827' }}>{entry.text}</div>
            </div>
          ))
        )}
        <div ref={transcriptEndRef} />
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && sendTyped()}
          placeholder={live ? 'Or type instead…' : 'Start a session to type'}
          disabled={!live}
          style={{
            flex: 1,
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid #d1d5db',
            fontSize: 14,
          }}
        />
        <button onClick={sendTyped} disabled={!live || !typed.trim()} style={buttonStyle('#374151')}>
          Send
        </button>
      </div>

      {toolCalls.length > 0 && (
        <div style={{ marginTop: 16, fontSize: 12, color: '#6b7280' }}>
          <strong style={{ color: '#374151' }}>Tool calls</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {toolCalls.map((call, i) => (
              <li key={`${call.name}-${i}`}>
                <code>{call.name}</code>
                {call.status
                  ? ` — ${call.status}${call.durationMs != null ? ` (${call.durationMs}ms)` : ''}`
                  : ' — running…'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {sessionId && (
        <p style={{ marginTop: 16, fontSize: 12, color: '#9ca3af' }}>
          Session <code>{sessionId}</code>
        </p>
      )}
    </div>
  )
}

function buttonStyle(background: string): React.CSSProperties {
  return {
    background,
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '8px 14px',
    fontSize: 14,
    cursor: 'pointer',
  }
}
