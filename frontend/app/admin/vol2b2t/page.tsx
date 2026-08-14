"use client";

import { useEffect, useState } from "react";
import { Logo } from "@/components/nav";
import { AdminNav } from "@/components/AdminNav";
import { isAuthed } from "@/lib/api";

type ScreenerInfo = {
  coin: string;
  avg_vol: number;
  curr_vol: number;
  mult: number;
  status: string;
  threshold: number;
};

type TradeEntry = {
  id: string;
  coin: string;
  entry_time: string;
  exit_time?: string;
  side: string;
  entry_price: number;
  exit_price?: number;
  qty: number;
  status: string;
  pnl_str?: string;
  extra_entry?: string;
  extra_exit?: string;
};

export default function Vol2b2tDashboard() {

  const [trades, setTrades] = useState<TradeEntry[]>([]);
  const [screener, setScreener] = useState<ScreenerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [visibleCount, setVisibleCount] = useState(12);

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }

    const fetchData = () => {
      fetch("/api/vol2b2t/trades")
        .then((res) => res.json())
        .then((data) => {
          setTrades(Array.isArray(data) ? data : []);
        })
        .catch((err) => console.error(err));
        
      fetch("/api/vol2b2t/screener")
        .then((res) => res.json())
        .then((data) => {
          setScreener(Array.isArray(data) ? data : []);
          setLoading(false);
        })
        .catch((err) => {
          console.error(err);
          setLoading(false);
        });
    };

    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, []);

  const visibleTrades = trades.slice(0, visibleCount);

  return (
    <main className="flex min-h-screen flex-col bg-ink-950 text-white">
      <AdminNav />

      <div className="flex-1 p-6 overflow-y-auto">
        <div className="mx-auto max-w-7xl">
          <div className="mb-6 flex items-center justify-between">
            <h1 className="text-2xl font-bold">Live Market Screener</h1>
            <span className="pill text-sm bg-blue-500/20 text-blue-400">Vol2b2t Monitor</span>
          </div>
          
          <div className="mb-10 overflow-hidden rounded-xl border border-ink-800 bg-ink-900/40">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead className="bg-black/20 text-slate-400">
                  <tr>
                    <th className="p-4 font-medium">Coin</th>
                    <th className="p-4 font-medium text-right">40 Candle Average</th>
                    <th className="p-4 font-medium text-right">Current Volume</th>
                    <th className="p-4 font-medium text-right">Volume Threshold</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-800/50">
                  {screener.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="p-8 text-center text-slate-500">
                        {loading ? "Loading screener data..." : "No screener data available."}
                      </td>
                    </tr>
                  ) : (
                    screener.map((s) => {
                      const isWatching = s.status.includes("Watching") || s.status.includes("In Position");
                      const multColor = s.mult >= s.threshold ? "text-green-400 font-bold" : "text-white";
                      
                      return (
                        <tr key={s.coin} className={`transition-colors hover:bg-ink-800/30 ${isWatching ? 'bg-blue-900/10' : ''}`}>
                          <td className="p-4 font-bold">
                            {s.coin}
                            {isWatching && <span className="ml-2 inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" title={s.status}></span>}
                          </td>
                          <td className="p-4 text-right font-mono text-slate-400">{s.avg_vol.toLocaleString(undefined, {maximumFractionDigits:0})}</td>
                          <td className="p-4 text-right font-mono">{s.curr_vol.toLocaleString(undefined, {maximumFractionDigits:0})}</td>
                          <td className={`p-4 text-right font-mono ${multColor}`}>
                            {(s.avg_vol * s.threshold).toLocaleString(undefined, {maximumFractionDigits:0})}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="mb-6 flex items-center justify-between">
            <h1 className="text-2xl font-bold">Vol2b2t Trade Executions</h1>
            <span className="text-xs text-muted">Showing {visibleTrades.length} of {trades.length} trades</span>
          </div>

          {loading ? (
            <div className="flex justify-center p-10">
              <span className="text-slate-400">Loading trades...</span>
            </div>
          ) : trades.length === 0 ? (
            <div className="flex justify-center rounded-xl border border-ink-800 bg-ink-900/30 p-10">
              <span className="text-slate-400">No trades recorded yet. Wait for a volume spike!</span>
            </div>
          ) : (
            <>
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-2">
                {visibleTrades.map((trade) => {
                  const isOpen = trade.status === "OPEN";
                  const isWin = trade.pnl_str && trade.pnl_str.includes("+");
                  const isLoss = trade.pnl_str && trade.pnl_str.includes("-");

                  let statusBadge = "bg-yellow-500/20 text-yellow-400 border-yellow-500/30";
                  if (!isOpen) {
                    statusBadge = isWin
                      ? "bg-green-500/20 text-green-400 border-green-500/30"
                      : "bg-red-500/20 text-red-400 border-red-500/30";
                  }

                  const chartUrl = `/api/vol2b2t/chart/${trade.coin}?entry_ts=${trade.entry_time}`;

                  return (
                    <div key={trade.id} className="flex flex-col overflow-hidden rounded-xl border border-ink-800 bg-ink-900/40">
                      {/* Header */}
                      <div className="flex items-center justify-between border-b border-ink-800 bg-black/20 p-4">
                        <div>
                          <div className="text-lg font-bold">{trade.coin}</div>
                          <div className="text-xs text-slate-400">{new Date(trade.entry_time).toLocaleString()}</div>
                        </div>
                        <div className={`rounded-md border px-3 py-1 text-xs font-bold uppercase tracking-wider ${statusBadge}`}>
                          {trade.status}
                        </div>
                      </div>

                      {/* Chart Image */}
                      <div className="relative aspect-video w-full bg-ink-950 border-b border-ink-800">
                        <img
                          src={chartUrl}
                          alt={`Chart for ${trade.coin}`}
                          loading="lazy"
                          className="h-full w-full object-cover opacity-90 hover:opacity-100 transition-opacity"
                          onError={(e) => {
                            e.currentTarget.style.display = 'none';
                            e.currentTarget.parentElement!.innerHTML = '<div class="flex h-full items-center justify-center text-slate-500 text-sm">Chart rendering failed or data expired</div>';
                          }}
                        />
                      </div>

                      {/* Details */}
                      <div className="grid grid-cols-2 gap-4 p-4 text-sm">
                        <div>
                          <div className="text-slate-400 text-xs uppercase">Entry</div>
                          <div className="font-mono">{trade.entry_price.toFixed(4)}</div>
                          <div className="text-xs text-slate-500 mt-1">{trade.extra_entry || "N/A"}</div>
                        </div>
                        <div>
                          <div className="text-slate-400 text-xs uppercase">Exit</div>
                          <div className="font-mono">{trade.exit_price ? trade.exit_price.toFixed(4) : "---"}</div>
                          <div className="text-xs text-slate-500 mt-1">{trade.extra_exit || "---"}</div>
                        </div>
                        <div>
                          <div className="text-slate-400 text-xs uppercase">Size</div>
                          <div className="font-mono">{trade.qty}</div>
                        </div>
                        <div>
                          <div className="text-slate-400 text-xs uppercase">Net PnL</div>
                          <div className={`font-mono font-bold ${isWin ? 'text-green-400' : isLoss ? 'text-red-400' : 'text-white'}`}>
                            {trade.pnl_str || (isOpen ? "OPEN" : "$0.00")}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {visibleCount < trades.length && (
                <div className="mt-8 flex justify-center">
                  <button
                    className="btn-gold !px-6 !py-2 text-sm font-semibold rounded-lg"
                    onClick={() => setVisibleCount((prev) => prev + 12)}
                  >
                    Load More Trades ({trades.length - visibleCount} remaining)
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </main>
  );
}
