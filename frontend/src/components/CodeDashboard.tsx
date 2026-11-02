import React, { useEffect, useState, useRef } from 'react'
import { Code2, GitBranch, Shield, GitPullRequest, Clock, ExternalLink, CheckCircle2, AlertTriangle, Play, Activity, Plus, RefreshCw, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

interface RepoInfo {
  name: string; full_name: string; owner: string
  updated_at: string; html_url: string
  language: string; size_kb: number
  branches: string[]; recent_commits: any[]; open_prs: any[]; webhooks: any[]
  strix_scan_status: string; api_endpoints: any
}
interface ActivityEvent { action: string; repo: string; detail: string; agent: string; timestamp: string }

interface ScanState { scanId: string; status: string; repoFull: string }

interface PRModalState { repo: RepoInfo; title: string; body: string; submitting: boolean; result: any | null; error: string }

function apiKey(): string { return localStorage.getItem('vantage_api_key') || '' }

export default function CodeDashboard() {
  const navigate = useNavigate()
  const [data, setData] = useState<any>(null)
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newRepoName, setNewRepoName] = useState('')
  const [scanning, setScanning] = useState<string | null>(null)
  const [scanStates, setScanStates] = useState<Record<string, ScanState>>({})
  const [prModal, setPrModal] = useState<PRModalState | null>(null)
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  const loadAll = () => {
    Promise.all([
      fetch('/api/code/overview').then(r => r.json()),
      fetch('/api/code/activity?limit=15').then(r => r.json()),
    ]).then(([overview, act]) => {
      setData(overview); setActivity(act.activity || []); setLoading(false)
    }).catch(() => setLoading(false))
  }

  useEffect(() => {
    loadAll()
    const t = setInterval(loadAll, 30000)
    return () => {
      clearInterval(t)
      Object.values(pollTimers.current).forEach(clearInterval)
    }
  }, [])

  function pollScanStatus(repoFull: string, scanId: string) {
    if (pollTimers.current[repoFull]) clearInterval(pollTimers.current[repoFull])
    const [owner, name] = repoFull.split('/')
    pollTimers.current[repoFull] = setInterval(() => {
      fetch(`/api/code/repo/${owner}/${name}/scan/${scanId}`, { headers: { 'X-Agent-Key': apiKey() } })
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (!d) return
          setScanStates(prev => ({ ...prev, [repoFull]: { ...prev[repoFull], status: d.status || 'running' } }))
          if (d.status === 'done' || d.status === 'complete' || d.status === 'completed' || d.status === 'failed') {
            clearInterval(pollTimers.current[repoFull])
            delete pollTimers.current[repoFull]
            loadAll()
          }
        })
        .catch(() => {})
    }, 3000)
  }

  const triggerScan = (repo: RepoInfo, e: React.MouseEvent) => {
    e.stopPropagation()
    setScanning(repo.full_name)
    const [owner, name] = repo.full_name.split('/')
    fetch(`/api/code/repo/${owner}/${name}/scan`, { method: 'POST', headers: { 'X-Agent-Key': apiKey() } })
      .then(r => r.json())
      .then(d => {
        const scanId = d.scan_id || d.id || 'unknown'
        setScanStates(prev => ({ ...prev, [repo.full_name]: { scanId, status: 'running', repoFull: repo.full_name } }))
        if (scanId !== 'unknown') pollScanStatus(repo.full_name, scanId)
        else loadAll()
      })
      .catch(() => loadAll())
      .finally(() => setScanning(null))
  }

  const createRepo = () => {
    if (!newRepoName) return
    fetch('/api/code/repo/create', { method: 'POST', headers: {'Content-Type':'application/json', 'X-Agent-Key': apiKey()}, body: JSON.stringify({name: newRepoName, description: 'Created via Vantage'}) })
      .then(r => r.json()).then(() => { setShowCreate(false); setNewRepoName(''); loadAll() })
  }

  async function submitPR() {
    if (!prModal) return
    setPrModal(prev => prev ? { ...prev, submitting: true, error: '' } : null)
    const [owner, name] = prModal.repo.full_name.split('/')
    try {
      const r = await fetch(`/api/code/repo/${owner}/${name}/pr`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Key': apiKey() },
        body: JSON.stringify({ title: prModal.title, body: prModal.body }),
      })
      const d = await r.json()
      if (r.ok) {
        setPrModal(prev => prev ? { ...prev, submitting: false, result: d } : null)
        loadAll()
      } else {
        setPrModal(prev => prev ? { ...prev, submitting: false, error: d.detail || 'PR creation failed' } : null)
      }
    } catch (err: any) {
      setPrModal(prev => prev ? { ...prev, submitting: false, error: err.message || 'Request failed' } : null)
    }
  }

  if (loading) return <div style={{padding:40,textAlign:'center'}}><div className="vf-spinner"/></div>

  return (
    <div>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12,marginBottom:20}}>
        <h1 className="page-title" style={{marginBottom:0}}>Code Collaboration</h1>
        <div style={{display:'flex',gap:8}}><button className="btn btn-sm" onClick={()=>setShowCreate(!showCreate)}><Plus size={14}/> New Repo</button><button className="btn btn-sm" onClick={loadAll}><RefreshCw size={14}/></button></div>
      </div>
      {showCreate && (<div className="cd-create-form"><input className="ares-input" placeholder="repo-name" value={newRepoName} onChange={e=>setNewRepoName(e.target.value)} style={{flex:1}}/><button className="btn" onClick={createRepo}>Create</button><button className="btn btn-sm" onClick={()=>setShowCreate(false)}>Cancel</button></div>)}
      <div className="cd-pipeline"><div className="cd-pipe-step active"><Activity size={14}/> OpenCode</div><div className="cd-pipe-arrow">→</div><div className="cd-pipe-step active"><GitBranch size={14}/> Gitea ({data?.total||0})</div><div className="cd-pipe-arrow">→</div><div className="cd-pipe-step active"><Shield size={14}/> Strix ({data?.with_hooks||0})</div><div className="cd-pipe-arrow">→</div><div className="cd-pipe-step active"><Activity size={14}/> Vantage</div></div>
      <div className="cd-stats"><div className="cd-stat"><Code2 size={16}/><span>{data?.total||0} repos</span></div><div className="cd-stat"><Shield size={16} style={{color:'#22c55e'}}/><span>{data?.with_hooks||0} Strix</span></div><div className="cd-stat"><GitPullRequest size={16}/><span>{data?.open_prs_total||0} PRs</span></div></div>
      <div className="cd-main-layout">
        <div className="cd-grid">
          {data?.repos.map((repo: RepoInfo) => {
            const scan = scanStates[repo.full_name]
            return (
              <div key={repo.full_name} className="cd-card" onClick={() => navigate('/code/' + repo.full_name)} style={{cursor:'pointer'}}>
                <div className="cd-card-header"><GitBranch size={14} className="cd-branch-icon"/><div className="cd-card-title-wrap"><span className="cd-card-title">{repo.name}</span><span className="cd-card-owner">{repo.owner}</span></div>{repo.webhooks?.length > 0 ? <CheckCircle2 size={13} className="cd-stix-ok"/> : <AlertTriangle size={13} className="cd-stix-warn"/>}</div>
                {repo.recent_commits?.length > 0 && <div className="cd-section"><div className="cd-section-label">Recent</div>{repo.recent_commits.map((c:any,i:number)=><div key={i} className="cd-commit"><code className="cd-commit-sha">{c.sha}</code><span className="cd-commit-msg">{c.message}</span><span className="cd-commit-meta">{c.author}</span></div>)}</div>}
                <div className="cd-section"><div className="cd-branches">{repo.branches.map((b:string)=><span key={b} className="cd-branch-tag">{b}</span>)}</div></div>
                {scan && (
                  <div style={{ fontSize: 11, color: scan.status === 'failed' ? '#ef4444' : scan.status === 'done' || scan.status === 'completed' ? '#22c55e' : '#f59e0b', padding: '4px 0', display: 'flex', alignItems: 'center', gap: 5 }}>
                    <Shield size={10} />
                    Scan {scan.scanId !== 'unknown' ? `#${scan.scanId.slice(0,8)}` : ''} — {scan.status}
                  </div>
                )}
                <div className="cd-card-actions">
                  <button className="btn btn-sm cd-action-btn" onClick={(e) => triggerScan(repo, e)} disabled={scanning===repo.full_name}>
                    <Shield size={11}/> {scanning===repo.full_name ? 'Scanning...' : 'Scan'}
                  </button>
                  <button
                    className="btn btn-sm cd-action-btn"
                    onClick={e => { e.stopPropagation(); setPrModal({ repo, title: '', body: '', submitting: false, result: null, error: '' }) }}
                    style={{ marginLeft: 6 }}
                  >
                    <GitPullRequest size={11}/> New PR
                  </button>
                </div>
                <div className="cd-card-footer"><span className="cd-footer-item"><Clock size={10}/> {repo.updated_at?new Date(repo.updated_at).toLocaleDateString():'—'}</span>{repo.language&&<span className="cd-footer-item">{repo.language} · {Math.round(repo.size_kb/1024*10)/10}MB</span>}<a href={repo.html_url} target="_blank" rel="noopener" className="cd-footer-link" onClick={e=>e.stopPropagation()}><ExternalLink size={10}/></a></div>
              </div>
            )
          })}
        </div>
        <div className="cd-activity-panel"><div className="cd-section-label" style={{marginBottom:10}}>Activity</div><div className="cd-activity-list">{activity.map((evt,i)=><div key={i} className="cd-activity-item"><div className="cd-activity-icon">{evt.action==='push'?<Play size={10}/>:evt.action==='scan'?<Shield size={10}/>:<Activity size={10}/>}</div><div className="cd-activity-body"><span className="cd-activity-action">{evt.action.replace('_',' ')}</span><span className="cd-activity-repo">{evt.repo}</span>{evt.detail&&<span className="cd-activity-detail">{evt.detail}</span>}</div></div>)}{activity.length===0&&<div className="cd-empty">No activity yet.</div>}</div></div>
      </div>

      {/* PR Modal */}
      {prModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 }} onClick={() => setPrModal(null)}>
          <div style={{ background: 'var(--surface, #1a1a2e)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, padding: 24, width: 480, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 15, display: 'flex', alignItems: 'center', gap: 8 }}>
                <GitPullRequest size={16} /> New Pull Request — {prModal.repo.name}
              </div>
              <button onClick={() => setPrModal(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)' }}><X size={16} /></button>
            </div>
            {prModal.result ? (
              <div style={{ textAlign: 'center', padding: 16 }}>
                <CheckCircle2 size={28} color="#22c55e" style={{ marginBottom: 8 }} />
                <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 6 }}>PR Created!</div>
                {prModal.result.html_url && (
                  <a href={prModal.result.html_url} target="_blank" rel="noopener" style={{ fontSize: 12, color: '#60a5fa' }}>
                    View PR #{prModal.result.number || ''} →
                  </a>
                )}
                <button className="btn btn-sm" style={{ marginTop: 14, display: 'block', marginLeft: 'auto', marginRight: 'auto' }} onClick={() => setPrModal(null)}>Close</button>
              </div>
            ) : (
              <>
                <div style={{ marginBottom: 12 }}>
                  <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Title</label>
                  <input
                    className="ares-input"
                    value={prModal.title}
                    onChange={e => setPrModal(prev => prev ? { ...prev, title: e.target.value } : null)}
                    placeholder="feat: add new feature"
                    style={{ width: '100%' }}
                  />
                </div>
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Description</label>
                  <textarea
                    className="ares-input"
                    value={prModal.body}
                    onChange={e => setPrModal(prev => prev ? { ...prev, body: e.target.value } : null)}
                    placeholder="Describe the changes in this PR…"
                    rows={4}
                    style={{ width: '100%', resize: 'vertical' }}
                  />
                </div>
                {prModal.error && (
                  <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 10 }}>{prModal.error}</div>
                )}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setPrModal(null)}>Cancel</button>
                  <button
                    className="btn btn-primary btn-sm"
                    disabled={!prModal.title || prModal.submitting}
                    onClick={submitPR}
                    style={{ display: 'flex', alignItems: 'center', gap: 5 }}
                  >
                    <GitPullRequest size={12} />
                    {prModal.submitting ? 'Creating...' : 'Create PR'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
