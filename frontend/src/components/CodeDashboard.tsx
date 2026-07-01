import React, { useEffect, useState } from 'react'
import { Code2, GitBranch, Shield, GitPullRequest, Clock, ExternalLink, CheckCircle2, AlertTriangle, Play, Activity, Plus, RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

interface RepoInfo {
  name: string; full_name: string; owner: string
  updated_at: string; html_url: string
  language: string; size_kb: number
  branches: string[]; recent_commits: any[]; open_prs: any[]; webhooks: any[]
  stix_scan_status: string; api_endpoints: any
}
interface ActivityEvent { action: string; repo: string; detail: string; agent: string; timestamp: string }

export default function CodeDashboard() {
  const navigate = useNavigate()
  const [data, setData] = useState<any>(null)
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newRepoName, setNewRepoName] = useState('')
  const [scanning, setScanning] = useState<string | null>(null)

  const loadAll = () => {
    Promise.all([
      fetch('/api/code/overview').then(r => r.json()),
      fetch('/api/code/activity?limit=15').then(r => r.json()),
    ]).then(([overview, act]) => {
      setData(overview); setActivity(act.activity || []); setLoading(false)
    }).catch(() => setLoading(false))
  }

  useEffect(() => { loadAll(); const t = setInterval(loadAll, 30000); return () => clearInterval(t) }, [])

  const triggerScan = (repo: RepoInfo) => {
    setScanning(repo.full_name)
    fetch('REPO_SCAN_URL'.replace('REPO_SCAN_URL', '/api/code/repo/' + repo.full_name + '/scan'), { method: 'POST' })
      .then(r => r.json()).then(() => { loadAll() })
      .finally(() => setScanning(null))
  }

  const createRepo = () => {
    if (!newRepoName) return
    fetch('/api/code/repo/create', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: newRepoName, description: 'Created via Vantage'}) })
      .then(r => r.json()).then(() => { setShowCreate(false); setNewRepoName(''); loadAll() })
  }

  if (loading) return <div style={{padding:40,textAlign:'center'}}><div className="vf-spinner"/></div>

  return (
    <div>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12,marginBottom:20}}>
        <h1 className="page-title" style={{marginBottom:0}}>Code Collaboration</h1>
        <div style={{display:'flex',gap:8}}><button className="btn btn-sm" onClick={()=>setShowCreate(!showCreate)}><Plus size={14}/> New Repo</button><button className="btn btn-sm" onClick={loadAll}><RefreshCw size={14}/></button></div>
      </div>
      {showCreate && (<div className="cd-create-form"><input className="ares-input" placeholder="repo-name" value={newRepoName} onChange={e=>setNewRepoName(e.target.value)} style={{flex:1}}/><button className="btn" onClick={createRepo}>Create</button><button className="btn btn-sm" onClick={()=>setShowCreate(false)}>Cancel</button></div>)}
      <div className="cd-pipeline"><div className="cd-pipe-step active"><Activity size={14}/> OpenCode</div><div className="cd-pipe-arrow">→</div><div className="cd-pipe-step active"><GitBranch size={14}/> Gitea ({data?.total||0})</div><div className="cd-pipe-arrow">→</div><div className="cd-pipe-step active"><Shield size={14}/> STIX ({data?.with_hooks||0})</div><div className="cd-pipe-arrow">→</div><div className="cd-pipe-step active"><Activity size={14}/> Vantage</div></div>
      <div className="cd-stats"><div className="cd-stat"><Code2 size={16}/><span>{data?.total||0} repos</span></div><div className="cd-stat"><Shield size={16} style={{color:'#22c55e'}}/><span>{data?.with_hooks||0} STIX</span></div><div className="cd-stat"><GitPullRequest size={16}/><span>{data?.open_prs_total||0} PRs</span></div></div>
      <div className="cd-main-layout">
        <div className="cd-grid">
          {data?.repos.map((repo: RepoInfo) => (
            <div key={repo.full_name} className="cd-card" onClick={() => navigate('/code/' + repo.full_name)} style={{cursor:'pointer'}}>
              <div className="cd-card-header"><GitBranch size={14} className="cd-branch-icon"/><div className="cd-card-title-wrap"><span className="cd-card-title">{repo.name}</span><span className="cd-card-owner">{repo.owner}</span></div>{repo.webhooks?.length > 0 ? <CheckCircle2 size={13} className="cd-stix-ok"/> : <AlertTriangle size={13} className="cd-stix-warn"/>}</div>
              {repo.recent_commits?.length > 0 && <div className="cd-section"><div className="cd-section-label">Recent</div>{repo.recent_commits.map((c:any,i:number)=><div key={i} className="cd-commit"><code className="cd-commit-sha">{c.sha}</code><span className="cd-commit-msg">{c.message}</span><span className="cd-commit-meta">{c.author}</span></div>)}</div>}
              <div className="cd-section"><div className="cd-branches">{repo.branches.map((b:string)=><span key={b} className="cd-branch-tag">{b}</span>)}</div></div>
              <div className="cd-card-actions"><button className="btn btn-sm cd-action-btn" onClick={(e)=>{e.stopPropagation();triggerScan(repo)}} disabled={scanning===repo.full_name}><Shield size={11}/> {scanning===repo.full_name?'Scanning...':'Scan'}</button></div>
              <div className="cd-card-footer"><span className="cd-footer-item"><Clock size={10}/> {repo.updated_at?new Date(repo.updated_at).toLocaleDateString():'—'}</span>{repo.language&&<span className="cd-footer-item">{repo.language} · {Math.round(repo.size_kb/1024*10)/10}MB</span>}<a href={repo.html_url} target="_blank" rel="noopener" className="cd-footer-link" onClick={e=>e.stopPropagation()}><ExternalLink size={10}/></a></div>
            </div>
          ))}
        </div>
        <div className="cd-activity-panel"><div className="cd-section-label" style={{marginBottom:10}}>Activity</div><div className="cd-activity-list">{activity.map((evt,i)=><div key={i} className="cd-activity-item"><div className="cd-activity-icon">{evt.action==='push'?<Play size={10}/>:evt.action==='scan'?<Shield size={10}/>:<Activity size={10}/>}</div><div className="cd-activity-body"><span className="cd-activity-action">{evt.action.replace('_',' ')}</span><span className="cd-activity-repo">{evt.repo}</span>{evt.detail&&<span className="cd-activity-detail">{evt.detail}</span>}</div></div>)}{activity.length===0&&<div className="cd-empty">No activity yet.</div>}</div></div>
      </div>
    </div>
  )
}
