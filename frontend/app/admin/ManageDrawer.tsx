"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Detail = Awaited<ReturnType<typeof api.adminClientDetail>>;

function isDate(val: any) {
  return !isNaN(Date.parse(val));
}

export function ManageDrawer({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setD(await api.adminClientDetail(id));
    } catch (e: any) {
      setMsg(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(name: string, fn: () => Promise<any>, confirmMsg?: string) {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(name);
    setMsg("");
    try {
      const r = await fn();
      setMsg(typeof r?.result === "string" ? r.result : "Done.");
      onChanged();
      if (name === "delete") {
        setTimeout(onClose, 1500);
        return;
      }
      await load();
    } catch (e: any) {
      setMsg(e.message || "Action failed");
    } finally {
      setBusy("");
    }
  }

  const connected = d?.connection?.status === "connected";
  const paused = d?.connection?.paused || d?.tradejini?.paused;
  // a client may have only a Tradejini connection (no Delta) — admin actions cover both
  const hasAnyConn = !!d?.connection || !!d?.tradejini;
  const anyConnected = connected || d?.tradejini?.status === "connected";

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      {/* panel */}
      <div className="relative h-full w-full max-w-md overflow-y-auto border-l border-ink-700 bg-ink-900 p-4 sm:p-6 shadow-2xl">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">Manage client</h2>
          <button className="text-muted hover:text-white" onClick={onClose}>✕</button>
        </div>

        {loading || !d ? (
          <p className="py-10 text-center text-muted">Loading…</p>
        ) : (
          <>
            <p className="font-medium text-white">{d.email}</p>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
              <span className="pill capitalize">{d.subscription?.package ?? "no plan"}</span>
              <span className="pill capitalize">{d.subscription?.status ?? "—"}</span>
              {d.subscription && (
                <div className="flex items-center gap-1 bg-ink-800 rounded px-2 py-1">
                  <span className="text-muted">Expires:</span>
                  <input
                    type="date"
                    className="bg-transparent text-white outline-none"
                    value={d.subscription.current_period_end ? d.subscription.current_period_end.substring(0, 10) : ""}
                    onChange={(e) => {
                      const iso = e.target.value ? new Date(e.target.value).toISOString() : null;
                      act("update_sub", () => api.adminUpdateSubscription(id, iso));
                    }}
                  />
                </div>
              )}
              <span className="pill">{paused ? "paused" : connected ? "connected" : d.connection?.status ?? "no key"}</span>
              {d.connection && (
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    d.connection.sandbox ? "bg-gold-500/15 text-gold-400" : "bg-loss/15 text-loss"
                  }`}
                >
                  {d.connection.sandbox ? "TESTNET" : "● LIVE — real money"}
                </span>
              )}
            </div>

            {/* equity + positions */}
            <div className="mt-5 grid grid-cols-2 gap-3">
              <div className="card p-4">
                <p className="text-xs uppercase tracking-wider text-muted">Equity</p>
                <p className="mt-1 text-lg font-semibold text-white">${d.equity.toFixed(2)}</p>
              </div>
              <div className="card p-4">
                <p className="text-xs uppercase tracking-wider text-muted">Open positions</p>
                <p className="mt-1 text-lg font-semibold text-white">{d.positions.length}</p>
              </div>
            </div>
            {d.live_error && (
              <p className="mt-3 rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
                Live data error: {d.live_error}
              </p>
            )}

            {d.positions.length > 0 && (
              <div className="mt-3 card p-4 text-sm">
                {d.positions.map((p) => (
                  <div key={p.symbol} className="flex justify-between border-b border-ink-800 py-1.5 last:border-0">
                    <span className="text-white">{p.base}</span>
                    <span className={p.side === "long" ? "text-gain" : "text-loss"}>{p.side.toUpperCase()}</span>
                    <span className="text-muted">{p.coin_size}</span>
                    <span className="font-mono text-muted">{p.entry.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Tradejini (Indian F&O) venue */}
            {d.tradejini && (
              <div className="mt-4 rounded-xl border border-ink-700 bg-ink-800/40 p-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-white">Tradejini (F&O)</p>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      d.tradejini.status === "connected"
                        ? "bg-gain/15 text-gain"
                        : "bg-loss/15 text-loss"
                    }`}
                  >
                    {d.tradejini.status}
                  </span>
                </div>
                <p className="mt-2 text-sm text-muted">
                  Margin ₹{(d.tradejini.equity_inr ?? 0).toLocaleString("en-IN")} ·{" "}
                  {d.tradejini.positions?.length ?? 0} positions
                  {d.tradejini.expires_at &&
                    ` · expires ${new Date(d.tradejini.expires_at).toLocaleString([], {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}`}
                </p>
                {d.tradejini.error && (
                  <p className="mt-1 text-xs text-loss">{d.tradejini.error}</p>
                )}
              </div>
            )}

            {/* actions */}
            <div className="mt-6 space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted">Actions</p>
              <button
                className="btn-ghost w-full justify-center"
                disabled={!hasAnyConn || !!busy}
                onClick={() => act("pause", () => api.adminPause(id, !paused))}
              >
                {busy === "pause" ? "…" : paused ? "Resume trading" : "Pause trading"}
              </button>
              <button
                className="btn-ghost w-full justify-center !border-gold-500/50 !text-gold-400"
                disabled={!anyConnected || !!busy}
                onClick={() =>
                  act("force", () => api.adminForceClose(id), "Force-close ALL of this client's open positions now (both venues)?")
                }
              >
                {busy === "force" ? "Closing…" : "Force-close positions"}
              </button>
              <button
                className="btn-ghost w-full justify-center !border-loss/50 !text-loss"
                disabled={!hasAnyConn || !!busy}
                onClick={() =>
                  act("disc", () => api.adminDisconnect(id), "Disconnect this client (both venues)? They stop trading until they reconnect.")
                }
              >
                {busy === "disc" ? "…" : "Disconnect client"}
              </button>
              <button
                className="btn-ghost w-full justify-center !border-loss/50 !text-loss"
                disabled={!!busy}
                onClick={() =>
                  act("delete", () => api.adminDeleteClient(id), "PERMANENTLY delete this client and all their data? This cannot be undone.")
                }
              >
                {busy === "delete" ? "Deleting…" : "Delete client"}
              </button>
            </div>

            {msg && <p className="mt-4 text-sm text-gold-400">{msg}</p>}

            {/* recent activity */}
            <div className="mt-6">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                Recent execution
              </p>
              {d.orders.length === 0 ? (
                <p className="text-sm text-muted">No activity yet.</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {d.orders.map((o, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="text-muted">
                        {o.at ? new Date(o.at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                      </span>
                      <span className={o.side === "buy" ? "text-gain" : o.side === "sell" ? "text-loss" : "text-gold-400"}>
                        {o.side.toUpperCase()}
                      </span>
                      <span className="text-white">{o.symbol}</span>
                      <span className="text-muted">· {o.status}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
