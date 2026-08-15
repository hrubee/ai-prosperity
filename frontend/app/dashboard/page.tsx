"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, clearToken, isAuthed, type Me, type OrderRow } from "@/lib/api";
import { PROFIT_SPLIT_MODEL } from "@/lib/packages";

export default function Dashboard() {
  const [me, setMe] = useState<Me | null>(null);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [tj, setTj] = useState<Awaited<ReturnType<typeof api.myTradejini>> | null>(null);
  const [cdcx, setCdcx] = useState<Awaited<ReturnType<typeof api.coindcxStatus>> | null>(null);
  const [notice, setNotice] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  async function handleRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

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
                <h2 className="text-2xl font-bold text-white mb-2">Account Awaiting Admin Approval</h2>
                <p className="text-sm text-muted leading-relaxed max-w-lg mx-auto mb-6">
                  Welcome <b className="text-white">{me?.name || me?.email}</b>! Your registration details and Tradejini Client ID (
                  <span className="font-mono text-gold-400 font-semibold">{me?.client_id || "Not set"}</span>) have been received.
                  An administrator is reviewing your request. Once approved, your trade automation features will unlock automatically.
                </p>
                <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                  <div className="inline-flex items-center gap-2 rounded-full border border-gold-500/30 bg-gold-500/10 px-4 py-2 text-xs text-gold-400 font-medium">
                    <span className="h-2 w-2 rounded-full bg-gold-400 animate-pulse" />
                    Status: Pending Admin Approval
                  </div>
                  <button
                    onClick={handleRefresh}
                    disabled={refreshing}
                    className="btn-gold text-xs px-5 py-2 inline-flex items-center gap-2 font-semibold shadow-lg shadow-gold-500/10"
                  >
                    <span className={refreshing ? "animate-spin" : ""}>🔄</span>
                    {refreshing ? "Checking Status…" : "Refresh Approval Status"}
                  </button>
                </div>
              </div>
            )}

            {/* 60-40 Profit Share Model Card */}
            {isApproved && (
              <div className="card mb-6 p-5 border-gold-500/30 bg-ink-900/60">
                <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                  <div>
                    <span className="pill text-gold-400 border-gold-500/40 mb-2">
                      Performance Model
                    </span>
                    <h3 className="text-lg font-bold text-white mt-1">60% Client Share · 40% Platform Share</h3>
                    <p className="text-xs text-muted mt-0.5">
                      Zero monthly subscription fees. Pay 40% performance share only when net profitable.
                    </p>
                  </div>
                  <div className="flex items-center gap-4 text-right">
                    <div>
                      <p className="text-xs text-muted uppercase tracking-wider">Your Profit Share</p>
                      <p className="text-xl font-bold text-gain">60%</p>
                    </div>
                    <div className="h-8 w-px bg-ink-800" />
                    <div>
                      <p className="text-xs text-muted uppercase tracking-wider">Platform Share</p>
                      <p className="text-xl font-bold text-gold-400">40%</p>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Exchange Connections Section (Visually Identical Layout) */}
            {isApproved && (
              <div className="space-y-4">
                <h2 className="text-sm font-semibold text-muted uppercase tracking-wider">Connected Exchanges</h2>
                
                {/* Tradejini (Indian F&O) connection */}
                <TradejiniPanel tj={tj} onReload={load} />

                {/* CoinDCX (Crypto Futures & Spot) connection — COMING SOON */}
                <CoinDCXPanel cdcx={cdcx} onReload={load} />
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
      <div className="card flex flex-col items-start justify-between gap-4 p-5 sm:flex-row sm:items-center hover:border-ink-600 transition-colors">
        <div className="flex items-center gap-3">
          <span className="h-3 w-3 rounded-full bg-ink-600" />
          <div>
            <div className="flex items-center gap-2">
              <p className="font-semibold text-white">Tradejini (Indian F&amp;O)</p>
              <span className="pill text-xs">Live Platform</span>
            </div>
            <p className="text-sm text-muted mt-0.5">
              Automate NIFTY &amp; BankNIFTY options copy trading.
            </p>
          </div>
        </div>
        <Link href="/connect/tradejini" className="btn-gold text-sm whitespace-nowrap">
          Connect Tradejini →
        </Link>
      </div>
    );
  }

  return (
    <div className={`card p-5 ${tj.error ? "border-loss/60" : ""}`}>
      <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
        <div className="flex items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${tj.connected ? "bg-gain" : "bg-loss"}`} />
          <div>
            <p className="font-semibold text-white">
              {tj.connected ? "Tradejini Connected — Indian F&O Live" : "Tradejini Auto-Renewing…"}
            </p>
            <p className="text-sm text-muted mt-0.5">
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

// ── CoinDCXPanel (Visually Identical to Tradejini, Marked COMING SOON) ─────
type CdcxState = Awaited<ReturnType<typeof api.coindcxStatus>> | null;

function CoinDCXPanel({ cdcx, onReload }: { cdcx: CdcxState; onReload: () => Promise<void> }) {
  return (
    <div className="card flex flex-col items-start justify-between gap-4 p-5 sm:flex-row sm:items-center opacity-85 border-ink-800 bg-ink-900/40">
      <div className="flex items-center gap-3">
        <span className="h-3 w-3 rounded-full bg-gold-500/60" />
        <div>
          <div className="flex items-center gap-2">
            <p className="font-semibold text-white">CoinDCX (Crypto Futures &amp; Spot)</p>
            <span className="pill border-gold-500/40 bg-gold-500/10 text-gold-400 text-xs font-semibold">
              Coming Soon
            </span>
          </div>
          <p className="text-sm text-muted mt-0.5">
            Automate Crypto Futures trading 24/7 with 30x volume spike strategy copying.
          </p>
        </div>
      </div>
      
      {/* Disabled / Non-clickable button specifying Coming Soon */}
      <button
        disabled
        className="btn-ghost text-sm whitespace-nowrap opacity-50 cursor-not-allowed border-ink-700 text-muted"
        title="CoinDCX integration is coming soon"
      >
        Connect CoinDCX (Coming Soon)
      </button>
    </div>
  );
}
