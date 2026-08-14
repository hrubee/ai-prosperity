"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { AdminNav } from "@/components/AdminNav";
import { api, formatInr, isAuthed, clearToken } from "@/lib/api";

type ProfitsData = Awaited<ReturnType<typeof api.adminClientProfits>>;

export default function ClientProfitsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<ProfitsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setLoading(true);
      setError(null);
      const res = await api.adminClientProfits(id);
      setData(res);
    } catch (e: any) {
      setError(e.message || "Failed to load client profit ledger");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    load();
  }, [id]);

  function formatPhone(phone: string | null) {
    if (!phone) return "—";
    if (phone.length === 10) return `+91 ${phone.slice(0, 5)} ${phone.slice(5)}`;
    return phone;
  }

  return (
    <main className="min-h-screen">
      <AdminNav />

      <div className="container-x py-8">
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Link href="/admin/ledger" className="text-xs text-gold-400 hover:underline flex items-center gap-1">
                <span>← Back to Ledger Directory</span>
              </Link>
            </div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <span>📊</span> Client Booked PnL &amp; Trade Audit
            </h1>
            {data?.client && (
              <p className="mt-1 text-sm text-muted">
                Showing trade ledger for <span className="text-white font-semibold">{data.client.name}</span> ({data.client.email}) · Tradejini ID: <span className="text-gold-400 font-mono font-semibold">{data.client.client_id || "N/A"}</span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              className="btn-ghost text-xs text-muted hover:text-white"
              onClick={load}
            >
              🔄 Refresh PnL
            </button>
            <button
              className="text-xs text-muted hover:text-white"
              onClick={() => { clearToken(); window.location.href = "/"; }}
            >
              Log out
            </button>
          </div>
        </div>

        {loading ? (
          <div className="card p-12 text-center text-sm text-muted">
            Fetching Tradejini account booked PnL and trade ledger…
          </div>
        ) : error ? (
          <div className="card p-8 border-loss/30 bg-loss/10 text-loss text-center">
            <p className="font-semibold">Error Loading Profits</p>
            <p className="text-xs mt-1">{error}</p>
            <button className="btn-ghost text-xs mt-4 text-white hover:underline" onClick={load}>
              Try Again
            </button>
          </div>
        ) : data ? (
          <>
            {/* Summary Metrics */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
              <div className="card p-5 border-gain/30 bg-gain/5">
                <p className="text-xs uppercase tracking-wider text-muted">Tradejini Booked PnL</p>
                <p className={`mt-1 text-2xl font-bold ${data.booked_pnl_inr >= 0 ? "text-gain" : "text-loss"}`}>
                  {formatInr(data.booked_pnl_inr)}
                </p>
              </div>
              <div className="card p-5">
                <p className="text-xs uppercase tracking-wider text-muted">Total Executed Trades</p>
                <p className="mt-1 text-2xl font-bold text-white">{data.total_trades}</p>
              </div>
              <div className="card p-5">
                <p className="text-xs uppercase tracking-wider text-muted">Brokerage &amp; Fees</p>
                <p className="mt-1 text-2xl font-bold text-gold-400">{formatInr(data.total_fees_inr)}</p>
              </div>
              <div className="card p-5">
                <p className="text-xs uppercase tracking-wider text-muted">Broker Connection</p>
                <p className="mt-1 text-2xl font-bold">
                  {data.tradejini_connected ? (
                    <span className="text-gain flex items-center gap-1.5 text-lg">
                      <span className="h-2 w-2 rounded-full bg-gain"></span> Connected
                    </span>
                  ) : (
                    <span className="text-muted text-lg font-normal">Offline / Archived</span>
                  )}
                </p>
              </div>
            </div>

            {/* Client Identity Header Card */}
            <div className="card p-5 mb-6">
              <h2 className="text-xs uppercase tracking-wider text-muted mb-3 font-semibold">Client Details</h2>
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-xs text-muted block">Client Name</span>
                  <span className="font-semibold text-white">{data.client.name}</span>
                </div>
                <div>
                  <span className="text-xs text-muted block">Email Address</span>
                  <span className="font-mono text-slate-300">{data.client.email}</span>
                </div>
                <div>
                  <span className="text-xs text-muted block">Phone Number</span>
                  <span className="font-mono text-slate-300">{formatPhone(data.client.phone)}</span>
                </div>
                <div>
                  <span className="text-xs text-muted block">Tradejini Client ID</span>
                  <span className="font-mono text-gold-400 font-bold">{data.client.client_id || "Not Provided"}</span>
                </div>
              </div>
            </div>

            {/* Permanent DB Audit Execution Log */}
            <div className="card p-5 mb-6">
              <h2 className="text-base font-semibold text-white mb-4 flex items-center justify-between">
                <span>📋 Permanent Trade Execution History Log</span>
                <span className="text-xs font-normal text-muted">{data.entries.length} records</span>
              </h2>

              <div className="overflow-x-auto">
                <table className="w-full text-sm whitespace-nowrap">
                  <thead className="text-left text-xs uppercase tracking-wider text-muted border-b border-ink-800">
                    <tr>
                      <th className="pb-3">Executed At</th>
                      <th className="pb-3">Symbol</th>
                      <th className="pb-3">Side</th>
                      <th className="pb-3 text-right">Size</th>
                      <th className="pb-3 text-right">Entry Price</th>
                      <th className="pb-3 text-right">Exit Price</th>
                      <th className="pb-3 text-right">Realized PnL (INR)</th>
                      <th className="pb-3 text-right">Fee (INR)</th>
                      <th className="pb-3 text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-300">
                    {data.entries.map((e) => (
                      <tr key={e.id} className="border-t border-ink-800/60 hover:bg-ink-800/30">
                        <td className="py-3 text-xs text-muted font-mono">
                          {e.executed_at ? new Date(e.executed_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                        </td>
                        <td className="font-semibold text-white">{e.symbol}</td>
                        <td>
                          <span className={`pill uppercase font-semibold ${e.side === "buy" ? "bg-gain/20 text-gain" : "bg-loss/20 text-loss"}`}>
                            {e.side}
                          </span>
                        </td>
                        <td className="text-right font-mono">{e.size}</td>
                        <td className="text-right font-mono">₹{e.entry_price.toLocaleString()}</td>
                        <td className="text-right font-mono">{e.exit_price ? `₹${e.exit_price.toLocaleString()}` : "—"}</td>
                        <td className={`text-right font-mono font-bold ${e.realized_pnl_inr >= 0 ? "text-gain" : "text-loss"}`}>
                          {formatInr(e.realized_pnl_inr)}
                        </td>
                        <td className="text-right font-mono text-xs text-muted">₹{e.fee_inr}</td>
                        <td className="text-center">
                          <span className="pill bg-gain/10 text-gain capitalize">{e.status}</span>
                        </td>
                      </tr>
                    ))}
                    {data.entries.length === 0 && (
                      <tr>
                        <td colSpan={9} className="py-10 text-center text-sm text-muted">
                          No trade execution records found for this client. Trades automatically log here when order fills occur.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Live Tradejini Positions (if connected) */}
            {data.tradejini_connected && data.tradejini_positions.length > 0 && (
              <div className="card p-5 border-gold-500/30">
                <h2 className="text-base font-semibold text-gold-400 mb-4">
                  ⚡ Live Tradejini Open Positions
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm whitespace-nowrap">
                    <thead className="text-left text-xs uppercase tracking-wider text-muted border-b border-ink-800">
                      <tr>
                        <th className="pb-3">Symbol</th>
                        <th className="pb-3">Product</th>
                        <th className="pb-3 text-right">Net Qty</th>
                        <th className="pb-3 text-right">Buy Price</th>
                        <th className="pb-3 text-right">Sell Price</th>
                        <th className="pb-3 text-right">Realized PnL</th>
                      </tr>
                    </thead>
                    <tbody className="text-slate-300">
                      {data.tradejini_positions.map((p, idx) => (
                        <tr key={idx} className="border-t border-ink-800/60">
                          <td className="py-3 font-semibold text-white">{p.tsym || p.sym_id}</td>
                          <td className="text-xs uppercase text-muted">{p.prd}</td>
                          <td className="text-right font-mono">{p.net_qty}</td>
                          <td className="text-right font-mono">₹{p.buy_avg_px || 0}</td>
                          <td className="text-right font-mono">₹{p.sell_avg_px || 0}</td>
                          <td className={`text-right font-mono font-bold ${p.rpnl >= 0 ? "text-gain" : "text-loss"}`}>
                            {formatInr(p.rpnl || 0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        ) : null}
      </div>
    </main>
  );
}
