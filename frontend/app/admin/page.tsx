"use client";

import { useEffect, useState } from "react";
import { Logo } from "@/components/nav";
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

  const filtered = clients.filter((c) => c.email.toLowerCase().includes(q.toLowerCase()));

  const stat = (label: string, value: string) => (
    <div key={label} className="card p-5">
      <p className="text-xs uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-white">{value}</p>
    </div>
  );

  return (
    <main className="min-h-screen">
      <AdminNav />

      <div className="container-x py-8">
        {err && (
          <p className="card mb-6 border-loss/40 p-4 text-sm text-loss">{err}</p>
        )}

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {stat("Total clients", String(stats?.total_clients ?? "—"))}
          {stat("Approved access", String(clients.filter((c) => c.payment_status === "approved").length))}
          {stat("Pending approval", String(clients.filter((c) => c.payment_status === "pending" || c.payment_status === "pending_verification").length))}
          {stat("Tradejini connected", String(clients.filter((c) => c.tradejini === "connected").length))}
        </div>

        <div className="card mt-6 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-white">Clients</h2>
            <input
              className="input max-w-xs"
              placeholder="Search email…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          {loading ? (
            <p className="py-6 text-center text-sm text-muted">Loading…</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm whitespace-nowrap">
                <thead className="text-left text-xs uppercase tracking-wider text-muted">
                  <tr>
                    <th className="pb-2">Client</th>
                    <th className="pb-2">Phone</th>
                    <th className="pb-2">Tradejini Client ID</th>
                    <th className="pb-2">Access Status</th>
                    <th className="pb-2">Connection</th>
                    <th className="pb-2"></th>
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {filtered.map((c) => (
                    <tr key={c.id || c.email} className="border-t border-ink-800">
                      <td className="py-3 font-medium text-white">
                        <div>{c.name || "Unknown Name"}</div>
                        <div className="text-xs text-muted font-normal">{c.email}</div>
                      </td>
                      <td className="font-mono text-sm text-slate-300">
                        {c.phone ? (c.phone.length === 10 ? `+91 ${c.phone.slice(0, 5)} ${c.phone.slice(5)}` : c.phone) : "—"}
                      </td>
                      <td className="font-mono text-gold-400 font-semibold">{c.client_id || "—"}</td>
                      <td>
                        <Badge ok={c.payment_status === "approved" || c.subscription === "active"}>
                          {c.payment_status === "approved" ? "Approved" : (c.subscription ?? "Pending")}
                        </Badge>
                      </td>
                      <td>
                        <Badge ok={c.tradejini === "connected" && !c.paused}>
                          {c.paused ? "paused" : (c.tradejini === "connected" ? "Connected" : "None")}
                        </Badge>
                      </td>
                      <td className="text-right">
                        <button
                          className="btn-gold !px-3 !py-1.5 text-xs font-semibold"
                          onClick={() => setManageId(c.id)}
                        >
                          Manage
                        </button>
                      </td>
                    </tr>
                  ))}
                  {filtered.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-6 text-center text-sm text-muted">
                        No clients.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Market screener removed from UI but mechanics kept in backend */}

        
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
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs capitalize ${
        ok ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-gain" : "bg-loss"}`} />
      {children}
    </span>
  );
}
