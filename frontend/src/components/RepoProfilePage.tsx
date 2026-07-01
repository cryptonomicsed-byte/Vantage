import React, { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { GitBranch, Shield, GitPullRequest, Clock, ExternalLink, CheckCircle2, AlertTriangle, Star, GitFork, Users, Play, Code2, ArrowLeft } from 'lucide-react'

export default function RepoProfilePage() {
  const { owner, name } = useParams<{owner: string; name: string}>()
  const [repo, setRepo] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState<any>(null)

  useEffect(() => { if (!owner || !name) return; fetch('/api/code/repo/' + owner + '/' + name + '/detail').then(r=>r.json()).then(d=>{setRepo(d);setLoading(false)}).catch(()=>setLoading(false)) }, [owner, name])
  const triggerScan = () => { setScanning(true); fetch('/api/code/repo/' + owner + '/' + name + '/scan',{method:'POST'}).then(r=>r.json()).then(d=>{setScanResult(d);setScanning(false)}).catch(()=>setScanning(false)) }

  if (loading) return <div style={{padding:40,textAlign:'center'}}><div className="vf-spinner"/></div>
  if (!repo) return <div style={{padding:40,color:'var(--muted)'}}>Repo not found</div>

  return (
    <div style={{maxWidth:900,margin:'0 auto'}}>
      <Link to="/code" className="rp-back"><ArrowLeft size={14}/> Code Dashboard</Link>
      <div className="rp-header"><div className="rp-header-left"><GitBranch size={22} className="rp-header-icon"/><div><h1 className="rp-title">{repo.name}</h1><span className="rp-owner">{repo.owner}</span></div></div><div className="rp-header-right">{repo.stix_active?<span className="rp-badge rp-badge-ok"><Shield size={12}/> STIX Active</span>:<span className="rp-badge rp-badge-warn"><AlertTriangle size={12}/> STIX Missing</span>}<a href={repo.html_url} target="_blank" rel="noopener" className="btn btn-sm"><ExternalLink size={12}/> Gitea</a></div></div>
      <div className="rp-stats"><div className="rp-stat"><Star size={14}/>{repo.stars} stars</div><div className="rp-stat"><GitFork size={14}/>{repo.forks} forks</div><div className="rp-stat"><GitPullRequest size={14}/>{repo.open_issues} issues</div><div className="rp-stat"><Users size={14}/>{repo.collaborators?.length||1} collab</div><div className="rp-stat"><Clock size={14}/>{repo.updated_at?new Date(repo.updated_at).toLocaleDateString():'—'}</div><div className="rp-stat"><Code2 size={14}/>{repo.language||'—'}</div><div className="rp-stat">{(repo.size_kb/1024).toFixed(1)}MB</div></div>
      {repo.description && <p className="rp-description">{repo.description}</p>}
      <div className="rp-grid">
        <div className="rp-col">
          <div className="rp-section"><div className="rp-section-title"><Shield size={15}/> STIX Security</div>{repo.stix_active?<div><div className="rp-stix-status"><CheckCircle2 size={14} style={{color:'#22c55e'}}/> Active on every push</div>{repo.stix_webhooks?.map((h:any,i:number)=><div key={i} className="rp-info-row"><span className="rp-info-label">Hook #{h.id}</span><span className="rp-info-value">{h.active?'Active':'Inactive'}</span></div>)}<button className="btn btn-sm" onClick={triggerScan} disabled={scanning} style={{marginTop:10}}><Play size={12}/> {scanning?'Scanning...':'Run STIX Scan'}</button></div>:<div className="rp-stix-status"><AlertTriangle size={14} style={{color:'#f59e0b'}}/> No STIX webhook</div>}{scanResult&&<div className="rp-scan-result" style={{marginTop:12}}><div className="rp-scan-header"><span>Scan</span><span>{scanResult.files_scanned} files</span></div><div className="rp-scan-counts"><span className="rp-sev rp-sev-crit">{scanResult.critical} critical</span><span className="rp-sev rp-sev-high">{scanResult.high} high</span></div>{scanResult.findings?.slice(0,5).map((f:any,i:number)=><div key={i} className="rp-finding"><span className={'rp-finding-sev '+(f.severity>=0.9?'crit':'high')}>{f.vuln_id}</span><span className="rp-finding-file">{f.file}:{f.line}</span><span className="rp-finding-snippet">{f.snippet}</span></div>)}</div>}</div>
          <div className="rp-section"><div className="rp-section-title"><GitBranch size={15}/> Branches ({repo.branches?.length||0})</div><div className="rp-branches">{repo.branches?.map((b:string)=><span key={b} className={'rp-branch '+(b===repo.default_branch?'rp-branch-default':'')}>{b}</span>)}</div></div>
          <div className="rp-section"><div className="rp-section-title">Clone</div><code className="rp-clone-url">git clone {repo.clone_url}</code></div>
        </div>
        <div className="rp-col">
          <div className="rp-section"><div className="rp-section-title">Recent Commits</div><div className="rp-commits">{repo.recent_commits?.map((c:any,i:number)=><div key={i} className="rp-commit"><code className="rp-commit-sha">{c.sha}</code><div className="rp-commit-body"><span className="rp-commit-msg">{c.message}</span><span className="rp-commit-meta">{c.author} · {c.date?new Date(c.date).toLocaleDateString():''}</span></div></div>)}{(!repo.recent_commits||repo.recent_commits.length===0)&&<span className="rp-empty">No commits</span>}</div></div>
          <div className="rp-section"><div className="rp-section-title"><GitPullRequest size={15}/> Open PRs</div>{repo.open_prs?.map((pr:any,i:number)=><div key={i} className="rp-pr"><GitPullRequest size={12} style={{color:'#22c55e'}}/><span className="rp-pr-title">#{pr.number} {pr.title}</span><span className="rp-pr-author">{pr.author}</span></div>)}{(!repo.open_prs||repo.open_prs.length===0)&&<span className="rp-empty">No open PRs</span>}</div>
          <div className="rp-section"><div className="rp-section-title">Agent API</div><div className="rp-api-list">{repo.api_endpoints&&Object.entries(repo.api_endpoints).map(([key,val]:[string,any])=><code key={key} className="rp-api-endpoint"><span className="rp-api-method">POST</span>{val}<span className="rp-api-label">{key}</span></code>)}</div></div>
        </div>
      </div>
    </div>
  )
}
