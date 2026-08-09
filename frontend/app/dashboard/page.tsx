"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, clearToken, isAuthed, type Me, type OrderRow } from "@/lib/api";

export default function Dashboard() {
  const [me, setMe] = useState<Me | null>(null);
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

  async function load() {
    try {
      const m = await api.me();
      setMe(m);
      if (m.connection?.status === "connected") {
        const o = await api.myOrders();
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

  // Determine plan-specific amount for display
  const [selectedPlan, setSelectedPlan] = useState<number>(1);
  const planPrices: Record<number, number> = {
    1: 5000,
    3: 13500,
    6: 24000,
    12: 38400,
  };
  const displayAmount = planPrices[selectedPlan];

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

  return (
    <main className="min-h-screen">
      <header className="border-b border-ink-800 bg-ink-950/70">
        <div className="container-x flex min-h-[4rem] flex-wrap items-center justify-between gap-4 py-3">
          <Logo />
          <div className="flex flex-wrap items-center gap-2 sm:gap-3 text-sm">
            {me?.subscription && <span className="pill capitalize">{me.subscription.package} plan</span>}
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
                  Select your subscription plan, scan the UPI QR code below, and pay the exact amount shown.
                  After payment, upload a screenshot of the transaction for admin verification.
                </p>

                <div className="mb-6">
                  <label className="mb-2 block text-sm font-medium text-white">Select Plan</label>
                  <select 
                    className="input w-full max-w-xs"
                    value={selectedPlan}
                    onChange={(e) => setSelectedPlan(Number(e.target.value))}
                  >
                    <option value={1}>1 Month - ₹5,000</option>
                    <option value={3}>3 Months - ₹13,500</option>
                    <option value={6}>6 Months - ₹24,000</option>
                    <option value={12}>12 Months - ₹38,400</option>
                  </select>
                </div>

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
            {paymentApproved && <TradejiniPanel tj={tj} onReload={load} />}





            {paymentApproved && (
              <div className="mt-6 max-w-3xl">
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

function TradejiniPanel({ tj, onReload }: { tj: TjState; onReload: () => Promise<void> }) {
  // Not yet set up — no credentials stored
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
