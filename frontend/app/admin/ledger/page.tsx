"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AdminNav } from "@/components/AdminNav";
import { api, formatInr, isAuthed, clearToken } from "@/lib/api";

type ClientItem = Awaited<ReturnType<typeof api.adminLedgerClients>>["clients"][number];

export default function LedgerClientsPage() {
  const [clients, setClients] = useState<ClientItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [source, setSource] = useState<"copier" | "all">("copier");

  async function load(currentSource: "copier" | "all" = source) {
    try {
      setLoading(true);
      const res = await api.adminLedgerClients(currentSource);
      setClients(res.clients);
    } catch (e: any) {
      console.error("Failed to load ledger clients:", e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    load(source);
  }, [source]);

  function formatPhone(phone: string | null) {
    if (!phone) return "—";
    if (phone.length === 10) return `+91 ${phone.slice(0, 5)} ${phone.slice(5)}`;
    return phone;
  }

  const filteredClients = clients.filter((c) => {
    const q = searchQuery.toLowerCase();
    return (
      c.name?.toLowerCase().includes(q) ||
      c.email?.toLowerCase().includes(q) ||
      c.client_id?.toLowerCase().includes(q) ||
      c.phone?.includes(q)
    );
  });

  const totalBookedPnl = clients.reduce((acc, c) => acc + (c.net_pnl_inr ?? c.booked_pnl_inr), 0);
  const totalGrossPnl = clients.reduce((acc, c) => acc + c.booked_pnl_inr, 0);
  const totalFees = clients.reduce((acc, c) => acc + (c.total_fees_inr || 0), 0);
  const totalTrades = clients.reduce((acc, c) => acc + c.total_trades, 0);

  return (
    <main className="min-h-screen pb-16 bg-[#0B0F19] text-slate-100">
      <AdminNav />

      <div className="container-x py-8">
        {/* Header Navigation */}
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2.5">
              <span className="text-gold-400">📈</span> All Clients Profit &amp; Loss Ledger
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              Overview of registered clients, live Tradejini broker connections, and algorithmic strategy performance.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {/* Strategy vs All Broker Filter Toggle */}
            <div className="flex items-center rounded-lg border border-slate-700/80 bg-slate-900/90 p-1 text-xs font-medium shadow-inner">
              <button
                onClick={() => setSource("copier")}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition ${
                  source === "copier"
                    ? "bg-amber-500/20 text-amber-300 font-semibold border border-amber-500/30 shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <span>⚡</span> Strategy Trades Only
              </button>
              <button
                onClick={() => setSource("all")}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition ${
                  source === "all"
                    ? "bg-blue-500/20 text-blue-300 font-semibold border border-blue-500/30 shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                <span>🏦</span> All Broker Activity
              </button>
            </div>

            <button
              className="btn-ghost text-xs text-slate-300 hover:text-white bg-slate-800/80 px-3.5 py-1.5 rounded-lg border border-slate-700/60 shadow-sm transition"
              onClick={() => load(source)}
            >
              🔄 Refresh
            </button>
            <button
              className="text-xs text-slate-400 hover:text-white px-3 py-1.5 transition"
              onClick={() => { clearToken(); window.location.href = "/"; }}
            >
              Log out
            </button>
          </div>
        </div>

        {/* Aggregate Stats Bar */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
          <div className="card p-5 border-slate-800 bg-slate-900/50">
            <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Registered Clients</p>
            <p className="mt-1.5 text-2xl font-bold text-white font-mono">{clients.length}</p>
            <p className="text-[11px] text-slate-500 mt-1">
              {clients.filter(c => c.tradejini_connected).length} active broker connections
            </p>
          </div>

          <div className={`card p-5 border ${totalBookedPnl >= 0 ? "border-emerald-500/40 bg-emerald-950/20" : "border-rose-500/40 bg-rose-950/20"}`}>
            <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Net Realized PnL</p>
            <p className={`mt-1.5 text-2xl font-bold font-mono ${totalBookedPnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              {formatInr(totalBookedPnl)}
            </p>
            <p className="text-[11px] text-slate-500 mt-1">
              Gross: <span className="font-mono text-slate-300">{formatInr(totalGrossPnl)}</span> · Fees: <span className="font-mono text-slate-300">₹{totalFees.toLocaleString()}</span>
            </p>
          </div>

          <div className="card p-5 border-slate-800 bg-slate-900/50">
            <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Total Executed Trades</p>
            <p className="mt-1.5 text-2xl font-bold text-white font-mono">{totalTrades}</p>
            <p className="text-[11px] text-slate-500 mt-1">
              Across all client accounts
            </p>
          </div>

          <div className="card p-5 border-slate-800 bg-slate-900/50">
            <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">Tradejini Connected</p>
            <p className="mt-1.5 text-2xl font-bold text-gold-400 font-mono">
              {clients.filter(c => c.tradejini_connected).length} / {clients.length}
            </p>
            <p className="text-[11px] text-emerald-400 mt-1 flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span> Auto-Sync Enabled
            </p>
          </div>
        </div>

        {/* Client Ledger Table Card */}
        <div className="card p-5 border-slate-800 bg-slate-900/50">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">
              Client Ledger Directory ({filteredClients.length})
            </h2>
            <input
              type="text"
              placeholder="Search name, email, client ID, phone..."
              className="bg-slate-950 border border-slate-700 text-xs px-3 py-2 rounded-lg text-white font-mono w-full sm:w-80 focus:border-gold-500 focus:outline-none"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {loading ? (
            <div className="py-16 text-center text-sm text-slate-400">
              <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-gold-400 border-t-transparent mb-3"></div>
              <p>Fetching live broker accounts and profit ledger…</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs whitespace-nowrap">
                <thead className="text-left uppercase tracking-wider text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="pb-3">Client</th>
                    <th className="pb-3">Tradejini ID / Phone</th>
                    <th className="pb-3 text-center">Account Status</th>
                    <th className="pb-3 text-center">Broker Connection</th>
                    <th className="pb-3 text-center">Trades (Win Rate)</th>
                    <th className="pb-3 text-right">Net Realized PnL</th>
                    <th className="pb-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {filteredClients.map((c) => {
                    const targetId = c.client_id || c.user_id;
                    const netPnl = c.net_pnl_inr ?? (c.booked_pnl_inr - (c.total_fees_inr || 0));
                    const winRate = c.total_trades > 0 ? Math.round((c.wins / c.total_trades) * 100) : 0;
                    return (
                      <tr key={c.user_id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="py-3.5 font-medium text-white">
                          <div className="font-semibold text-sm text-white">{c.name || "Client"}</div>
                          <div className="text-xs text-slate-400 font-mono">{c.email}</div>
                        </td>
                        <td className="font-mono">
                          <span className="text-gold-400 font-bold block">{c.client_id || "—"}</span>
                          <span className="text-[11px] text-slate-500">{formatPhone(c.phone)}</span>
                        </td>
                        <td className="text-center">
                          {c.is_deleted ? (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/10 text-rose-400">Archived</span>
                          ) : c.status === "approved" ? (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400">🟢 Approved</span>
                          ) : c.status === "rejected" ? (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/10 text-rose-400">🔴 Rejected</span>
                          ) : (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400">⏳ Pending</span>
                          )}
                        </td>
                        <td className="text-center">
                          {c.tradejini_connected ? (
                            <span className="inline-flex items-center gap-1 text-[11px] text-emerald-400 font-medium">
                              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span> Connected
                            </span>
                          ) : (
                            <span className="text-[11px] text-slate-500">Disconnected</span>
                          )}
                        </td>
                        <td className="text-center font-mono">
                          <span className="font-bold text-white">{c.total_trades}</span>
                          {c.total_trades > 0 && (
                            <span className="text-[11px] text-slate-500 block">
                              {c.wins}W / {c.total_trades - c.wins}L ({winRate}%)
                            </span>
                          )}
                        </td>
                        <td className="text-right font-mono">
                          <span className={`text-base font-bold block ${netPnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                            {formatInr(netPnl)}
                          </span>
                          <span className="text-[10px] text-slate-500">Gross: {formatInr(c.booked_pnl_inr)}</span>
                        </td>
                        <td className="text-right">
                          <Link
                            href={`/admin/ledger/${targetId}/profits`}
                            className="px-3 py-1.5 bg-gold-500 hover:bg-gold-400 text-slate-950 text-xs font-bold rounded-lg transition inline-flex items-center gap-1 shadow-sm"
                          >
                            <span>View Profits</span>
                            <span>→</span>
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredClients.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-12 text-center text-sm text-slate-500">
                        No clients found matching your search.
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
