"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { AdminNav } from "@/components/AdminNav";
import { api, formatInr, isAuthed, clearToken } from "@/lib/api";

type ProfitsData = Awaited<ReturnType<typeof api.adminClientProfits>>;
type TimeframeOption = "today" | "7d" | "1m" | "3m" | "1y" | "all" | "custom";

export default function ClientProfitsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<ProfitsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<TimeframeOption>("all");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [expandedDates, setExpandedDates] = useState<Record<string, boolean>>({});

  async function load(selectedTimeframe = timeframe, fDate = fromDate, tDate = toDate) {
    try {
      setLoading(true);
      setError(null);
      const res = await api.adminClientProfits(id, selectedTimeframe, fDate || undefined, tDate || undefined);
      setData(res);
      if (res.daily_breakdown && res.daily_breakdown.length > 0) {
        const initialExpanded: Record<string, boolean> = {};
        res.daily_breakdown.slice(0, 5).forEach((d) => {
          initialExpanded[d.date] = true;
        });
        setExpandedDates((prev) => ({ ...initialExpanded, ...prev }));
      }
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
    load(timeframe, fromDate, toDate);
  }, [id, timeframe]);

  function handleTimeframeChange(tf: TimeframeOption) {
    setTimeframe(tf);
    if (tf !== "custom") {
      load(tf, "", "");
    }
  }

  function handleApplyCustomRange() {
    if (fromDate && toDate) {
      load("custom", fromDate, toDate);
    }
  }

  function toggleDateExpand(dateKey: string) {
    setExpandedDates((prev) => ({
      ...prev,
      [dateKey]: !prev[dateKey],
    }));
  }

  function formatPhone(phone: string | null) {
    if (!phone) return "—";
    if (phone.length === 10) return `+91 ${phone.slice(0, 5)} ${phone.slice(5)}`;
    return phone;
  }

  return (
    <main className="min-h-screen pb-16 bg-[#0B0F19] text-slate-100">
      <AdminNav />

      <div className="container-x py-8">
        {/* Header Navigation */}
        <div className="mb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1.5">
              <Link href="/admin/ledger" className="text-xs text-gold-400 hover:text-gold-300 flex items-center gap-1 font-medium transition">
                <span>← Back to Ledger Directory</span>
              </Link>
            </div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2.5">
              <span className="text-gold-400">📊</span> Client Profit Ledger &amp; Date-Wise Audit
            </h1>
            {data?.client && (
              <p className="mt-1 text-sm text-slate-400">
                Live Trade Book &amp; PnL for <span className="text-white font-semibold">{data.client.name}</span> ({data.client.email}) · Tradejini ID: <span className="text-gold-400 font-mono font-bold">{data.client.client_id || "N/A"}</span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              className="btn-ghost text-xs text-slate-300 hover:text-white bg-slate-800/80 px-3.5 py-1.5 rounded-lg border border-slate-700/60 shadow-sm"
              onClick={() => load()}
            >
              🔄 Refresh Live Data
            </button>
            <button
              className="text-xs text-slate-400 hover:text-white px-3 py-1.5 transition"
              onClick={() => { clearToken(); window.location.href = "/"; }}
            >
              Log out
            </button>
          </div>
        </div>

        {/* Timeframe Filter Tabs */}
        <div className="card p-4 mb-6 border-slate-800/80 bg-slate-900/60 backdrop-blur-md">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 mr-2">Timeframe:</span>
              {(
                [
                  { id: "today", label: "Today (Intraday)" },
                  { id: "7d", label: "Last 7 Days" },
                  { id: "1m", label: "1 Month" },
                  { id: "3m", label: "3 Months" },
                  { id: "1y", label: "1 Year" },
                  { id: "all", label: "All Time" },
                  { id: "custom", label: "Custom Range" },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => handleTimeframeChange(tab.id)}
                  className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    timeframe === tab.id
                      ? "bg-gold-500 text-slate-950 font-bold shadow-md shadow-gold-500/20"
                      : "bg-slate-800/80 text-slate-300 hover:bg-slate-700/80 hover:text-white border border-slate-700/50"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {timeframe === "custom" && (
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  type="date"
                  value={fromDate}
                  onChange={(e) => setFromDate(e.target.value)}
                  className="bg-slate-950 border border-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-white font-mono focus:border-gold-500 focus:outline-none"
                />
                <span className="text-xs text-slate-500 font-medium">to</span>
                <input
                  type="date"
                  value={toDate}
                  onChange={(e) => setToDate(e.target.value)}
                  className="bg-slate-950 border border-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-white font-mono focus:border-gold-500 focus:outline-none"
                />
                <button
                  onClick={handleApplyCustomRange}
                  disabled={!fromDate || !toDate}
                  className="px-3 py-1.5 bg-gold-500 hover:bg-gold-400 disabled:opacity-50 text-slate-950 text-xs font-bold rounded-lg transition"
                >
                  Apply
                </button>
              </div>
            )}
          </div>
        </div>

        {loading ? (
          <div className="card p-16 text-center text-sm text-slate-400 border-slate-800/80 bg-slate-900/40">
            <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-gold-400 border-t-transparent mb-3"></div>
            <p>Fetching Tradejini live trade book, positions, and date-wise ledger…</p>
          </div>
        ) : error ? (
          <div className="card p-8 border-rose-500/30 bg-rose-500/10 text-rose-300 text-center">
            <p className="font-semibold text-rose-400">Error Loading Profits</p>
            <p className="text-xs mt-1">{error}</p>
            <button className="btn-ghost text-xs mt-4 text-white hover:underline" onClick={() => load()}>
              Try Again
            </button>
          </div>
        ) : data ? (
          <>
            {/* Top Summary Metrics */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
              <div className={`card p-5 border ${data.summary.net_pnl_inr >= 0 ? "border-emerald-500/40 bg-emerald-950/20" : "border-rose-500/40 bg-rose-950/20"}`}>
                <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Net Realized PnL</p>
                <p className={`mt-1.5 text-2xl font-bold font-mono ${data.summary.net_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {formatInr(data.summary.net_pnl_inr)}
                </p>
                <p className="text-[11px] text-slate-500 mt-1">
                  Gross: <span className="font-mono text-slate-300">{formatInr(data.summary.gross_pnl_inr)}</span>
                </p>
              </div>

              <div className="card p-5 border-slate-800 bg-slate-900/50">
                <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Executed Trades</p>
                <p className="mt-1.5 text-2xl font-bold text-white font-mono">{data.summary.total_trades}</p>
                <p className="text-[11px] text-slate-500 mt-1">
                  <span className="text-emerald-400 font-semibold">{data.summary.wins}W</span> · <span className="text-rose-400 font-semibold">{data.summary.losses}L</span> ({data.summary.win_rate_pct}% Win Rate)
                </p>
              </div>

              <div className="card p-5 border-slate-800 bg-slate-900/50">
                <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Brokerage &amp; Taxes</p>
                <p className="mt-1.5 text-2xl font-bold text-gold-400 font-mono">{formatInr(data.summary.total_fees_inr)}</p>
                <p className="text-[11px] text-slate-500 mt-1">Estimated exchange &amp; broker fees</p>
              </div>

              <div className="card p-5 border-slate-800 bg-slate-900/50">
                <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Tradejini Live Status</p>
                <div className="mt-1.5 flex items-center justify-between">
                  <span className="text-lg font-bold">
                    {data.tradejini_connected ? (
                      <span className="text-emerald-400 flex items-center gap-1.5">
                        <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse"></span> Connected
                      </span>
                    ) : (
                      <span className="text-slate-500 font-normal">Offline / Disconnected</span>
                    )}
                  </span>
                </div>
                <p className="text-[11px] text-slate-500 mt-1">
                  Today Intraday PnL: <span className={`font-mono font-semibold ${data.tradejini_today_realized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{formatInr(data.tradejini_today_realized_pnl)}</span>
                </p>
              </div>
            </div>

            {/* Client Identity Information Card */}
            <div className="card p-5 mb-6 border-slate-800 bg-slate-900/40">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-xs text-slate-400 block font-medium">Client Name</span>
                  <span className="font-semibold text-white mt-0.5 block">{data.client.name}</span>
                </div>
                <div>
                  <span className="text-xs text-slate-400 block font-medium">Email Address</span>
                  <span className="font-mono text-slate-300 mt-0.5 block truncate">{data.client.email}</span>
                </div>
                <div>
                  <span className="text-xs text-slate-400 block font-medium">Phone Number</span>
                  <span className="font-mono text-slate-300 mt-0.5 block">{formatPhone(data.client.phone)}</span>
                </div>
                <div>
                  <span className="text-xs text-slate-400 block font-medium">Tradejini Account ID</span>
                  <span className="font-mono text-gold-400 font-bold mt-0.5 block">{data.client.client_id || "Not Provided"}</span>
                </div>
              </div>
            </div>

            {/* Monthly Summary Cards (if multiple months exist) */}
            {data.monthly_breakdown && data.monthly_breakdown.length > 0 && (
              <div className="card p-5 mb-6 border-slate-800 bg-slate-900/40">
                <h2 className="text-sm font-semibold text-white uppercase tracking-wider mb-3 flex items-center justify-between">
                  <span>📅 Monthly Performance Breakdown</span>
                  <span className="text-xs text-slate-500 font-normal lowercase">{data.monthly_breakdown.length} months active</span>
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {data.monthly_breakdown.map((m) => (
                    <div key={m.month} className="p-3.5 rounded-lg bg-slate-950/60 border border-slate-800/80 flex items-center justify-between">
                      <div>
                        <p className="text-xs font-bold text-white">{m.formatted_month}</p>
                        <p className="text-[11px] text-slate-500 mt-0.5">
                          {m.total_trades} trades · <span className="text-emerald-400">{m.wins}W</span>/<span className="text-rose-400">{m.losses}L</span>
                        </p>
                      </div>
                      <div className="text-right">
                        <p className={`text-sm font-bold font-mono ${m.net_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {formatInr(m.net_pnl_inr)}
                        </p>
                        <p className="text-[10px] text-slate-500">Fees: ₹{m.total_fees_inr}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Date-Wise Breakdown Section */}
            <div className="mb-6 space-y-3">
              <div className="flex items-center justify-between px-1">
                <h2 className="text-sm font-semibold text-white uppercase tracking-wider flex items-center gap-2">
                  <span>🗓️ Date-Wise Daily PnL &amp; Trade Breakdown</span>
                </h2>
                <button
                  onClick={() => {
                    const allExp = Object.keys(expandedDates).length === data.daily_breakdown.length && Object.values(expandedDates).every(Boolean);
                    const updated: Record<string, boolean> = {};
                    data.daily_breakdown.forEach((d) => {
                      updated[d.date] = !allExp;
                    });
                    setExpandedDates(updated);
                  }}
                  className="text-xs text-gold-400 hover:underline"
                >
                  Toggle All Days
                </button>
              </div>

              {data.daily_breakdown && data.daily_breakdown.length > 0 ? (
                data.daily_breakdown.map((day) => {
                  const isExpanded = !!expandedDates[day.date];
                  return (
                    <div key={day.date} className="card border-slate-800/90 bg-slate-900/50 overflow-hidden">
                      {/* Day Header Bar */}
                      <button
                        onClick={() => toggleDateExpand(day.date)}
                        className="w-full p-4 flex items-center justify-between text-left hover:bg-slate-800/40 transition"
                      >
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500 font-mono">{isExpanded ? "▼" : "▶"}</span>
                          <div>
                            <span className="text-sm font-bold text-white mr-2">{day.formatted_date}</span>
                            <span className="text-xs text-slate-400 font-mono">({day.date})</span>
                          </div>
                          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 font-medium">
                            {day.total_trades} {day.total_trades === 1 ? "trade" : "trades"}
                          </span>
                        </div>

                        <div className="flex items-center gap-4">
                          <div className="text-right">
                            <span className="text-xs text-slate-500 block">Gross PnL: <span className="font-mono text-slate-400">{formatInr(day.gross_pnl_inr)}</span></span>
                            <span className="text-xs text-slate-500">Fees: <span className="font-mono text-slate-400">₹{day.total_fees_inr}</span></span>
                          </div>
                          <div className="text-right min-w-[110px]">
                            <span className={`text-base font-bold font-mono block ${day.net_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                              {formatInr(day.net_pnl_inr)}
                            </span>
                            <span className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Net PnL</span>
                          </div>
                        </div>
                      </button>

                      {/* Day Trades Table */}
                      {isExpanded && (
                        <div className="border-t border-slate-800 bg-slate-950/40 p-4">
                          <div className="overflow-x-auto">
                            <table className="w-full text-xs whitespace-nowrap">
                              <thead className="text-left uppercase tracking-wider text-slate-400 border-b border-slate-800">
                                <tr>
                                  <th className="pb-2.5">Time (IST)</th>
                                  <th className="pb-2.5">Symbol</th>
                                  <th className="pb-2.5">Side</th>
                                  <th className="pb-2.5 text-right">Quantity</th>
                                  <th className="pb-2.5 text-right">Entry Price</th>
                                  <th className="pb-2.5 text-right">Exit Price</th>
                                  <th className="pb-2.5 text-right">Gross PnL</th>
                                  <th className="pb-2.5 text-right">Fee</th>
                                  <th className="pb-2.5 text-right">Net PnL</th>
                                  <th className="pb-2.5 text-center">Status</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-800/60 text-slate-300 font-mono">
                                {day.trades.map((t) => (
                                  <tr key={t.id} className="hover:bg-slate-900/60">
                                    <td className="py-2.5 text-slate-400">{t.time_ist || "—"}</td>
                                    <td className="font-sans font-bold text-white">{t.symbol}</td>
                                    <td>
                                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${t.side === "buy" ? "bg-emerald-500/20 text-emerald-300" : "bg-rose-500/20 text-rose-300"}`}>
                                        {t.side}
                                      </span>
                                    </td>
                                    <td className="text-right">{t.size}</td>
                                    <td className="text-right">₹{t.entry_price.toLocaleString()}</td>
                                    <td className="text-right">{t.exit_price ? `₹${t.exit_price.toLocaleString()}` : "—"}</td>
                                    <td className={`text-right font-bold ${t.realized_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                      {formatInr(t.realized_pnl_inr)}
                                    </td>
                                    <td className="text-right text-slate-500">₹{t.fee_inr}</td>
                                    <td className={`text-right font-bold ${t.net_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                      {formatInr(t.net_pnl_inr)}
                                    </td>
                                    <td className="text-center font-sans">
                                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-300 capitalize">{t.status}</span>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              ) : (
                <div className="card p-10 text-center text-sm text-slate-400 border-slate-800">
                  No executed trade records found for the selected timeframe.
                </div>
              )}
            </div>
          </>
        ) : null}
      </div>
    </main>
  );
}
