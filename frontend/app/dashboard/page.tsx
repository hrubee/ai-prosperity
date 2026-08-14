"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, clearToken, isAuthed, type Me, type OrderRow } from "@/lib/api";

export default function Dashboard() {
  const [me, setMe] = useState<Me | null>(null);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [tj, setTj] = useState<Awaited<ReturnType<typeof api.myTradejini>> | null>(null);
  const [cdcx, setCdcx] = useState<Awaited<ReturnType<typeof api.coindcxStatus>> | null>(null);
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      const m = await api.me();
      setMe(m);
      if (m.connection?.status === "connected") {
        const o = await api.myOrders();
        setOrders(o || []);
      }
    } catch {
      // unauthorized / backend down
    }
    try {
      setTj(await api.myTradejini());
    } catch {
      /* no tradejini connection */
    }
    try {
      setCdcx(await api.coindcxStatus());
    } catch {
      /* no coindcx connection */
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    const p = new URLSearchParams(window.location.search).get("tradejini");
    if (p === "connected") setNotice("Tradejini account connected ✓");
    else if (p) setNotice("Tradejini connection failed — please try again.");
    load();
  }, []);

  const isApproved = me?.payment_status === "approved";

  return (
    <main className="min-h-screen">
      <header className="border-b border-ink-800 bg-ink-950/70">
        <div className="container-x flex min-h-[4rem] flex-wrap items-center justify-between gap-4 py-3">
          <Logo />
          <div className="flex flex-wrap items-center gap-2 sm:gap-3 text-sm">
            {me?.client_id && (
              <span className="pill font-mono">ID: {me.client_id}</span>
            )}
            <span className={`pill ${isApproved ? "bg-gain/20 text-gain" : "bg-gold-500/20 text-gold-400"}`}>
              {isApproved ? "🟢 Account Approved" : "⏳ Pending Approval"}
            </span>
            <Link href="/account" className="text-muted hover:text-white">Account</Link>
            <button
              className="text-muted hover:text-white"
              onClick={() => { clearToken(); window.location.href = "/"; }}
            >
              Log out
            </button>
          </div>
        </div>
      </header>

      <div className="container-x py-8">
        {loading ? (
          <div className="card p-10 text-center text-muted">Loading your account…</div>
        ) : (
          <>
            {notice && (
              <div className="card mb-6 border-gold-500/40 p-3 text-center text-sm text-gold-400">
                {notice}
              </div>
            )}

            {/* Pending Approval Stage Card */}
            {!isApproved && (
              <div className="card mb-6 p-8 border-gold-500/30 text-center max-w-2xl mx-auto">
                <div className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-gold-500/10 text-gold-400 text-2xl font-bold mb-4">
                  ⏳
                </div>
                <h2 className="text-2xl font-bold text-white mb-2">Account Awaiting Approval</h2>
                <p className="text-sm text-muted leading-relaxed max-w-lg mx-auto mb-6">
                  Welcome <b className="text-white">{me?.name || me?.email}</b>! Your registration details and Tradejini Client ID (
                  <span className="font-mono text-gold-400 font-semibold">{me?.client_id || "Not set"}</span>) have been received.
                  An administrator is reviewing your request. Once approved, your trade automation features will unlock automatically.
                </p>
                <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/30 bg-gold-500/10 px-4 py-1.5 text-xs text-gold-400 font-medium">
                  <span className="h-2 w-2 rounded-full bg-gold-400 animate-pulse" />
                  Status: Pending Admin Approval
                </div>
              </div>
            )}

            {/* CoinDCX (Crypto Futures & Spot) connection */}
            {isApproved && <CoinDCXPanel cdcx={cdcx} onReload={load} />}

            {/* Tradejini (Indian F&O) connection */}
            {isApproved && <TradejiniPanel tj={tj} onReload={load} />}

            {/* Signal Feed */}
            {isApproved && (
              <div className="mt-6 max-w-3xl">
                <div className="card p-5">
                  <h2 className="mb-4 font-semibold text-white">Recent signals</h2>
                  {orders.length === 0 ? (
                    <p className="py-6 text-center text-sm text-muted">No activity yet.</p>
                  ) : (
                    <ul className="space-y-4">
                      {orders.map((s, i) => (
                        <li key={i} className="flex gap-3 text-sm">
                          <span className="mt-0.5 w-14 shrink-0 text-xs text-muted">
                            {s.at ? new Date(s.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                          </span>
                          <div>
                            <p className="text-white">
                              <span
                                className={
                                  s.side === "buy" ? "text-gain" : s.side === "sell" ? "text-loss" : "text-gold-400"
                                }
                              >
                                {s.side.toUpperCase()}
                              </span>{" "}
                              {s.symbol}{" "}
                              <span className="text-xs text-muted">· {s.status}</span>
                            </p>
                            {s.detail && <p className="text-muted">{s.detail}</p>}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}

// ── TradejiniPanel ──────────────────────────────────────────────────────────
type TjState = Awaited<ReturnType<typeof api.myTradejini>> | null;

function TradejiniPanel({ tj, onReload }: { tj: TjState; onReload: () => Promise<void> }) {
  if (!tj || !tj.connected_once) {
    return (
      <div className="card mb-6 flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center">
        <p className="text-sm text-muted">
          Trade <b className="text-slate-200">Indian F&amp;O</b> (NIFTY / BankNIFTY)?
        </p>
        <Link href="/connect/tradejini" className="btn-ghost text-sm">
          Connect Tradejini →
        </Link>
      </div>
    );
  }

  return (
    <div className={`card mb-6 p-5 ${tj.error ? "border-loss/60" : ""}`}>
      <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
        <div className="flex items-center gap-3">
          <span className={`h-2.5 w-2.5 rounded-full ${tj.connected ? "bg-gain" : "bg-loss"}`} />
          <div>
            <p className="font-semibold text-white">
              {tj.connected ? "Tradejini connected — Indian F&O live" : "Tradejini auto-renewing…"}
            </p>
            <p className="text-sm text-muted">
              {tj.expires_at
                ? `Session ends ${new Date(tj.expires_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}`
                : ""}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn-ghost text-sm"
            onClick={async () => {
              await api.tradejiniPause(!tj.paused);
              await onReload();
            }}
          >
            {tj.paused ? "Resume" : "Pause"}
          </button>
          <button
            className="btn-ghost text-sm"
            onClick={async () => {
              await api.tradejiniDisconnect();
              await onReload();
            }}
          >
            Disconnect
          </button>
        </div>
      </div>
      {tj.error && (
        <div className="mt-4 space-y-2">
          <p className="rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
            Auto-renew failed: {tj.error}
          </p>
          <Link href="/connect/tradejini" className="btn-ghost text-sm inline-block">
            Reconnect →
          </Link>
        </div>
      )}
    </div>
  );
}

// ── CoinDCXPanel ────────────────────────────────────────────────────────────
type CdcxState = Awaited<ReturnType<typeof api.coindcxStatus>> | null;

function CoinDCXPanel({ cdcx, onReload }: { cdcx: CdcxState; onReload: () => Promise<void> }) {
  if (!cdcx || !cdcx.connected) {
    return (
      <div className="card mb-6 flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center border-blue-500/30">
        <div>
          <p className="font-semibold text-white">CoinDCX (Crypto Futures &amp; Spot)</p>
          <p className="text-sm text-muted">
            Automate Crypto Futures trading 24/7 with 10x volume spike strategy copying.
          </p>
        </div>
        <Link href="/connect/coindcx" className="btn-gold text-sm whitespace-nowrap">
          Connect CoinDCX →
        </Link>
      </div>
    );
  }

  return (
    <div className="card mb-6 p-5 border-blue-500/40">
      <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
        <div className="flex items-center gap-3">
          <span className={`h-2.5 w-2.5 rounded-full ${!cdcx.paused ? "bg-gain" : "bg-gold-500"}`} />
          <div>
            <p className="font-semibold text-white">
              CoinDCX Connected — {!cdcx.paused ? "Live 24/7 Copying Active" : "Copying Paused"}
            </p>
            <p className="text-xs text-muted font-mono mt-0.5">
              API Key: {cdcx.api_key} {cdcx.balance_usdt !== undefined ? `· Balance: $${cdcx.balance_usdt.toFixed(2)} USDT` : ""}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn-ghost text-sm"
            onClick={async () => {
              await api.coindcxPause();
              await onReload();
            }}
          >
            {cdcx.paused ? "Resume Copying" : "Pause Copying"}
          </button>
          <button
            className="btn-ghost text-sm"
            onClick={async () => {
              await api.coindcxDisconnect();
              await onReload();
            }}
          >
            Disconnect
          </button>
        </div>
      </div>
    </div>
  );
}
