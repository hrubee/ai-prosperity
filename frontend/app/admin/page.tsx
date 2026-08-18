"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, formatInr, isAuthed } from "@/lib/api";
import { AdminNav } from "@/components/AdminNav";
import { ManageDrawer } from "./ManageDrawer";

type Client = {
  id: string;
  email: string;
  name?: string | null;
  phone?: string | null;
  client_id?: string | null;
  package: string | null;
  subscription: string | null;
  payment_status: string;
  connection: string | null;
  tradejini: string | null;
  paused: boolean | null;
  lot_multiplier?: number;
  buyable_cash_inr?: number | null;
};

type Stats = {
  total_clients: number;
  active_subscribers: number;
  mrr_inr: number;
  by_package: Record<string, { name: string; price_inr: number; active: number; total: number }>;
};

export default function Admin() {
  const [clients, setClients] = useState<Client[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [manageId, setManageId] = useState<string | null>(null);
  const [updatingMultiplier, setUpdatingMultiplier] = useState<Record<string, boolean>>({});

  function reload() {
    Promise.all([api.adminStats(), api.adminClients()])
      .then(([s, c]) => {
        setStats(s);
        setClients(c);
      })
      .catch(() => {});
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    Promise.all([api.adminStats(), api.adminClients()])
      .then(([s, c]) => {
        setStats(s);
        setClients(c);
      })
      .catch((e: any) => setErr(e.message || "Failed to load (admin only)"))
      .finally(() => setLoading(false));
  }, []);

  async function handleUpdateLotMultiplier(clientId: string, newMultiplier: number) {
    const val = Math.max(0.1, roundTwo(newMultiplier));
    setUpdatingMultiplier((prev) => ({ ...prev, [clientId]: true }));
    try {
      await api.adminSetLotMultiplier(clientId, val);
      setClients((prev) =>
        prev.map((c) => (c.id === clientId ? { ...c, lot_multiplier: val } : c))
      );
    } catch (e: any) {
      alert(`Failed to update lot multiplier: ${e.message || "Network error"}`);
    } finally {
      setUpdatingMultiplier((prev) => ({ ...prev, [clientId]: false }));
    }
  }

  function roundTwo(num: number) {
    return Math.round((num + Number.EPSILON) * 100) / 100;
  }

  const filtered = clients.filter((c) => {
    const query = q.toLowerCase();
    return (
      c.email.toLowerCase().includes(query) ||
      (c.name && c.name.toLowerCase().includes(query)) ||
      (c.client_id && c.client_id.toLowerCase().includes(query)) ||
      (c.phone && c.phone.includes(query))
    );
  });

  const stat = (label: string, value: string) => (
    <div key={label} className="card p-5 border-slate-800 bg-slate-900/50">
      <p className="text-xs uppercase tracking-wider text-slate-400 font-medium">{label}</p>
      <p className="mt-1.5 text-2xl font-bold font-mono text-white">{value}</p>
    </div>
  );

  return (
    <main className="min-h-screen pb-16 bg-[#0B0F19] text-slate-100">
      <AdminNav />

      <div className="container-x py-8">
        {err && (
          <p className="card mb-6 border-rose-500/40 bg-rose-950/20 p-4 text-sm text-rose-400">{err}</p>
        )}

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
          {stat("Total clients", String(stats?.total_clients ?? clients.length))}
          {stat("Approved access", String(clients.filter((c) => c.payment_status === "approved").length))}
          {stat("Pending approval", String(clients.filter((c) => c.payment_status === "pending" || c.payment_status === "pending_verification").length))}
          {stat("Tradejini connected", String(clients.filter((c) => c.tradejini === "connected").length))}
        </div>

        <div className="card p-5 border-slate-800 bg-slate-900/50">
          <div className="mb-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <h2 className="text-base font-bold text-white uppercase tracking-wider">
                Client Directory &amp; Sizing ({filtered.length})
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Manage lot sizing multipliers, view live Tradejini cash limits, and manage client accounts.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <input
                className="bg-slate-950 border border-slate-700 text-xs px-3 py-2 rounded-lg text-white font-mono w-full sm:w-72 focus:border-gold-500 focus:outline-none"
                placeholder="Search email, name, client ID…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <button
                onClick={reload}
                className="btn-ghost text-xs text-slate-300 hover:text-white bg-slate-800/80 px-3 py-2 rounded-lg border border-slate-700/60 shadow-sm"
              >
                🔄 Refresh
              </button>
            </div>
          </div>

          {loading ? (
            <div className="py-16 text-center text-sm text-slate-400">
              <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-gold-400 border-t-transparent mb-3"></div>
              <p>Loading client accounts and live Tradejini cash margins…</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs whitespace-nowrap">
                <thead className="text-left uppercase tracking-wider text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="pb-3">Client</th>
                    <th className="pb-3">Phone</th>
                    <th className="pb-3">Tradejini Client ID</th>
                    <th className="pb-3 text-right text-gold-400 font-bold">Buyable Cash</th>
                    <th className="pb-3 text-center">Access Status</th>
                    <th className="pb-3 text-center">Connection</th>
                    <th className="pb-3 text-center">Lot Multiplier</th>
                    <th className="pb-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {filtered.map((c) => {
                    const currentMultiplier = c.lot_multiplier ?? 1.0;
                    const isBusy = !!updatingMultiplier[c.id];

                    return (
                      <tr key={c.id || c.email} className="hover:bg-slate-800/30 transition-colors">
                        <td className="py-3.5 font-medium text-white">
                          <div className="font-semibold text-sm text-white">{c.name || "Client"}</div>
                          <div className="text-xs text-slate-400 font-mono">{c.email}</div>
                        </td>
                        <td className="font-mono text-slate-300">
                          {c.phone ? (c.phone.length === 10 ? `+91 ${c.phone.slice(0, 5)} ${c.phone.slice(5)}` : c.phone) : "—"}
                        </td>
                        <td className="font-mono text-gold-400 font-bold text-sm">{c.client_id || "—"}</td>

                        {/* Buyable Cash Column (Between Tradejini ID and Access Status) */}
                        <td className="text-right font-mono">
                          {c.buyable_cash_inr != null ? (
                            <span className="text-emerald-400 font-bold text-sm block">
                              {formatInr(c.buyable_cash_inr)}
                            </span>
                          ) : (
                            <span className="text-slate-500 font-normal text-xs">—</span>
                          )}
                        </td>

                        <td className="text-center">
                          <Badge ok={c.payment_status === "approved" || c.subscription === "active"}>
                            {c.payment_status === "approved" ? "Approved" : (c.subscription ?? "Pending")}
                          </Badge>
                        </td>

                        <td className="text-center">
                          <Badge ok={c.tradejini === "connected" && !c.paused}>
                            {c.paused ? "Paused" : (c.tradejini === "connected" ? "Connected" : "None")}
                          </Badge>
                        </td>

                        {/* Interactive Lot Sizing Multiplier Controls */}
                        <td className="text-center font-mono">
                          <div className="inline-flex items-center gap-1.5 bg-slate-950/90 px-2 py-1 rounded-lg border border-slate-700/80 shadow-sm">
                            <button
                              disabled={isBusy || currentMultiplier <= 0.1}
                              onClick={() => handleUpdateLotMultiplier(c.id, Math.max(0.1, currentMultiplier - 0.5))}
                              className="h-6 w-6 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 font-bold flex items-center justify-center text-xs transition active:scale-95"
                              title="Decrease lot size multiplier (-0.5x)"
                            >
                              -
                            </button>
                            <span className="font-bold text-gold-400 min-w-[42px] text-center text-xs">
                              {isBusy ? "…" : `${currentMultiplier.toFixed(1)}x`}
                            </span>
                            <button
                              disabled={isBusy}
                              onClick={() => handleUpdateLotMultiplier(c.id, currentMultiplier + 0.5)}
                              className="h-6 w-6 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 font-bold flex items-center justify-center text-xs transition active:scale-95"
                              title="Increase lot size multiplier (+0.5x)"
                            >
                              +
                            </button>
                          </div>
                        </td>

                        <td className="text-right">
                          <div className="flex items-center justify-end gap-2">
                            <Link
                              href={`/admin/ledger/${c.client_id || c.id}/profits`}
                              className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs font-medium border border-slate-700 transition"
                            >
                              Ledger 📊
                            </Link>
                            <button
                              className="btn-gold !px-3 !py-1 text-xs font-semibold shadow-sm"
                              onClick={() => setManageId(c.id)}
                            >
                              Manage
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {filtered.length === 0 && (
                    <tr>
                      <td colSpan={8} className="py-12 text-center text-sm text-slate-500">
                        No clients found matching search query.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {manageId && (
        <ManageDrawer
          id={manageId}
          onClose={() => setManageId(null)}
          onChanged={reload}
        />
      )}
    </main>
  );
}

function Badge({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] font-bold capitalize ${
        ok ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-400"}`} />
      {children}
    </span>
  );
}
