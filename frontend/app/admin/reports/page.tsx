"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AdminNav } from "@/components/AdminNav";
import { api, formatInr, isAuthed } from "@/lib/api";

type ReportData = Awaited<ReturnType<typeof api.adminProfitReports>>;

export default function AdminReportsPage() {
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedMonth, setSelectedMonth] = useState<string>("2026-08");
  const [fromDate, setFromDate] = useState<string>("");
  const [toDate, setToDate] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [expandedClientId, setExpandedClientId] = useState<string | null>(null);

  // Generate list of past 12 months for selector
  const months = [
    { value: "2026-08", label: "August 2026" },
    { value: "2026-07", label: "July 2026" },
    { value: "2026-06", label: "June 2026" },
    { value: "2026-05", label: "May 2026" },
    { value: "2026-04", label: "April 2026" },
    { value: "2026-03", label: "March 2026" },
    { value: "2026-02", label: "February 2026" },
    { value: "2026-01", label: "January 2026" },
  ];

  async function loadReport(params?: { month?: string; from_date?: string; to_date?: string }) {
    setLoading(true);
    try {
      const res = await api.adminProfitReports({
        month: params?.month ?? (fromDate || toDate ? undefined : selectedMonth),
        from_date: params?.from_date ?? (fromDate || undefined),
        to_date: params?.to_date ?? (toDate || undefined),
      });
      setData(res);
    } catch (e: any) {
      console.error("Failed to load profit report:", e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    loadReport();
  }, []);

  function handleMonthChange(m: string) {
    setSelectedMonth(m);
    setFromDate("");
    setToDate("");
    loadReport({ month: m });
  }

  function handleCustomDateApply() {
    if (!fromDate && !toDate) return;
    loadReport({ from_date: fromDate, to_date: toDate });
  }

  function handleDownloadCSV() {
    if (!data || !data.clients) return;

    const headers = [
      "Client Name",
      "Phone Number",
      "Tradejini Client ID",
      "Email Address",
      "Report Period",
      "Total Trades",
      "Winning Trades",
      "Losing Trades",
      "Win Rate %",
      "Gross Profit (INR)",
      "Brokerage & Fees (INR)",
      "Net Profit (INR)",
      "net 40%",
    ];

    const rows = data.clients.map((c) => {
      const net40 = c.net_profit_inr * 0.4;
      return [
        `"${(c.name || "Client").replace(/"/g, '""')}"`,
        `"${c.phone || "—"}"`,
        `"${c.client_id || "—"}"`,
        `"${c.email || "—"}"`,
        `"${data.date_range}"`,
        c.total_trades,
        c.winning_trades,
        c.losing_trades,
        `${c.win_rate_pct}%`,
        c.gross_profit_inr.toFixed(2),
        c.fee_inr.toFixed(2),
        c.net_profit_inr.toFixed(2),
        net40.toFixed(2),
      ];
    });

    // Summary row
    const totalNet40 = data.summary.total_net_profit_inr * 0.4;
    rows.push([
      `"TOTAL (All Clients)"`,
      `""`,
      `""`,
      `""`,
      `"${data.date_range}"`,
      data.summary.total_trades,
      "",
      "",
      "",
      data.summary.total_gross_profit_inr.toFixed(2),
      data.summary.total_fees_inr.toFixed(2),
      data.summary.total_net_profit_inr.toFixed(2),
      totalNet40.toFixed(2),
    ]);

    const csvContent = [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const safePeriod = data.date_range.replace(/[^a-zA-Z0-9_-]/g, "_");
    link.setAttribute("href", url);
    link.setAttribute("download", `client_profits_report_${safePeriod}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  const filteredClients = (data?.clients || []).filter((c) => {
    const q = searchQuery.toLowerCase();
    return (
      c.name?.toLowerCase().includes(q) ||
      c.email?.toLowerCase().includes(q) ||
      c.client_id?.toLowerCase().includes(q) ||
      c.phone?.includes(q)
    );
  });

  return (
    <main className="min-h-screen pb-16 bg-[#0B0F19] text-slate-100">
      <AdminNav />

      <div className="container-x py-8">
        {/* Header Title & Export Button */}
        <div className="mb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-sky-400">
              <span>Financial Audit &amp; Performance</span>
            </div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2 mt-1">
              <span>📑</span> Monthly Client Profits &amp; Revenue Reporting
            </h1>
            <p className="mt-1 text-xs text-slate-400">
              Audit, reconcile, and export permanent monthly profit and loss records for all client accounts.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleDownloadCSV}
              disabled={loading || !data || data.clients.length === 0}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-bold flex items-center gap-2 shadow-lg shadow-emerald-950/40 transition active:scale-95"
            >
              <span>📥</span> Download CSV Report
            </button>
          </div>
        </div>

        {/* Filter Controls Bar */}
        <div className="card p-4 border-slate-800 bg-slate-900/60 mb-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          {/* Month Preset Selector */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider mr-1">Select Month:</span>
            {months.slice(0, 4).map((m) => (
              <button
                key={m.value}
                onClick={() => handleMonthChange(m.value)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  selectedMonth === m.value && !fromDate && !toDate
                    ? "bg-sky-500 text-white shadow-md shadow-sky-500/20"
                    : "bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700"
                }`}
              >
                {m.label}
              </button>
            ))}

            <select
              value={selectedMonth}
              onChange={(e) => handleMonthChange(e.target.value)}
              className="bg-slate-800 border border-slate-700 text-xs px-3 py-1.5 rounded-lg text-slate-200 outline-none"
            >
              {months.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          {/* Custom Date Range Filter */}
          <div className="flex flex-wrap items-center gap-2 border-t lg:border-t-0 border-slate-800 pt-3 lg:pt-0">
            <span className="text-xs text-slate-400 font-medium">Or Custom Range:</span>
            <input
              type="date"
              value={fromDate}
              onChange={(e) => {
                setFromDate(e.target.value);
                setSelectedMonth("");
              }}
              className="bg-slate-950 border border-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-white font-mono outline-none"
            />
            <span className="text-slate-500 text-xs">to</span>
            <input
              type="date"
              value={toDate}
              onChange={(e) => {
                setToDate(e.target.value);
                setSelectedMonth("");
              }}
              className="bg-slate-950 border border-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-white font-mono outline-none"
            />
            <button
              onClick={handleCustomDateApply}
              className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white border border-slate-700"
            >
              Filter 📅
            </button>
          </div>
        </div>

        {/* Aggregate KPI Summary Cards */}
        {data && (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-6">
            <div className="card p-5 border-slate-800 bg-slate-900/50">
              <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Report Period</p>
              <p className="mt-1.5 text-base font-bold text-sky-400 font-mono truncate">{data.date_range}</p>
              <p className="text-[11px] text-slate-500 mt-1">
                {data.summary.active_trading_clients} of {data.summary.total_clients} clients traded
              </p>
            </div>

            <div className="card p-5 border-slate-800 bg-slate-900/50">
              <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Executed Trades</p>
              <p className="mt-1.5 text-2xl font-bold text-white font-mono">{data.summary.total_trades}</p>
              <p className="text-[11px] text-slate-500 mt-1">Permanent audit entries</p>
            </div>

            <div className="card p-5 border-slate-800 bg-slate-900/50">
              <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Brokerage &amp; Fees</p>
              <p className="mt-1.5 text-2xl font-bold text-amber-400 font-mono">{formatInr(data.summary.total_fees_inr)}</p>
              <p className="text-[11px] text-slate-500 mt-1">STT &amp; Exchange charges</p>
            </div>

            <div
              className={`card p-5 border ${
                data.summary.total_net_profit_inr >= 0
                  ? "border-emerald-500/40 bg-emerald-950/20"
                  : "border-rose-500/40 bg-rose-950/20"
              }`}
            >
              <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Client Net Profit</p>
              <p
                className={`mt-1.5 text-2xl font-bold font-mono ${
                  data.summary.total_net_profit_inr >= 0 ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {formatInr(data.summary.total_net_profit_inr)}
              </p>
              <p className="text-[11px] text-slate-400 mt-1">Gross: {formatInr(data.summary.total_gross_profit_inr)}</p>
            </div>

            <div className="card p-5 border border-gold-500/40 bg-gold-950/20">
              <p className="text-xs uppercase tracking-wider text-gold-400 font-medium">Total Net 40% Share</p>
              <p className="mt-1.5 text-2xl font-bold font-mono text-gold-400">
                {formatInr(data.summary.total_net_profit_inr * 0.4)}
              </p>
              <p className="text-[11px] text-gold-300/80 mt-1">40% Performance Share</p>
            </div>
          </div>
        )}

        {/* Client Profit Table */}
        <div className="card p-5 border-slate-800 bg-slate-900/50">
          <div className="mb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <h2 className="text-base font-bold text-white uppercase tracking-wider">
                Client Monthly Profits ({filteredClients.length})
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Breakdown of net profits, 40% share, trades, and fees for <b className="text-white">{data?.date_range}</b>.
              </p>
            </div>

            <div className="flex items-center gap-3">
              <input
                className="bg-slate-950 border border-slate-700 text-xs px-3 py-2 rounded-lg text-white font-mono w-full sm:w-72 focus:border-sky-500 focus:outline-none"
                placeholder="Search client, phone, client ID…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          </div>

          {loading ? (
            <div className="py-16 text-center text-sm text-slate-400">
              <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-sky-400 border-t-transparent mb-3"></div>
              <p>Calculating permanent client profit ledger report…</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs whitespace-nowrap">
                <thead className="text-left uppercase tracking-wider text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="pb-3">Client Name</th>
                    <th className="pb-3">Phone</th>
                    <th className="pb-3">Tradejini Client ID</th>
                    <th className="pb-3">Report Period</th>
                    <th className="pb-3 text-center">Trades</th>
                    <th className="pb-3 text-center">Win Rate</th>
                    <th className="pb-3 text-right">Gross Profit</th>
                    <th className="pb-3 text-right">Fees &amp; Taxes</th>
                    <th className="pb-3 text-right font-bold text-emerald-400">Net Profit (INR)</th>
                    <th className="pb-3 text-right font-bold text-gold-400">Net 40% (INR)</th>
                    <th className="pb-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {filteredClients.map((c) => {
                    const isExpanded = expandedClientId === c.user_id;
                    const net40 = c.net_profit_inr * 0.4;

                    return (
                      <>
                        <tr key={c.user_id} className="hover:bg-slate-800/30 transition-colors">
                          <td className="py-3.5 font-medium text-white">
                            <div className="font-semibold text-sm text-white">{c.name}</div>
                            <div className="text-xs text-slate-400 font-mono">{c.email}</div>
                          </td>
                          <td className="font-mono text-slate-300">
                            {c.phone && c.phone !== "—"
                              ? c.phone.length === 10
                                ? `+91 ${c.phone.slice(0, 5)} ${c.phone.slice(5)}`
                                : c.phone
                              : "—"}
                          </td>
                          <td className="font-mono text-gold-400 font-bold text-sm">{c.client_id}</td>
                          <td className="text-slate-400 font-mono">{c.date_range}</td>
                          <td className="text-center font-mono font-semibold">{c.total_trades}</td>
                          <td className="text-center font-mono">
                            {c.total_trades > 0 ? (
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  c.win_rate_pct >= 50
                                    ? "bg-emerald-500/20 text-emerald-400"
                                    : "bg-amber-500/20 text-amber-400"
                                }`}
                              >
                                {c.win_rate_pct}%
                              </span>
                            ) : (
                              <span className="text-slate-500">—</span>
                            )}
                          </td>
                          <td
                            className={`text-right font-mono font-semibold ${
                              c.gross_profit_inr >= 0 ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {formatInr(c.gross_profit_inr)}
                          </td>
                          <td className="text-right font-mono text-amber-400/90">{formatInr(c.fee_inr)}</td>
                          <td
                            className={`text-right font-mono font-bold text-sm ${
                              c.net_profit_inr >= 0 ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {formatInr(c.net_profit_inr)}
                          </td>
                          <td
                            className={`text-right font-mono font-bold text-sm ${
                              net40 >= 0 ? "text-gold-400" : "text-rose-400"
                            }`}
                          >
                            {formatInr(net40)}
                          </td>
                          <td className="text-right">
                            <div className="flex items-center justify-end gap-2">
                              {c.trades.length > 0 && (
                                <button
                                  onClick={() => setExpandedClientId(isExpanded ? null : c.user_id)}
                                  className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs border border-slate-700 font-medium"
                                >
                                  {isExpanded ? "Hide Trades ▲" : `Trades (${c.trades.length}) ▼`}
                                </button>
                              )}
                              <Link
                                href={`/admin/ledger/${c.client_id || c.user_id}/profits`}
                                className="px-2.5 py-1 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 text-xs font-semibold border border-sky-500/30 transition"
                              >
                                Audit 📈
                              </Link>
                            </div>
                          </td>
                        </tr>

                        {/* Expandable Trade Execution Details */}
                        {isExpanded && c.trades.length > 0 && (
                          <tr className="bg-slate-950/80">
                            <td colSpan={11} className="p-4 border-y border-slate-800">
                              <div className="card p-4 border-slate-800 bg-slate-900/90">
                                <h4 className="text-xs font-bold text-white uppercase tracking-wider mb-3">
                                  Executed Trades in {data?.date_range} ({c.trades.length} fills)
                                </h4>
                                <table className="w-full text-xs whitespace-nowrap">
                                  <thead className="text-left text-[11px] uppercase tracking-wider text-slate-400 border-b border-slate-800">
                                    <tr>
                                      <th className="pb-2">Execution Time</th>
                                      <th className="pb-2">Contract / Symbol</th>
                                      <th className="pb-2">Side</th>
                                      <th className="pb-2 text-right">Size</th>
                                      <th className="pb-2 text-right">Entry Px</th>
                                      <th className="pb-2 text-right">Exit Px</th>
                                      <th className="pb-2 text-right">Gross PnL</th>
                                      <th className="pb-2 text-right">Net PnL</th>
                                      <th className="pb-2 text-right text-gold-400">40% Share</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-slate-800/40 text-slate-300 font-mono">
                                    {c.trades.map((t) => {
                                      const tradeNet40 = t.net_pnl_inr * 0.4;
                                      return (
                                        <tr key={t.id} className="hover:bg-slate-800/40">
                                          <td className="py-2 text-slate-400">
                                            {t.executed_at
                                              ? new Date(t.executed_at).toLocaleString([], {
                                                  month: "short",
                                                  day: "numeric",
                                                  hour: "2-digit",
                                                  minute: "2-digit",
                                                })
                                              : "—"}
                                          </td>
                                          <td className="font-bold text-white font-sans">{t.symbol}</td>
                                          <td className="uppercase text-slate-300">{t.side}</td>
                                          <td className="text-right">{t.size}</td>
                                          <td className="text-right">₹{t.entry_price.toFixed(2)}</td>
                                          <td className="text-right">
                                            {t.exit_price != null ? `₹${t.exit_price.toFixed(2)}` : "—"}
                                          </td>
                                          <td
                                            className={`text-right font-bold ${
                                              t.realized_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"
                                            }`}
                                          >
                                            {formatInr(t.realized_pnl_inr)}
                                          </td>
                                          <td
                                            className={`text-right font-bold ${
                                              t.net_pnl_inr >= 0 ? "text-emerald-400" : "text-rose-400"
                                            }`}
                                          >
                                            {formatInr(t.net_pnl_inr)}
                                          </td>
                                          <td
                                            className={`text-right font-bold ${
                                              tradeNet40 >= 0 ? "text-gold-400" : "text-rose-400"
                                            }`}
                                          >
                                            {formatInr(tradeNet40)}
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}

                  {filteredClients.length === 0 && (
                    <tr>
                      <td colSpan={11} className="py-12 text-center text-sm text-slate-500">
                        No clients found for the selected period or search query.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
