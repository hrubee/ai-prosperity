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

  async function load() {
    try {
      const res = await api.adminLedgerClients();
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
    load();
  }, []);

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

  const totalBookedPnl = clients.reduce((acc, c) => acc + c.booked_pnl_inr, 0);
  const totalTrades = clients.reduce((acc, c) => acc + c.total_trades, 0);

  return (
    <main className="min-h-screen">
      <AdminNav />

      <div className="container-x py-8">
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <span>📈</span> Permanent Client Profit Ledger
            </h1>
            <p className="mt-1 text-sm text-muted">
              Select any client below to view their Tradejini account booked PnL, live trade positions, and permanent audit logs.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              className="btn-ghost text-xs text-muted hover:text-white"
              onClick={load}
            >
              🔄 Refresh List
            </button>
            <button
              className="text-xs text-muted hover:text-white"
              onClick={() => { clearToken(); window.location.href = "/"; }}
            >
              Log out
            </button>
          </div>
        </div>

        {/* Aggregate Stats Bar */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
          <div className="card p-5">
            <p className="text-xs uppercase tracking-wider text-muted">Total Registered Clients</p>
            <p className="mt-1 text-2xl font-bold text-white">{clients.length}</p>
          </div>
          <div className="card p-5 border-gain/30 bg-gain/5">
            <p className="text-xs uppercase tracking-wider text-muted">Total Client Booked PnL</p>
            <p className={`mt-1 text-2xl font-bold ${totalBookedPnl >= 0 ? "text-gain" : "text-loss"}`}>
              {formatInr(totalBookedPnl)}
            </p>
          </div>
          <div className="card p-5">
            <p className="text-xs uppercase tracking-wider text-muted">Total Executed Trades</p>
            <p className="mt-1 text-2xl font-bold text-white">{totalTrades}</p>
          </div>
          <div className="card p-5">
            <p className="text-xs uppercase tracking-wider text-muted">Tradejini Connected</p>
            <p className="mt-1 text-2xl font-bold text-gold-400">
              {clients.filter(c => c.tradejini_connected).length} / {clients.length}
            </p>
          </div>
        </div>

        {/* Client Ledger Table Card */}
        <div className="card p-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
            <h2 className="text-base font-semibold text-white">
              Clients Directory ({filteredClients.length})
            </h2>
            <input
              type="text"
              placeholder="Search name, email, client ID, phone..."
              className="input text-sm w-full sm:w-72"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {loading ? (
            <p className="py-10 text-center text-sm text-muted">Loading client profit ledger…</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm whitespace-nowrap">
                <thead className="text-left text-xs uppercase tracking-wider text-muted border-b border-ink-800">
                  <tr>
                    <th className="pb-3">Client</th>
                    <th className="pb-3">Phone</th>
                    <th className="pb-3">Tradejini Client ID</th>
                    <th className="pb-3 text-center">Status</th>
                    <th className="pb-3 text-center">Executed Trades</th>
                    <th className="pb-3 text-right">Booked PnL (INR)</th>
                    <th className="pb-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {filteredClients.map((c) => {
                    const targetId = c.client_id || c.user_id;
                    return (
                      <tr key={c.user_id} className="border-t border-ink-800/60 hover:bg-ink-800/30 transition-colors">
                        <td className="py-3 font-medium text-white">
                          <div>{c.name || "Client"}</div>
                          <div className="text-xs text-muted font-normal">{c.email}</div>
                        </td>
                        <td className="font-mono text-sm">{formatPhone(c.phone)}</td>
                        <td className="font-mono text-gold-400 font-semibold">{c.client_id || "—"}</td>
                        <td className="text-center">
                          {c.is_deleted ? (
                            <span className="pill bg-loss/10 text-loss">Archived</span>
                          ) : c.status === "approved" ? (
                            <span className="pill bg-gain/10 text-gain">🟢 Approved</span>
                          ) : c.status === "rejected" ? (
                            <span className="pill bg-loss/10 text-loss">🔴 Rejected</span>
                          ) : (
                            <span className="pill bg-warning/10 text-warning">⏳ Pending</span>
                          )}
                        </td>
                        <td className="text-center font-mono">{c.total_trades}</td>
                        <td className={`text-right font-mono font-bold text-base ${c.booked_pnl_inr >= 0 ? "text-gain" : "text-loss"}`}>
                          {formatInr(c.booked_pnl_inr)}
                        </td>
                        <td className="text-right">
                          <Link
                            href={`/admin/ledger/${targetId}/profits`}
                            className="btn-gold !py-1 !px-3 text-xs inline-flex items-center gap-1.5"
                          >
                            <span>View Profits</span>
                            <span>📈</span>
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredClients.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-10 text-center text-sm text-muted">
                        No clients found matching query.
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
