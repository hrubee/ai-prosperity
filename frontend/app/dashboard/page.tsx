"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, clearToken, isAuthed, type Me, type OrderRow, type Position } from "@/lib/api";

export default function Dashboard() {
  const [me, setMe] = useState<Me | null>(null);
  const [equity, setEquity] = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [pausing, setPausing] = useState(false);
  const [tj, setTj] = useState<Awaited<ReturnType<typeof api.myTradejini>> | null>(null);
  const [notice, setNotice] = useState("");
  // Payment flow state
  const [paymentLoading, setPaymentLoading] = useState(false);
  const [qrCode, setQrCode] = useState<{ qr_base64: string; amount_inr: number; name: string; upi_id: string } | null>(null);
  const [screenshotPreview, setScreenshotPreview] = useState<string | null>(null);
  const [screenshotFile, setScreenshotFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [paymentStatus, setPaymentStatus] = useState<{ payment_status: string; screenshot: any } | null>(null);
  const [plans, setPlans] = useState<Record<string, {name: string; price_inr: number; months: number}>>({});

  async function load() {
    try {
      const m = await api.me();
      setMe(m);
      if (m.connection?.status === "connected") {
        const [p, o] = await Promise.all([api.myPositions(), api.myOrders()]);
        setEquity(p.equity);
        setPositions(p.positions || []);
        setOrders(o || []);
      }
    } catch {
      // unauthorized / backend down — fall through to the connect prompt
    }
    try {
      setTj(await api.myTradejini());
    } catch {
      /* no tradejini connection */
    } finally {
      setLoading(false);
    }
  }

  async function loadPaymentStatus() {
    try {
      const ps = await api.paymentStatus();
      setPaymentStatus(ps);
      if (ps.screenshot?.status === "pending" || ps.screenshot?.status === "approved") {
        // Show the existing screenshot if there is one
        setScreenshotPreview(`data:${ps.screenshot.mime_type};base64,${ps.screenshot.image_b64 || ""}`);
      }
    } catch {
      // ignore
    }
  }

  async function loadQRCode() {
    try {
      const qr = await api.getQRCode();
      setQrCode(qr);
    } catch {
      setNotice("UPI QR code not configured. Please contact support.");
    }
  }

  async function loadPlans() {
    try {
      const p = await api.getPlans();
      setPlans(p);
    } catch {
      // ignore
    }
  }

  // Determine plan-specific amount for display
  const planFromUrl = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("plan") : null;
  const displayAmount = planFromUrl && plans[planFromUrl] ? plans[planFromUrl].price_inr : (qrCode?.amount_inr || 5000);

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    const p = new URLSearchParams(window.location.search).get("tradejini");
    if (p === "connected") setNotice("Tradejini account connected ✓");
    else if (p) setNotice("Tradejini connection failed — please try again.");
    load();
    loadPaymentStatus();
    loadQRCode();
    loadPlans();
  }, []);

  const connOk = me?.connection?.status === "connected" && !me?.connection?.paused;
  const paymentApproved = me?.payment_status === "approved" || paymentStatus?.payment_status === "approved";

  async function togglePause() {
    if (!me?.connection) return;
    setPausing(true);
    try {
      await api.pause(!me.connection.paused);
      await load();
    } finally {
      setPausing(false);
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setNotice("Please select an image file (PNG/JPEG)");
      return;
    }
    if (file.size > 500_000) {
      setNotice("File too large (max 500KB)");
      return;
    }
    setScreenshotFile(file);
    const reader = new FileReader();
    reader.onload = (ev) => setScreenshotPreview(ev.target?.result as string);
    reader.readAsDataURL(file);
  }

  async function handleUpload() {
    if (!screenshotFile) return;
    setUploading(true);
    setNotice("");
    try {
      const base64 = screenshotPreview?.split(",")[1] || "";
      await api.uploadScreenshot(base64, screenshotFile.type);
      setNotice("Screenshot uploaded — awaiting admin approval");
      await loadPaymentStatus();
    } catch (e: any) {
      setNotice(e.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteScreenshot() {
    setPaymentLoading(true);
    try {
      await api.deleteScreenshot();
      setScreenshotPreview(null);
      setScreenshotFile(null);
      setNotice("Screenshot deleted");
      await loadPaymentStatus();
    } catch (e: any) {
      setNotice(e.message || "Delete failed");
    } finally {
      setPaymentLoading(false);
    }
  }

  async function confirmPlan() {
    if (!planFromUrl || !plans[planFromUrl]) return;
    setPaymentLoading(true);
    try {
      await api.checkout(planFromUrl);
      setNotice(`Subscribed to ${plans[planFromUrl].name} plan! Please upload your payment proof.`);
      await load(); // refresh me to update pending subscription package
    } catch (e: any) {
      setNotice(e.message || "Could not select plan");
    } finally {
      setPaymentLoading(false);
    }
  }

  return (
    <main className="min-h-screen">
      <header className="border-b border-ink-800 bg-ink-950/70">
        <div className="container-x flex h-16 items-center justify-between">
          <Logo />
          <div className="flex items-center gap-3 text-sm">
            {me?.subscription && <span className="pill capitalize">{plans[me.subscription.package]?.name || me.subscription.package} plan</span>}
            <span className={`pill ${paymentApproved ? "bg-gain/20 text-gain" : "bg-loss/20 text-loss"}`}>
              {paymentApproved ? "Payment Approved" : "Payment Pending"}
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

            {/* Payment Flow — QR Code + Screenshot Upload */}
            {!paymentApproved && (
              <div className="card mb-6 p-5">
                <h2 className="mb-4 font-semibold text-white">Complete Payment to Activate</h2>
                <p className="mb-4 text-sm text-muted">
                  Scan the UPI QR code below to pay <b>₹{displayAmount.toLocaleString()}</b> for your plan.
                  After payment, upload a screenshot of the transaction for admin verification.
                </p>

                {me?.subscription?.package !== planFromUrl && planFromUrl && plans[planFromUrl] && (
                  <div className="mb-6 flex justify-center">
                    <button className="btn-gold" onClick={confirmPlan} disabled={paymentLoading}>
                      Confirm selection: {plans[planFromUrl].name} (₹{plans[planFromUrl].price_inr})
                    </button>
                  </div>
                )}

                {/* QR Code Display */}
                {qrCode && (
                  <div className="mb-6 flex flex-col items-center gap-3">
                    <div className="rounded-xl border border-ink-700 bg-ink-800/40 p-4">
                      <img
                        src={`data:image/png;base64,${qrCode.qr_base64}`}
                        alt="UPI QR Code"
                        className="mx-auto max-w-xs"
                      />
                    </div>
                    <div className="text-center text-sm text-muted space-y-1">
                      <p>Pay to: <span className="text-white font-mono">{qrCode.name}</span></p>
                      <p>UPI ID: <span className="text-white font-mono">{qrCode.upi_id}</span></p>
                      <p>Amount: <span className="text-white font-bold">₹{displayAmount.toLocaleString()}</span></p>
                    </div>
                  </div>
                )}

                {/* Screenshot Upload */}
                <div className="space-y-4">
                  <h3 className="text-lg font-medium text-white">Payment Proof</h3>
                  {paymentStatus?.payment_status === "rejected" && (
                    <div className="rounded-lg border border-loss/40 bg-loss/10 p-3 text-sm text-loss">
                      Your payment was <b>rejected</b>. {paymentStatus.screenshot?.review_note && `Reason: ${paymentStatus.screenshot.review_note}`}
                      Please upload a new screenshot.
                    </div>
                  )}
                  {paymentStatus?.payment_status === "approved" && (
                    <div className="rounded-lg border border-gain/40 bg-gain/10 p-3 text-sm text-gain">
                      Payment approved! Redirecting to dashboard…
                    </div>
                  )}

                  {paymentStatus?.screenshot?.status === "pending" && (
                    <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
                      Screenshot uploaded — awaiting admin review
                    </div>
                  )}

                  {paymentStatus?.screenshot?.status === "approved" && (
                    <div className="rounded-lg border border-gain/40 bg-gain/10 p-3 text-sm text-gain">
                      Payment approved!
                    </div>
                  )}

                  {!paymentStatus?.screenshot || paymentStatus.screenshot.status !== "approved" ? (
                    <div className="space-y-3">
                      {screenshotPreview ? (
                        <div className="relative">
                          <img
                            src={screenshotPreview}
                            alt="Payment screenshot preview"
                            className="max-w-md rounded-lg border border-ink-700"
                          />
                          <div className="mt-2 flex gap-2">
                            <button
                              className="btn-ghost text-sm"
                              onClick={handleDeleteScreenshot}
                              disabled={uploading || paymentLoading}
                            >
                              Delete & Re-upload
                            </button>
                            <button
                              className="btn-gold text-sm"
                              onClick={handleUpload}
                              disabled={uploading || !screenshotFile}
                            >
                              {uploading ? "Uploading…" : "Upload Screenshot"}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="space-y-3">
                          <input
                            type="file"
                            accept="image/png,image/jpeg"
                            onChange={handleFileChange}
                            className="input"
                            disabled={uploading}
                          />
                          <button
                            className="btn-gold w-full"
                            onClick={handleUpload}
                            disabled={uploading || !screenshotFile}
                          >
                            {uploading ? "Uploading…" : "Upload Screenshot"}
                          </button>
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            )}

            {/* Tradejini (Indian F&O) connection */}
            {paymentApproved && <TradejiniPanel tj={tj} waitingForConnection={me?.subscription?.status === "approved_waiting_connection"} onReload={load} />}



            {/* Stat cards */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                ["Equity", `$${equity.toFixed(2)}`],
                ["Risk / trade", "2%"],
                ["Open positions", String(positions.length)],
                ["Subscription", me?.subscription?.status ?? "none"],
              ].map(([k, v]) => (
                <div key={k} className="card p-5">
                  <p className="text-xs uppercase tracking-wider text-muted">{k}</p>
                  <p className="mt-1 text-lg font-semibold capitalize text-white">{v}</p>
                </div>
              ))}
            </div>

            {paymentApproved && (
              <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
                {/* Positions */}
                <div className="card p-5 lg:col-span-2">
                  <h2 className="mb-4 font-semibold text-white">Open positions</h2>
                  {positions.length === 0 ? (
                    <p className="py-6 text-center text-sm text-muted">No open positions.</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="text-left text-xs uppercase tracking-wider text-muted">
                          <tr>
                            <th className="pb-2">Symbol</th>
                            <th className="pb-2">Side</th>
                            <th className="pb-2">Size</th>
                            <th className="pb-2">Entry</th>
                          </tr>
                        </thead>
                        <tbody className="text-slate-300">
                          {positions.map((p) => (
                            <tr key={p.symbol} className="border-t border-ink-800">
                              <td className="py-2.5 font-medium text-white">{p.base}</td>
                              <td className={p.side === "long" ? "text-gain" : "text-loss"}>
                                {p.side.toUpperCase()}
                              </td>
                              <td>{p.coin_size}</td>
                              <td className="font-mono">{p.entry.toLocaleString()}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* Signal / order feed */}
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
// Inline Tradejini status panel rendered in the dashboard.
// Two states: not yet connected (connected_once false/absent) → Link to connect flow;
// connected_once true → status card with optional error + Pause/Disconnect.

type TjState = Awaited<ReturnType<typeof api.myTradejini>> | null;

function TradejiniPanel({ tj, waitingForConnection, onReload }: { tj: TjState; waitingForConnection: boolean; onReload: () => Promise<void> }) {
  // Not yet set up — no credentials stored
  if (!tj || !tj.connected_once) {
    return (
      <div className={`card mb-6 flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center ${waitingForConnection ? "border-gold-500/60 shadow-glow bg-ink-800/40" : ""}`}>
        <p className="text-sm text-muted">
          {waitingForConnection ? (
            <><b className="text-gold-400">Action Required:</b> Connect your Tradejini account to activate your subscription and start receiving trades.</>
          ) : (
            <><b className="text-slate-200">Account not connected.</b> Connect your Tradejini account to start receiving trades.</>
          )}
        </p>
        <Link href="/connect/tradejini" className={`text-sm ${waitingForConnection ? "btn-gold" : "btn-ghost"}`}>
          Connect Tradejini →
        </Link>
      </div>
    );
  }

  // Credentials stored — show live status (auto-renew handles daily re-auth)
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
              Margin ₹{(tj.equity_inr ?? 0).toLocaleString("en-IN")} ·{" "}
              {tj.positions?.length ?? 0} positions
              {tj.expires_at
                ? ` · session ends ${new Date(tj.expires_at).toLocaleTimeString([], {
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
