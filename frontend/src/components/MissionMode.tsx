import React, { useState, useEffect, useCallback } from 'react'
import { Target, Play, Pause, Square, ChevronRight, Clock, Users, Zap, CheckCircle2, Circle, AlertCircle } from 'lucide-react'

interface Task {
  task_id: number
  title: string
  status: string
  agent_name?: string
  guild_slug?: string
  created_at: string
  claimed_at?: string
  completed_at?: string
  capabilities?: string[]
}

interface Mission {
  mission_id: string
  title: string
  objective: string
  tasks: Task[]
  status: 'planning' | 'active' | 'paused' | 'complete'
  created_at: string
}

function statusColor(status: string): string {
  switch (status) {
    case 'open': return '#6b7280'
    case 'claimed': return '#f59e0b'
    case 'executing': return '#3b82f6'
    case 'done': return '#10b981'
    case 'aborted': return '#ef4444'
    default: return '#6b7280'
  }
}

function StatusDot({ status }: { status: string }) {
  const color = statusColor(status)
  const pulse = status === 'executing'
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: color, flexShrink: 0,
      animation: pulse ? 'pulse 1.5s ease-in-out infinite' : 'none',
    }} />
  )
}

function TaskRow({ task }: { task: Task }) {
  const elapsed = task.claimed_at
    ? Math.floor((Date.now() - new Date(task.claimed_at).getTime()) / 1000)
    : null
  const elapsedStr = elapsed != null
    ? elapsed > 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m`
      : elapsed > 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s`
      : `${elapsed}s`
    : null

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
    }}>
      <StatusDot status={task.status} />
      <span style={{ flex: 1, fontSize: 13, color: 'var(--text)' }}>{task.title}</span>
      {task.agent_name && (
        <span style={{ fontSize: 11, color: '#a78bfa', fontFamily: 'monospace' }}>
          @{task.agent_name}
        </span>
      )}
      {elapsedStr && (
        <span style={{ fontSize: 10, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 3 }}>
          <Clock size={9} /> {elapsedStr}
        </span>
      )}
      <span style={{
        fontSize: 10, padding: '1px 6px', borderRadius: 4,
        background: `${statusColor(task.status)}22`, color: statusColor(task.status),
        fontFamily: 'monospace',
      }}>
        {task.status}
      </span>
    </div>
  )
}

function MissionCard({ mission, onActivate }: { mission: Mission; onActivate?: (id: string) => void }) {
  const total = mission.tasks.length
  const done = mission.tasks.filter(t => t.status === 'done').length
  const active = mission.tasks.filter(t => t.status === 'executing' || t.status === 'claimed').length
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
      borderRadius: 12, marginBottom: 12, overflow: 'hidden',
    }}>
      <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 8, flexShrink: 0,
          background: mission.status === 'active' ? 'rgba(59,130,246,0.15)' : 'rgba(255,255,255,0.05)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Target size={16} color={mission.status === 'active' ? '#3b82f6' : 'var(--muted)'} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>
            {mission.title}
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.4 }}>
            {mission.objective}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <span style={{
            fontSize: 10, padding: '2px 8px', borderRadius: 20,
            background: mission.status === 'active' ? 'rgba(59,130,246,0.15)' : 'rgba(255,255,255,0.06)',
            color: mission.status === 'active' ? '#60a5fa' : 'var(--muted)',
          }}>
            {mission.status}
          </span>
          {mission.status === 'planning' && onActivate && (
            <button
              onClick={() => onActivate(mission.mission_id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 4,
                background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.3)',
                borderRadius: 6, padding: '3px 10px', cursor: 'pointer',
                color: '#60a5fa', fontSize: 11,
              }}
            >
              <Play size={10} /> Activate
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div style={{ padding: '0 16px 8px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>
            {active > 0 && <><Zap size={9} style={{ verticalAlign: 'middle' }} /> {active} running · </>}
            {done}/{total} tasks
          </span>
          <span style={{ fontSize: 10, color: pct === 100 ? '#10b981' : 'var(--muted)' }}>{pct}%</span>
        </div>
        <div style={{ height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2 }}>
          <div style={{
            height: '100%', borderRadius: 2,
            background: pct === 100 ? '#10b981' : '#3b82f6',
            width: `${pct}%`, transition: 'width 0.5s ease',
          }} />
        </div>
      </div>

      {/* Task list */}
      {mission.tasks.length > 0 && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
          {mission.tasks.slice(0, 5).map(t => <TaskRow key={t.task_id} task={t} />)}
          {mission.tasks.length > 5 && (
            <div style={{ padding: '6px 12px', fontSize: 11, color: 'var(--muted)' }}>
              +{mission.tasks.length - 5} more tasks
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function NewMissionForm({ onCreated }: { onCreated: (m: Mission) => void }) {
  const [title, setTitle] = useState('')
  const [objective, setObjective] = useState('')
  const [tasks, setTasks] = useState<string[]>([''])
  const [submitting, setSubmitting] = useState(false)

  function addTask() { setTasks(t => [...t, '']) }
  function updateTask(i: number, v: string) { setTasks(t => t.map((x, j) => j === i ? v : x)) }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim() || !objective.trim()) return
    setSubmitting(true)
    const taskList = tasks.filter(t => t.trim()).map((t, i) => ({
      task_id: Date.now() + i, title: t.trim(), status: 'open', created_at: new Date().toISOString(),
    }))
    const mission: Mission = {
      mission_id: crypto.randomUUID(),
      title: title.trim(),
      objective: objective.trim(),
      tasks: taskList,
      status: 'planning',
      created_at: new Date().toISOString(),
    }
    // Persist to localStorage for now (until backend mission table exists)
    const existing = JSON.parse(localStorage.getItem('vantage_missions') || '[]')
    localStorage.setItem('vantage_missions', JSON.stringify([...existing, mission]))
    setTitle(''); setObjective(''); setTasks([''])
    setSubmitting(false)
    onCreated(mission)
  }

  return (
    <form onSubmit={submit} style={{
      background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)',
      borderRadius: 12, padding: 16, marginBottom: 20,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 12 }}>
        New Mission
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <input
          value={title} onChange={e => setTitle(e.target.value)}
          placeholder="Mission title..."
          style={{
            background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '8px 12px', color: 'var(--text)', fontSize: 13,
          }}
        />
        <textarea
          value={objective} onChange={e => setObjective(e.target.value)}
          placeholder="Objective and success criteria..."
          rows={2}
          style={{
            background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '8px 12px', color: 'var(--text)', fontSize: 12,
            resize: 'vertical', fontFamily: 'inherit',
          }}
        />
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>Tasks</div>
          {tasks.map((t, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
              <input
                value={t} onChange={e => updateTask(i, e.target.value)}
                placeholder={`Task ${i + 1}...`}
                style={{
                  flex: 1, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)',
                  borderRadius: 8, padding: '6px 10px', color: 'var(--text)', fontSize: 12,
                }}
              />
            </div>
          ))}
          <button type="button" onClick={addTask} style={{
            background: 'none', border: '1px dashed rgba(255,255,255,0.15)',
            borderRadius: 8, padding: '4px 12px', color: 'var(--muted)', cursor: 'pointer', fontSize: 11,
          }}>+ Add task</button>
        </div>
        <button type="submit" disabled={submitting || !title.trim()} style={{
          background: submitting || !title.trim() ? 'rgba(59,130,246,0.3)' : 'rgba(59,130,246,0.8)',
          border: 'none', borderRadius: 8, padding: '8px 16px', color: 'white',
          fontSize: 13, fontWeight: 600, cursor: submitting || !title.trim() ? 'default' : 'pointer',
        }}>
          {submitting ? 'Creating...' : 'Create Mission'}
        </button>
      </div>
    </form>
  )
}

export default function MissionMode() {
  const [missions, setMissions] = useState<Mission[]>([])
  const [showNew, setShowNew] = useState(false)
  const [activeTasks, setActiveTasks] = useState<Task[]>([])

  useEffect(() => {
    const stored = JSON.parse(localStorage.getItem('vantage_missions') || '[]')
    setMissions(stored)
  }, [])

  useEffect(() => {
    const apiKey = localStorage.getItem('vantage_api_key')
    if (!apiKey) return
    function poll() {
      fetch('/api/tasks?status=claimed,executing&limit=20', { headers: { 'X-Agent-Key': apiKey! } })
        .then(r => r.ok ? r.json() : [])
        .then((data: Task[]) => setActiveTasks(Array.isArray(data) ? data : []))
        .catch(() => {})
    }
    poll()
    const t = setInterval(poll, 8000)
    return () => clearInterval(t)
  }, [])

  function handleCreated(m: Mission) {
    setMissions(prev => [m, ...prev])
    setShowNew(false)
  }

  function activateMission(id: string) {
    setMissions(prev => {
      const updated = prev.map(m => m.mission_id === id ? { ...m, status: 'active' as const } : m)
      localStorage.setItem('vantage_missions', JSON.stringify(updated))
      return updated
    })
  }

  const activeMissions = missions.filter(m => m.status === 'active')
  const planningMissions = missions.filter(m => m.status === 'planning')
  const completeMissions = missions.filter(m => m.status === 'complete')

  return (
    <div style={{ padding: '24px 28px', maxWidth: 760, margin: '0 auto' }}>
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Target size={18} color="#3b82f6" />
          <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>Mission Mode</span>
        </div>
        <button onClick={() => setShowNew(!showNew)} style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.3)',
          borderRadius: 8, padding: '6px 14px', cursor: 'pointer', color: '#60a5fa', fontSize: 12,
        }}>
          <Target size={12} /> New Mission
        </button>
      </div>

      {showNew && <NewMissionForm onCreated={handleCreated} />}

      {/* Live activity */}
      {activeTasks.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Zap size={10} color="#f59e0b" /> Live Activity
          </div>
          <div style={{ background: 'rgba(245,158,11,0.05)', border: '1px solid rgba(245,158,11,0.15)', borderRadius: 10, overflow: 'hidden' }}>
            {activeTasks.map(t => <TaskRow key={t.task_id} task={t} />)}
          </div>
        </div>
      )}

      {/* Active missions */}
      {activeMissions.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Active
          </div>
          {activeMissions.map(m => <MissionCard key={m.mission_id} mission={m} />)}
        </div>
      )}

      {/* Planning missions */}
      {planningMissions.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Planning
          </div>
          {planningMissions.map(m => <MissionCard key={m.mission_id} mission={m} onActivate={activateMission} />)}
        </div>
      )}

      {/* Complete missions */}
      {completeMissions.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Complete
          </div>
          {completeMissions.map(m => <MissionCard key={m.mission_id} mission={m} />)}
        </div>
      )}

      {missions.length === 0 && !showNew && (
        <div style={{
          textAlign: 'center', padding: '60px 20px',
          color: 'var(--muted)', fontSize: 13,
        }}>
          <Target size={28} style={{ opacity: 0.3, marginBottom: 12 }} />
          <div>No missions yet.</div>
          <div style={{ fontSize: 11, marginTop: 4 }}>Create a mission to coordinate multi-task agent workflows.</div>
        </div>
      )}
    </div>
  )
}
