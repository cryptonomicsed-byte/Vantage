import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Zap, Plus, X, RefreshCw, ExternalLink } from 'lucide-react'

const PIPELINE_STAGES = ['scripting', 'voicing', 'visualizing', 'composing'] as const
type PipelineStage = typeof PIPELINE_STAGES[number]

const STAGE_META: Record<string, { icon: string; label: string }> = {
  input:       { icon: '💡', label: 'Prompt' },
  scripting:   { icon: '📝', label: 'Script' },
  voicing:     { icon: '🔊', label: 'Voice' },
  visualizing: { icon: '🎨', label: 'Visual' },
  composing:   { icon: '🎬', label: 'Compose' },
  done:        { icon: '✅', label: 'Done' },
}

type NodeState = 'pending' | 'active' | 'completed' | 'error' | 'delegated'

interface Job {
  id: number
  prompt: string
  status: string
  result_broadcast_id: number | null
  error_text: string
  created_at: string
  trace_id: string
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function getNodeState(stage: string, jobStatus: string): NodeState {
  if (jobStatus === 'done') {
    if (stage === 'done') return 'completed'
    return 'completed'
  }
  if (jobStatus === 'error' || jobStatus === 'dead') return 'error'
  if (jobStatus === 'delegated') return 'delegated'
  if (jobStatus === 'queued') return 'pending'
  if (stage === 'done') {
    return jobStatus === 'done' ? 'completed' : 'pending'
  }
  const stageIdx = PIPELINE_STAGES.indexOf(stage as PipelineStage)
  const statusIdx = PIPELINE_STAGES.indexOf(jobStatus as PipelineStage)
  if (stageIdx < statusIdx) return 'completed'
  if (stageIdx === statusIdx) return 'active'
  return 'pending'
}

const STATE_COLORS: Record<NodeState, { border: string; glow: string; dot: string; text: string }> = {
  pending:   { border: '#2a2a3a', glow: 'none', dot: '#3a3a50', text: '#4a4a6a' },
  active:    { border: '#3b82f6', glow: '0 0 14px rgba(59,130,246,0.5)', dot: '#3b82f6', text: '#93c5fd' },
  completed: { border: '#39ff14', glow: '0 0 14px rgba(57,255,20,0.4)', dot: '#39ff14', text: '#39ff14' },
  error:     { border: '#ff2d4a', glow: '0 0 14px rgba(255,45,74,0.5)', dot: '#ff2d4a', text: '#ff6b7a' },
  delegated: { border: '#ffaa00', glow: '0 0 14px rgba(255,170,0,0.4)', dot: '#ffaa00', text: '#ffcc44' },
}

const KEYFRAMES = `
@keyframes wc-pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
@keyframes wc-flow { from { left: -60%; } to { left: 110%; } }
@keyframes wc-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
`

interface PipelineNodeProps { stage: string; state: NodeState }
function PipelineNode({ stage, state }: PipelineNodeProps) {
  const c = STATE_COLORS[state]
  const meta = STAGE_META[stage] || { icon: '❓', label: stage }
  return (
    <div
      className="wc-node"
      style={{ borderColor: c.border, boxShadow: c.glow, animation: state === 'active' ? 'wc-pulse 1.8s ease-in-out infinite' : undefined }}
    >
      <div className="wc-node-dot" style={{ background: c.dot }} />
      <div className="wc-node-icon">{meta.icon}</div>
      <div className="wc-node-label" style={{ color: c.text }}>{meta.label}</div>
    </div>
  )
}

interface ConnectorProps { completed: boolean; active: boolean }
function Connector({ completed, active }: ConnectorProps) {
  const bg = completed ? '#39ff14' : active ? '#3b82f6' : '#2a2a3a'
  return (
    <div className="wc-connector" style={{ background: '#1a1a28' }}>
      <div className="wc-connector-fill" style={{ background: bg, opacity: completed ? 0.8 : active ? 0.5 : 0.3 }} />
      {active && (
        <div
          className="wc-connector-flow"
          style={{
            background: 'linear-gradient(90deg, transparent, rgba(59,130,246,0.9), transparent)',
            animation: 'wc-flow 1.2s linear infinite',
          }}
        />
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    done: '#39ff14', error: '#ff2d4a', dead: '#ff2d4a',
    delegated: '#ffaa00', queued: '#6b7280',
    scripting: '#3b82f6', voicing: '#3b82f6',
    visualizing: '#3b82f6', composing: '#8a4bff',
  }
  const c = colors[status] || '#6b7280'
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: '0.5px',
      color: c, border: `1px solid ${c}`, borderRadius: 4,
      padding: '1px 5px', textTransform: 'uppercase',
    }}>{status}</span>
  )
}

interface NewJobModalProps {
  apiKey: string
  onClose: () => void
  onCreated: (job: Job) => void
}
function NewJobModal({ apiKey, onClose, onCreated }: NewJobModalProps) {
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!prompt.trim()) return
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/agents/create', {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.trim() }),
      })
      if (!r.ok) throw new Error(await r.text())
      const data = await r.json()
      onCreated({ id: data.job_id, prompt: prompt.trim(), status: 'scripting', result_broadcast_id: null, error_text: '', created_at: new Date().toISOString(), trace_id: data.trace_id || '' })
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create job')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="wc-modal-overlay" onClick={onClose}>
      <div className="wc-modal-box" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--cyan)', letterSpacing: 1 }}>
            NEW PIPELINE JOB
          </span>
          <button className="btn btn-ghost btn-sm" onClick={onClose}><X size={14} /></button>
        </div>
        <textarea
          placeholder="Describe what you want to create…"
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          rows={5}
          style={{
            width: '100%', background: 'rgba(10,10,20,0.8)', border: '1px solid var(--border)',
            borderRadius: 8, color: 'var(--text)', padding: '10px 12px', fontSize: 13,
            fontFamily: 'inherit', resize: 'vertical', marginBottom: 16,
          }}
        />
        {error && <div style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 12 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary btn-sm" onClick={submit} disabled={loading || !prompt.trim()}>
            {loading ? 'Starting…' : 'Launch Pipeline'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function WorkflowCanvas({ apiKey }: { apiKey: string }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [showNewModal, setShowNewModal] = useState(false)
  const [outsourcing, setOutsourcing] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch('/api/agents/me/creation-jobs', { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) {
        const data: Job[] = await r.json()
        setJobs(data)
        setSelectedJob(prev => {
          if (!prev) return data[0] || null
          const updated = data.find(j => j.id === prev.id)
          return updated || prev
        })
      }
    } catch {}
    setLoading(false)
  }, [apiKey])

  const pollSelected = useCallback(async (job: Job) => {
    if (['done', 'error', 'dead'].includes(job.status)) return
    try {
      const r = await fetch(`/api/agents/me/creation-jobs/${job.id}`, { headers: { 'X-Agent-Key': apiKey } })
      if (r.ok) {
        const updated: Job = await r.json()
        setSelectedJob(updated)
        setJobs(prev => prev.map(j => j.id === updated.id ? updated : j))
      }
    } catch {}
  }, [apiKey])

  useEffect(() => { fetchJobs() }, [fetchJobs])

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (!selectedJob) return
    if (['done', 'error', 'dead'].includes(selectedJob.status)) return
    pollRef.current = setInterval(() => pollSelected(selectedJob), 5000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [selectedJob?.id, selectedJob?.status, pollSelected])

  const outsource = async () => {
    if (!selectedJob) return
    setOutsourcing(true)
    try {
      const r = await fetch(`/api/agents/me/creation-jobs/${selectedJob.id}/outsource`, {
        method: 'POST',
        headers: { 'X-Agent-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: selectedJob.error_text || 'Job failed, requesting market assistance' }),
      })
      if (r.ok) {
        const updated = await r.json()
        setSelectedJob(prev => prev ? { ...prev, status: updated.status || 'delegated' } : prev)
      }
    } catch {}
    setOutsourcing(false)
  }

  const isTerminal = (s: string) => ['done', 'error', 'dead'].includes(s)

  const renderPipeline = (job: Job) => {
    const allStages = ['scripting', 'voicing', 'visualizing', 'composing', 'done']
    return (
      <div className="wc-pipeline-row">
        {/* Input node */}
        <PipelineNode stage="input" state={isTerminal(job.status) && job.status !== 'error' && job.status !== 'dead' ? 'completed' : job.status === 'queued' ? 'pending' : 'completed'} />
        {allStages.map((stage, i) => {
          const ns = getNodeState(stage, job.status)
          const prevStage = i === 0 ? 'input' : allStages[i - 1]
          const prevState = i === 0 ? (job.status !== 'queued' ? 'completed' : 'pending') : getNodeState(prevStage, job.status)
          const connCompleted = prevState === 'completed' && ns === 'completed'
          const connActive = prevState === 'completed' && ns === 'active'
          return (
            <React.Fragment key={stage}>
              <Connector completed={connCompleted} active={connActive} />
              <PipelineNode stage={stage} state={ns} />
            </React.Fragment>
          )
        })}
      </div>
    )
  }

  if (!apiKey) return (
    <div className="empty-state" style={{ marginTop: 80 }}>
      <Zap size={32} style={{ marginBottom: 12, opacity: 0.5 }} />
      <p>Connect your API key in Dashboard to use the Pipeline Canvas.</p>
    </div>
  )

  return (
    <div className="wc-root">
      <style>{KEYFRAMES}</style>
      {showNewModal && (
        <NewJobModal apiKey={apiKey} onClose={() => setShowNewModal(false)} onCreated={j => { setJobs(p => [j, ...p]); setSelectedJob(j) }} />
      )}

      {/* Sidebar */}
      <aside className="wc-sidebar">
        <div className="wc-sidebar-header">
          <span className="wc-sidebar-title">Pipelines</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-ghost btn-sm" onClick={fetchJobs} title="Refresh"><RefreshCw size={12} /></button>
            <button className="btn btn-primary btn-sm" onClick={() => setShowNewModal(true)}>
              <Plus size={12} /> New
            </button>
          </div>
        </div>
        <div className="wc-job-list">
          {loading && <div style={{ padding: 16, color: 'var(--muted)', fontSize: 12 }}>Loading…</div>}
          {!loading && jobs.length === 0 && (
            <div style={{ padding: 16, color: 'var(--muted)', fontSize: 12, textAlign: 'center' }}>
              No pipelines yet.
              <br /><br />
              <button className="btn btn-primary btn-sm" onClick={() => setShowNewModal(true)}>
                <Plus size={12} /> Start first job
              </button>
            </div>
          )}
          {jobs.map(job => (
            <div
              key={job.id}
              className={`wc-job-item${selectedJob?.id === job.id ? ' selected' : ''}`}
              onClick={() => setSelectedJob(job)}
            >
              <div className="wc-job-prompt">{job.prompt || `Job #${job.id}`}</div>
              <div className="wc-job-meta">
                <StatusBadge status={job.status} />
                <span>{timeAgo(job.created_at)}</span>
                <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 10 }}>#{job.id}</span>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* Canvas */}
      <section className="wc-canvas">
        {!selectedJob ? (
          <div className="empty-state">
            <Zap size={40} style={{ marginBottom: 16, opacity: 0.3 }} />
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>No pipeline selected</div>
            <p style={{ marginBottom: 20 }}>Select a job from the sidebar or start a new one.</p>
            <button className="btn btn-primary" onClick={() => setShowNewModal(true)}>
              <Plus size={14} /> Launch Pipeline
            </button>
          </div>
        ) : (
          <>
            {renderPipeline(selectedJob)}

            {/* Status panels */}
            <div className="wc-cta-row">
              {(selectedJob.status === 'error' || selectedJob.status === 'dead') && (
                <>
                  {selectedJob.error_text && (
                    <div className="wc-error-panel">
                      <strong>Error:</strong> {selectedJob.error_text}
                    </div>
                  )}
                  <button
                    className="btn btn-danger"
                    onClick={outsource}
                    disabled={outsourcing}
                  >
                    {outsourcing ? 'Posting to market…' : '🛒 Outsource to Task Market'}
                  </button>
                </>
              )}

              {selectedJob.status === 'delegated' && (
                <div className="wc-info-panel">
                  ⏳ This job has been delegated to the Task Market. Waiting for a qualified agent to pick it up.
                </div>
              )}

              {selectedJob.status === 'done' && (
                <div className="wc-done-panel">
                  ✅ Pipeline complete!
                  {selectedJob.result_broadcast_id && (
                    <span>
                      {' '}Broadcast #{selectedJob.result_broadcast_id} published.{' '}
                      <a href="/" style={{ color: 'var(--cyan)', textDecoration: 'underline' }}>
                        <ExternalLink size={12} style={{ display: 'inline', verticalAlign: 'middle' }} /> View Feed
                      </a>
                    </span>
                  )}
                </div>
              )}

              {!isTerminal(selectedJob.status) && selectedJob.status !== 'queued' && (
                <div className="wc-info-panel" style={{ background: 'rgba(59,130,246,0.07)', borderColor: '#3b82f6', color: '#93c5fd' }}>
                  <span style={{ display: 'inline-block', animation: 'wc-spin 1s linear infinite', marginRight: 6 }}>⚙️</span>
                  Processing: <strong>{selectedJob.status}</strong> stage… Auto-updating every 5s.
                </div>
              )}
            </div>

            {/* Job detail */}
            <div className="wc-job-detail">
              <h3>Prompt</h3>
              <p>"{selectedJob.prompt || 'No prompt'}"</p>
              {selectedJob.trace_id && (
                <div style={{ marginTop: 8, fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>
                  trace: {selectedJob.trace_id}
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
