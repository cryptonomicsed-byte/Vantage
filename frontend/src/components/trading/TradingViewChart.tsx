import React, { useState } from "react";

const SYMBOLS = [
  { id: "BINANCE:BTCUSDT", label: "BTC/USDT" },
  { id: "BINANCE:ETHUSDT", label: "ETH/USDT" },
  { id: "BINANCE:SOLUSDT", label: "SOL/USDT" },
  { id: "BINANCE:BNBUSDT", label: "BNB/USDT" },
  { id: "BINANCE:AVAXUSDT", label: "AVAX/USDT" },
  { id: "BINANCE:DOTUSDT", label: "DOT/USDT" },
  { id: "BINANCE:LINKUSDT", label: "LINK/USDT" },
  { id: "BINANCE:ADAUSDT", label: "ADA/USDT" },
];

export default function TradingViewChart() {
  const [symbol, setSymbol] = useState("BINANCE:BTCUSDT");
  const [interval, setInterval] = useState("60");

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <h1 className="page-title" style={{ margin: 0 }}>Chart</h1>
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
          style={{ background: "rgba(12,12,22,0.9)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6, padding: "6px 12px", fontSize: 13 }}>
          {SYMBOLS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        <select value={interval} onChange={(e) => setInterval(e.target.value)}
          style={{ background: "rgba(12,12,22,0.9)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6, padding: "6px 12px", fontSize: 13 }}>
          <option value="1">1m</option><option value="5">5m</option><option value="15">15m</option>
          <option value="60">1H</option><option value="240">4H</option><option value="D">1D</option><option value="W">1W</option>
        </select>
      </div>
      <div style={{ height: 500, borderRadius: 12, overflow: "hidden", border: "1px solid var(--border)" }}>
        <iframe title="TradingView" width="100%" height="100%" style={{ border: "none" }}
          src={`https://s.tradingview.com/widgetembed/?frameElementId=tv_chart&symbol=${symbol}&interval=${interval}&theme=dark&style=1&locale=en&toolbarbg=0f0f1a&studies=RSI%40tv-basicstudies%2CMACD%40tv-basicstudies%2CBollinger%40tv-basicstudies`} />
      </div>
    </div>
  );
}
