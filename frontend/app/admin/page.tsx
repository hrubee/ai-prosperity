"use client";

import { useEffect, useState } from "react";
import { Logo } from "@/components/nav";
import { api, formatInr, isAuthed } from "@/lib/api";
import { AdminNav } from "@/components/AdminNav";
import { ManageDrawer } from "./ManageDrawer";

type Client = {
  id: string;
  email: string;
  package: string | null;
  subscription: string | null;
  connection: string | null;
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
          {stat("Active subscribers", String(stats?.active_subscribers ?? "—"))}
          {stat("MRR", stats ? formatInr(stats.mrr_inr) : "—")}
          {stat("Connected", String(clients.filter((c) => c.connection === "connected").length))}
        </div>

        {/* Subscriptions sold, broken down by plan */}
        <div className="mt-6">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
            Subscriptions sold by plan
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
            {stats &&
              Object.entries(stats.by_package).map(([id, p]) => (
                <PlanCard key={id} id={id} p={p} onReload={reload} />
              ))}
          </div>
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
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-muted">
                  <tr>
                    <th className="pb-2">Client</th>
                    <th className="pb-2">Plan</th>
                    <th className="pb-2">Subscription</th>
                    <th className="pb-2">Connection</th>
                    <th className="pb-2"></th>
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {filtered.map((c) => (
                    <tr key={c.email} className="border-t border-ink-800">
                      <td className="py-3 font-medium text-white">{c.email}</td>
                      <td className="capitalize">{c.package ?? "—"}</td>
                      <td>
                        <Badge ok={c.subscription === "active"}>{c.subscription ?? "none"}</Badge>
                      </td>
                      <td>
                        <Badge ok={c.connection === "connected" && !c.paused}>
                          {c.paused ? "paused" : c.connection ?? "none"}
                        </Badge>
                      </td>
                      <td className="text-right">
                        <button
                          className="btn-ghost !px-3 !py-1.5 text-xs"
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

function PlanCard({ id, p, onReload }: { id: string; p: any; onReload: () => void }) {
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState(p.price_inr);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.updatePlan(parseInt(id), parseInt(price));
      setEditing(false);
      onReload();
    } catch (e: any) {
      alert("Failed to update plan: " + e.message);
    } finally {
      setSaving(false);
    }
  }

  // legacy plans (like 'starter', 'pro') shouldn't be editable here, only numeric (1,3,6,12)
  const isEditable = !isNaN(parseInt(id));

  return (
    <div className="card flex flex-col p-5">
      <div className="flex items-center justify-between mb-4">
        <p className="font-semibold text-white">{p.name}</p>
        {!editing && isEditable && (
          <button onClick={() => setEditing(true)} className="text-xs text-gold-400 hover:underline">
            Edit
          </button>
        )}
      </div>

      {editing ? (
        <div className="mb-4">
          <label className="text-xs text-muted block mb-1">Price (INR)</label>
          <div className="flex gap-2">
            <input type="number" className="input text-sm py-1" value={price} onChange={e => setPrice(e.target.value)} />
            <button className="btn-gold !px-3 !py-1 text-xs" onClick={save} disabled={saving}>{saving ? "..." : "Save"}</button>
            <button className="btn-ghost !px-3 !py-1 text-xs" onClick={() => {setEditing(false); setPrice(p.price_inr)}}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="mb-4">
          <span className="pill">{formatInr(p.price_inr)}</span>
        </div>
      )}

      <div className="mt-auto">
        <div className="flex items-end gap-2">
          <span className="text-3xl font-bold text-gold-400">{p.active}</span>
          <span className="pb-1 text-sm text-muted">active</span>
        </div>
        <p className="mt-1 text-xs text-muted">
          {p.total} total sold · {formatInr(p.active * p.price_inr)}/mo
        </p>
      </div>
    </div>
  );
}
