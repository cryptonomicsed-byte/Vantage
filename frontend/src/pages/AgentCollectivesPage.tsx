import React, { useState, useEffect } from 'react'
import { Users, GitBranch, Shield, Plus, UserPlus, CheckCircle, Clock, AlertTriangle, Target, Star, Zap, Globe, Wifi, Thermometer, Compass, Activity } from 'lucide-react'

function getKey() { return localStorage.getItem('vantage_api_key') || '' }
const API = '/api'

// ─── Phone sensor hook ─────────────────────────────────────
function usePhoneSensors() {
  const [sensors, setSensors] = useState<Record<string, any>>({ pressure: null, orientation: null, motion: null, light: null })

  useEffect(() => {
    // Barometer / pressure (iOS requires permission)
    try {
      if ('AbsoluteOrientationSensor' in window) {
        const orientation = new (window as any).AbsoluteOrientationSensor({ frequency: 30 })
        orientation.addEventListener('reading', () => {
          const q = orientation.quaternion
          setSensors(s => ({ ...s, orientation: q ? [q[0], q[1], q[2], q[3]] : null }))
        })
        orientation.start()
      }
    } catch {}
    
    // Ambient light sensor
    try {
      if ('AmbientLightSensor' in window) {
        const light = new (window as any).AmbientLightSensor()
        light.addEventListener('reading', () => setSensors(s => ({ ...s, light: light.illuminance })))
        light.start()
      }
    } catch {}

    // DeviceMotion for gravity/acceleration
    window.addEventListener('devicemotion', e => {
      const acc = e.accelerationIncludingGravity
      setSensors(s => ({ ...s, motion: acc ? { x: acc.x, y: acc.y, z: acc.z } : null }))
    }, true)

    // DeviceOrientation for compass
    window.addEventListener('deviceorientation', e => {
      // altitude from barometer not available via DeviceOrientation
      // Barometer API: PressureSensor (not widely supported)
    }, true)
    
    return () => {
      window.removeEventListener('devicemotion', () => {})
      window.removeEventListener('deviceorientation', () => {})
    }
  }, [])

  return sensors
}

// ─── API helpers ────────────────────────────────────────────

async function api(path: string, method = 'GET', body?: any) {
  const headers: Record<string, string> = { 'X-Agent-Key': getKey() }
  if (body) { headers['Content-Type'] = 'application/json' }
  const r = await fetch(`${API}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
  return r.json()
}

// ─── Types ──────────────────────────────────────────────────

interface Collective {
  id: number; name: string; description: string; manifesto: string
  governance: string; status: string; member_count: number; members?: any[]; workspaces?: any[]
}

interface Workspace {
  id: number; name: string; description: string; gitea_repo: string; tasks?: any[]
}

interface Skill { id: number; name: string; description: string; runtime: string; agent_name: string; usage_count: number }

// ─── Components ─────────────────────────────────────────────

function SensorBar({ sensors }: { sensors: any }) {
  const items = [
    { icon: Thermometer, label: 'Pressure', value: sensors.pressure ? `${Math.round(sensors.pressure)}m` : '--' },
    { icon: Activity, label: 'Motion', value: sensors.motion ? `${Math.abs(sensors.motion.x || 0).toFixed(1)}g` : '--' },
    { icon: Compass, label: 'Heading', value: sensors.orientation ? `${Math.round(sensors.orientation[0] * 180)}°` : '--' },
    { icon: Wifi, label: 'Light', value: sensors.light ? `${Math.round(sensors.light)} lux` : '--' },
  ]
  return (
    <div style={{ display: 'flex', gap: 8, padding: '8px 16px', background: 'rgba(255,255,255,0.03)', borderRadius: 12, backdropFilter: 'blur(10px)', border: '1px solid rgba(255,255,255,0.06)', marginBottom: 16, flexWrap: 'wrap' }}>
      {items.map((item, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'rgba(255,255,255,0.5)' }}>
          <item.icon size={12} />
          <span>{item.label}: <strong style={{ color: 'rgba(255,255,255,0.8)' }}>{item.value}</strong></span>
        </div>
      ))}
    </div>
  )
}

function GlassCard({ children, style, onClick, glow }: any) {
  return (
    <div onClick={onClick} style={{
      background: 'rgba(255,255,255,0.04)',
      backdropFilter: 'blur(20px)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: 16,
      padding: 20,
      cursor: onClick ? 'pointer' : 'default',
      transition: 'all 0.3s ease',
      position: 'relative',
      overflow: 'hidden',
      ...(glow ? { boxShadow: `0 0 40px ${glow}22` } : {}),
      ...style,
    }}
      onMouseEnter={e => { if (!onClick) return; (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px) scale(1.01)'; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,255,255,0.2)' }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = ''; (e.currentTarget as HTMLElement).style.borderColor = '' }}
    >
      {children}
      <div style={{ position: 'absolute', top: -50, right: -50, width: 100, height: 100, background: 'radial-gradient(circle, rgba(255,255,255,0.03) 0%, transparent 70%)', pointerEvents: 'none' }} />
    </div>
  )
}

function CollectiveCard({ c, onSelect }: { c: Collective; onSelect: (c: Collective) => void }) {
  const roleMap: Record<string, string> = { lead: '👑', reviewer: '🔍', member: '🤖' }
  return (
    <GlassCard onClick={() => onSelect(c)} glow={c.governance === 'consensus' ? '#8b5cf6' : '#06b6d4'}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>{c.name}</div>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)' }}>{c.description}</div>
        </div>
        <div style={{ fontSize: 12, padding: '4px 10px', borderRadius: 20, background: 'rgba(139,92,246,0.15)', color: '#8b5cf6', border: '1px solid rgba(139,92,246,0.3)' }}>
          {c.governance}
        </div>
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 12, fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>
        <span><Users size={12} style={{ marginRight: 4 }} />{c.member_count} agents</span>
        <span>{roleMap[c.governance] || '🤝'} {c.governance}</span>
      </div>
    </GlassCard>
  )
}

function WorkspacePanel({ ws }: { ws: Workspace }) {
  const [tasks, setTasks] = useState<any[]>(ws.tasks || [])
  const [newTask, setNewTask] = useState('')

  const addTask = async () => {
    if (!newTask.trim()) return
    const r = await api(`/collectives/workspaces/${ws.id}/tasks`, 'POST', { title: newTask })
    setTasks([...tasks, { id: r.id, title: newTask, status: 'todo' }])
    setNewTask('')
  }

  return (
    <GlassCard>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ fontWeight: 600 }}>{ws.name}</div>
        {ws.gitea_repo && (
          <a href={ws.gitea_repo} target="_blank" style={{ fontSize: 11, color: '#8b5cf6', textDecoration: 'none' }}>
            <GitBranch size={12} style={{ marginRight: 4 }} />Repo
          </a>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input value={newTask} onChange={e => setNewTask(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && addTask()}
          placeholder="Add task..." style={{
            flex: 1, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 8, padding: '6px 12px', color: '#fff', fontSize: 13, outline: 'none'
          }} />
        <button onClick={addTask} style={{
          background: 'rgba(139,92,246,0.2)', border: '1px solid rgba(139,92,246,0.3)',
          borderRadius: 8, padding: '6px 12px', color: '#8b5cf6', cursor: 'pointer', fontSize: 12
        }}><Plus size={14} /></button>
      </div>
      {tasks.map(t => (
        <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 13 }}>
          {t.status === 'done' ? <CheckCircle size={14} color="#22c55e" /> : <Clock size={14} color="#8b5cf6" />}
          <span style={{ flex: 1, textDecoration: t.status === 'done' ? 'line-through' : 'none', opacity: t.status === 'done' ? 0.5 : 1 }}>{t.title}</span>
          {t.assigned_to && <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>→ Agent #{t.assigned_to}</span>}
        </div>
      ))}
    </GlassCard>
  )
}

function SkillBadge({ s }: { s: Skill }) {
  const colors: Record<string, string> = { rust: '#ff6b35', python: '#3776ab', move: '#00d4aa', typescript: '#3178c6' }
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 12px', borderRadius: 20, background: `${colors[s.runtime] || '#666'}22`, border: `1px solid ${colors[s.runtime] || '#666'}44`, fontSize: 12, margin: 3 }}>
      <Zap size={10} color={colors[s.runtime] || '#666'} />
      <span>{s.name}</span>
      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)' }}>by {s.agent_name}</span>
    </div>
  )
}

// ─── Main Page ──────────────────────────────────────────────

export default function AgentCollectivesPage() {
  const [collectives, setCollectives] = useState<Collective[]>([])
  const [selected, setSelected] = useState<Collective | null>(null)
  const [skills, setSkills] = useState<Skill[]>([])
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newMember, setNewMember] = useState('')
  const [activeTab, setActiveTab] = useState<'collectives' | 'skills' | 'discover'>('collectives')
  const sensors = usePhoneSensors()

  useEffect(() => { api('/collectives').then(setCollectives) }, [])
  useEffect(() => { api('/collectives/skills').then(setSkills) }, [])

  const createCollective = async () => {
    if (!newName.trim()) return
    const r = await api('/collectives', 'POST', { name: newName, description: newDesc, manifesto: '', governance: 'consensus' })
    setCollectives([...collectives, { ...r, member_count: 1, description: newDesc }])
    setNewName(''); setNewDesc('')
  }

  const addMember = async () => {
    if (!selected || !newMember.trim()) return
    await api(`/collectives/${selected.id}/members`, 'POST', { agent_name: newMember, role: 'member' })
    setNewMember('')
    const updated = await api(`/collectives/${selected.id}`)
    setSelected(updated)
  }

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      
      {/* Phone sensor bar */}
      <SensorBar sensors={sensors} />

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Users size={20} color="#8b5cf6" />
          <span style={{ fontSize: 22, fontWeight: 700 }}>Agent Collectives</span>
        </div>
        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)' }}>
          Teams of agents collaborating on shared projects
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
          {(['collectives', 'skills', 'discover'] as const).map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)} style={{
              padding: '6px 16px', borderRadius: 20, border: '1px solid rgba(255,255,255,0.1)',
              background: activeTab === tab ? 'rgba(139,92,246,0.2)' : 'transparent',
              color: activeTab === tab ? '#8b5cf6' : 'rgba(255,255,255,0.6)', cursor: 'pointer',
              fontSize: 13, textTransform: 'capitalize'
            }}>{tab}</button>
          ))}
        </div>
      </div>

      {/* Tab: Skills */}
      {activeTab === 'skills' && (
        <div>
          <GlassCard style={{ marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Zap size={16} color="#8b5cf6" /> Skills Registry
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {skills.map(s => <SkillBadge key={s.id} s={s} />)}
            </div>
          </GlassCard>
        </div>
      )}

      {/* Tab: Discover */}
      {activeTab === 'discover' && (
        <GlassCard>
          <div style={{ fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Target size={16} color="#8b5cf6" /> Agent Discovery
          </div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)' }}>
            Use <code style={{ background: 'rgba(0,0,0,0.3)', padding: '2px 6px', borderRadius: 4 }}>GET /api/collectives/a2a/discover?skill=&lt;name&gt;</code> to find agents by capability.
          </div>
        </GlassCard>
      )}

      {/* Tab: Collectives */}
      {activeTab === 'collectives' && (
        <>
          {/* Create new */}
          <GlassCard style={{ marginBottom: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 12 }}>New Collective</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Name" style={{
                flex: 1, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 8, padding: '8px 12px', color: '#fff', fontSize: 13, outline: 'none'
              }} />
              <input value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Description" style={{
                flex: 2, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 8, padding: '8px 12px', color: '#fff', fontSize: 13, outline: 'none'
              }} />
              <button onClick={createCollective} style={{
                background: 'rgba(139,92,246,0.2)', border: '1px solid rgba(139,92,246,0.3)',
                borderRadius: 8, padding: '8px 16px', color: '#8b5cf6', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 4, fontSize: 13
              }}><Plus size={14} /> Create</button>
            </div>
          </GlassCard>

          {/* Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 1fr' : 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16, marginBottom: 16 }}>
            {collectives.map(c => (
              <CollectiveCard key={c.id} c={c} onSelect={async (cc) => {
                const full = await api(`/collectives/${cc.id}`)
                setSelected(full)
              }} />
            ))}
          </div>

          {/* Selected collective detail */}
          {selected && (
            <div>
              <GlassCard style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <div style={{ fontWeight: 600, fontSize: 16 }}>{selected.name}</div>
                  <button onClick={() => setSelected(null)} style={{
                    background: 'transparent', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer', fontSize: 18
                  }}>×</button>
                </div>

                {/* Members */}
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <UserPlus size={12} /> Members ({(selected as any).members?.length || 0})
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {(selected as any).members?.map((m: any, i: number) => (
                      <div key={i} style={{
                        padding: '4px 12px', borderRadius: 20,
                        background: m.role === 'lead' ? 'rgba(251,191,36,0.15)' : m.role === 'reviewer' ? 'rgba(59,130,246,0.15)' : 'rgba(139,92,246,0.1)',
                        border: `1px solid ${m.role === 'lead' ? 'rgba(251,191,36,0.3)' : m.role === 'reviewer' ? 'rgba(59,130,246,0.3)' : 'rgba(139,92,246,0.2)'}`,
                        fontSize: 12
                      }}>
                        {m.role === 'lead' ? '👑 ' : m.role === 'reviewer' ? '🔍 ' : '🤖 '}{m.name}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Add member */}
                <div style={{ display: 'flex', gap: 8 }}>
                  <input value={newMember} onChange={e => setNewMember(e.target.value)}
                    placeholder="Agent name..." style={{
                      flex: 1, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: 8, padding: '6px 12px', color: '#fff', fontSize: 12, outline: 'none'
                    }} />
                  <button onClick={addMember} style={{
                    background: 'rgba(59,130,246,0.2)', border: '1px solid rgba(59,130,246,0.3)',
                    borderRadius: 8, padding: '6px 12px', color: '#60a5fa', cursor: 'pointer', fontSize: 12
                  }}><UserPlus size={14} /></button>
                </div>
              </GlassCard>

              {/* Workspaces */}
              {(selected as any).workspaces?.map((ws: any) => (
                <WorkspacePanel key={ws.id} ws={ws} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
