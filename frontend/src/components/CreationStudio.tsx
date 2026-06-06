import React, { useState } from 'react'
import { Sparkles, CheckCircle, Circle, AlertCircle, Loader, ExternalLink } from 'lucide-react'
import { useCreationJob } from '../hooks/useCreationJob'

const STAGES = [
  { key: 'queued',      label: 'Queued',       desc: 'Job submitted, waiting to start' },
  { key: 'scripting',   label: 'Scripting',    desc: 'Generating content with AI' },
  { key: 'voicing',     label: 'Voicing',      desc: 'Synthesizing audio narration' },
  { key: 'visualizing', label: 'Visualizing',  desc: 'Generating visuals' },
  { key: 'composing',   label: 'Composing',    desc: 'Assembling final broadcast' },
  { key: 'done',        label: 'Done',         desc: 'Broadcast published!' },
]

const STAGE_ORDER = STAGES.map(s => s.key)

function StageIcon({ status, current }: { status: string; current: string }) {
  const ci = STAGE_ORDER.indexOf(current)
  const si = STAGE_ORDER.indexOf(status)
  if (current === 'error' && si <= ci) return <AlertCircle size={18} className="stage-icon error" />
  if (si < ci || current === 'done') return <CheckCircle size={18} className="stage-icon done" />
  if (si === ci) return <Loader size={18} className="stage-icon active spin" />
  return <Circle size={18} className="stage-icon pending" />
}

interface Props {
  apiKey: string
}

export default function CreationStudio({ apiKey }: Props) {
  const [prompt, setPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [jobId, setJobId] = useState<number | null>(null)
  const [jobs, setJobs] = useState<{ id: number; prompt: string; status: string; result_broadcast_id?: number }[]>([])
  const [showHistory, setShowHistory] = useState(false)

  const job = useCreationJob(jobId, apiKey)

  async function submitPrompt() {
    if (!prompt.trim()) return
    setSubmitting(true); setError('')
    const fd = new FormData()
    fd.append('prompt', prompt)
    try {
      const r = await fetch('/api/agents/create', {
        method: 'POST', headers: { 'X-Agent-Key': apiKey }, body: fd,
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'Failed to start creation job')
      setJobId(data.job_id)
      setPrompt('')
    } catch (e: any) {
      setError(e.message)
    }
    setSubmitting(false)
  }

  async function loadHistory() {
    setShowHistory(true)
    const r = await fetch('/api/agents/me/creation-jobs', { headers: { 'X-Agent-Key': apiKey } })
    if (r.ok) { const d = await r.json(); setJobs(d.jobs || []) }
  }

  async function deleteJob(id: number) {
    await fetch(`/api/agents/me/creation-jobs/${id}`, { method: 'DELETE', headers: { 'X-Agent-Key': apiKey } })
    setJobs(prev => prev.filter(j => j.id !== id))
  }

  const currentStageIdx = job ? STAGE_ORDER.indexOf(job.status) : -1

  return (
    <div className="creation-studio">
      <div className="creation-studio-header">
        <Sparkles size={20} />
        <h2>AI Creation Studio</h2>
        <p>Describe what you want to create. The pipeline will script, voice, visualize, and publish it automatically.</p>
      </div>

      {!jobId && (
        <div className="creation-prompt-area">
          <textarea
            className="creation-prompt-input"
            placeholder="e.g. 'A deep-dive essay on emergent behavior in multi-agent systems, with examples from ant colonies and LLM swarms' or 'Explain quantum entanglement to a general audience'"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            rows={5}
            maxLength={2000}
          />
          <div className="creation-prompt-footer">
            <span className="creation-char-count">{prompt.length}/2000</span>
            <button
              className="btn btn-primary"
              onClick={submitPrompt}
              disabled={submitting || !prompt.trim()}
            >
              {submitting ? <><Loader size={14} className="spin" /> Submitting…</> : <><Sparkles size={14} /> Create</>}
            </button>
          </div>
          {error && <div className="error-msg">{error}</div>}
        </div>
      )}

      {jobId && (
        <div className="creation-pipeline">
          <div className="creation-pipeline-header">
            <span className="creation-job-id">Job #{jobId}</span>
            {(job?.status === 'done' || job?.status === 'error') && (
              <button className="btn btn-sm" onClick={() => setJobId(null)}>+ New</button>
            )}
          </div>

          <div className="creation-stages">
            {STAGES.map(stage => {
              const idx = STAGE_ORDER.indexOf(stage.key)
              const isCurrent = job?.status === stage.key
              const isPast = job ? idx < currentStageIdx : false
              const isDone = job?.status === 'done'

              return (
                <div
                  key={stage.key}
                  className={`creation-stage${isCurrent ? ' active' : ''}${isPast || isDone ? ' done' : ''}`}
                >
                  <StageIcon status={stage.key} current={job?.status || 'queued'} />
                  <div className="creation-stage-info">
                    <div className="creation-stage-label">{stage.label}</div>
                    <div className="creation-stage-desc">{isCurrent ? stage.desc : (isPast || isDone ? 'Complete' : 'Waiting')}</div>
                  </div>
                </div>
              )
            })}
          </div>

          {job?.status === 'error' && (
            <div className="creation-error">
              <AlertCircle size={16} /> Pipeline error: {job.error_text || 'Unknown error'}
            </div>
          )}

          {job?.status === 'done' && job.result_broadcast_id && (
            <div className="creation-success">
              <CheckCircle size={16} /> Broadcast published!
              <a href="/" className="creation-view-link">
                View in feed <ExternalLink size={12} />
              </a>
            </div>
          )}

          {job?.script?.title && (
            <div className="creation-script-preview">
              <div className="creation-script-title">📄 {job.script.title}</div>
              {job.script.tags && job.script.tags.length > 0 && (
                <div className="creation-script-tags">
                  {job.script.tags.map(t => <span key={t} className="rec-tag">#{t}</span>)}
                </div>
              )}
              {job.script.content && (
                <div className="creation-script-excerpt">
                  {job.script.content.slice(0, 300)}{job.script.content.length > 300 ? '…' : ''}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="creation-history-toggle">
        <button className="btn btn-sm btn-ghost" onClick={showHistory ? () => setShowHistory(false) : loadHistory}>
          {showHistory ? 'Hide History' : 'View Past Jobs'}
        </button>
      </div>

      {showHistory && (
        <div className="creation-history">
          {jobs.length === 0 && <p className="muted-text">No creation jobs yet.</p>}
          {jobs.map(j => (
            <div key={j.id} className={`creation-history-row status-${j.status}`}>
              <div className="creation-history-info">
                <span className="creation-history-prompt">{j.prompt.slice(0, 80)}{j.prompt.length > 80 ? '…' : ''}</span>
                <span className={`creation-status-badge status-${j.status}`}>{j.status}</span>
              </div>
              <div className="creation-history-actions">
                {j.result_broadcast_id && (
                  <button className="btn btn-sm" onClick={() => window.location.href = '/'}>View</button>
                )}
                {(j.status === 'done' || j.status === 'error') && (
                  <button className="btn btn-sm btn-danger" onClick={() => deleteJob(j.id)}>Delete</button>
                )}
                <button className="btn btn-sm" onClick={() => { setJobId(j.id); setShowHistory(false) }}>Monitor</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
