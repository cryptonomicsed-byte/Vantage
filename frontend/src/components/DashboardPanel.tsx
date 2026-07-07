import React, { useEffect, useState } from 'react'
import { Activity, TrendingUp, TrendingDown, AlertTriangle, Zap, RefreshCw } from 'lucide-react'

export default function DashboardPanel() {
  const [signals, setSignals] = useState<any[]>([])
  const [market, setMarket] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [lastRefresh, setLastRefresh] = useState(Date.now())

  const load = () => {
    Promise.all([
      fetch('/api/intel/signals?limit=10').then(r=>r.json()),
      fetch('/api/intel/market/top?limit=5').then(r=>r.json())
    ]).then(([s,m])=>{setSignals(s.signals||[]);setMarket(m.tokens||[]);setLoading(false);setLastRefresh(Date.now())})
    .catch(()=>setLoading(false))
  }
  useEffect(()=>{load();const t=setInterval(load,15000);return()=>clearInterval(t)},[])

  if(loading)return<div className="td-dashboard"><div className="vf-spinner"/></div>

  return <div className="td-dashboard">
    <div className="td-status-bar"><span className="td-status-dot"/> <span className="td-status-text">Live</span><span className="td-status-info">{signals.length} signals · refresh in <span className="td-status-cd">15s</span></span><button className="td-refresh-btn" onClick={load}><RefreshCw size={12}/></button></div>
    <div className="td-row">
      <div className="td-col-2">
        <div className="td-card"><div className="td-card-header"><Zap size={14}/> Price Gauges</div>
          {market.slice(0,3).map((t:any)=><div key={t.symbol} className="td-gauge-wrap"><span className="td-gauge-label">{t.symbol}</span><span className="td-gauge-value">{t.price?.toLocaleString()||'--'}</span><span className={'td-change '+(t.change_24h>=0?'up':'down')}>{t.change_24h>=0?'+':''}{t.change_24h?.toFixed(1)}%</span><div className="td-gauge"><div className="td-spark-bar" style={{width:Math.abs(t.change_24h||0)*3+'px',background:t.change_24h>=0?'#22c55e':'#ef4444'}}/></div></div>)}
        </div>
      </div>
      <div className="td-col-2">
        <div className="td-card"><div className="td-card-header"><Activity size={14}/> Top Signals</div>
          {signals.slice(0,5).map((s:any,i:number)=><div key={i} className="td-signal-card"><div className="td-signal-top"><span className="td-signal-rank">#{i+1}</span><span className="td-signal-symbol">{s.symbol}</span><span className="td-signal-tag">{s.source}</span></div><div className="td-signal-bottom"><span className="td-signal-type">{s.type}</span><div className="td-conv-bar"><div className="td-conv-bar-bg" style={{width:(s.conviction||0.5)*100+'%',background:s.direction==='BUY'?'#22c55e':'#ef4444'}}/></div><span className="td-conv-value">{((s.conviction||0.5)*100).toFixed(0)}%</span></div></div>)}
        </div>
      </div>
    </div>
    <div className="td-row">
      <div className="td-col-3"><div className="td-card"><div className="td-card-header"><AlertTriangle size={14}/> Alerts</div><div className="td-alert-list">{market.filter((t:any)=>Math.abs(t.change_24h||0)>5).slice(0,3).map((t:any)=><div key={t.symbol} className="td-alert-row"><span className="td-alert-icon">{t.change_24h>=0?<TrendingUp size={12} color="#22c55e"/>:<TrendingDown size={12} color="#ef4444"/>}</span><span className="td-alert-symbol">{t.symbol}</span><span className="td-alert-detail">{t.change_24h>=0?'+':''}{t.change_24h}%</span></div>)}</div></div></div>
    </div>
  </div>
}
