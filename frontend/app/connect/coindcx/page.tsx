"use client";

import { useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api } from "@/lib/api";

const steps = ["Generate API Key", "Enter Credentials", "Connected"] as const;

export default function CoinDCXConnectPage() {
  const [step, setStep] = useState(0);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");
  const [balance, setBalance] = useState<number | null>(null);

  async function submitConnect(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setSubmitting(true);
    try {
      const res = await api.coindcxConnect(apiKey, apiSecret);
      setBalance(res.balance_usdt);
      setStep(2);
    } catch (e: any) {
      setErr(e.message || "Authentication failed — please verify your CoinDCX API Key & Secret.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen px-5 py-10">
      <div className="mx-auto max-w-lg">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>

        {/* Stepper */}
        <ol className="mb-8 flex items-center justify-between text-xs">
          {steps.map((s, i) => (
            <li key={s} className="flex flex-1 items-center">
              <span
                className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border text-[11px] font-semibold ${
                  i <= step
                    ? "border-gold-500 bg-gold-500 text-ink-950"
                    : "border-ink-600 text-muted"
                }`}
              >
                {i + 1}
              </span>
              <span className={`ml-2 ${i <= step ? "text-white" : "text-muted"}`}>{s}</span>
              {i < steps.length - 1 && <span className="mx-2 h-px flex-1 bg-ink-700" />}
            </li>
          ))}
        </ol>

        <div className="card p-7">
          {step === 0 && (
            <div className="space-y-4">
              <h1 className="text-xl font-semibold text-white">Create your CoinDCX API Key</h1>
              <p className="text-sm text-muted">
                To connect your CoinDCX account for 24/7 automated trade copying, generate an API Key &amp; API Secret on CoinDCX.
              </p>

              <div className="space-y-2 rounded-xl border border-ink-700 bg-ink-800/40 p-4 text-sm text-slate-300">
                <p className="font-semibold text-white">Instructions:</p>
                <ol className="list-decimal pl-4 space-y-1 text-xs text-muted leading-relaxed">
                  <li>Log in to your <a href="https://coindcx.com" target="_blank" rel="noreferrer" className="text-gold-400 underline">CoinDCX Account</a>.</li>
                  <li>Go to <b>Profile</b> → <b>API Key Management</b>.</li>
                  <li>Click <b>Create API Key</b> and check <b>Order Execution</b> &amp; <b>Balance Check</b> permissions.</li>
                  <li>Copy both the <b>API Key</b> and <b>API Secret</b>.</li>
                </ol>
              </div>

              <button className="btn-gold w-full" onClick={() => setStep(1)}>
                I&apos;ve got my API Key &amp; Secret →
              </button>
            </div>
          )}

          {step === 1 && (
            <form className="space-y-4" onSubmit={submitConnect}>
              <div>
                <h1 className="text-xl font-semibold text-white">Connect CoinDCX</h1>
                <p className="mt-1 text-sm text-muted">
                  Paste your CoinDCX API Key and Secret below. Credentials are encrypted at rest with AES-256 GCM and used strictly for strategy execution.
                </p>
              </div>

              {err && (
                <p className="rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
                  {err}
                </p>
              )}

              <div>
                <label className="label" htmlFor="cdcx-api-key">
                  API Key
                </label>
                <input
                  id="cdcx-api-key"
                  className="input font-mono"
                  required
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Enter CoinDCX API Key"
                  autoComplete="off"
                />
              </div>

              <div>
                <label className="label" htmlFor="cdcx-api-secret">
                  API Secret
                </label>
                <input
                  id="cdcx-api-secret"
                  type="password"
                  className="input font-mono"
                  required
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="Enter CoinDCX API Secret"
                  autoComplete="off"
                />
              </div>

              <div className="flex gap-3">
                <button type="button" className="btn-ghost flex-1" onClick={() => setStep(0)}>
                  Back
                </button>
                <button type="submit" className="btn-gold flex-1" disabled={submitting}>
                  {submitting ? "Validating & Connecting…" : "Connect Account"}
                </button>
              </div>
            </form>
          )}

          {step === 2 && (
            <div className="space-y-4 text-center">
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-gain/10 text-gain text-2xl">
                ✓
              </div>
              <h1 className="text-xl font-semibold text-white">CoinDCX Account Connected!</h1>
              <p className="text-sm text-muted">
                Your credentials were verified successfully against CoinDCX servers.
              </p>

              {balance !== null && (
                <div className="rounded-xl border border-gain/30 bg-gain/10 p-4">
                  <p className="text-xs text-muted uppercase tracking-wider">Available Wallet Balance</p>
                  <p className="text-2xl font-bold text-gain mt-1">${balance.toFixed(2)} USDT</p>
                </div>
              )}

              <Link href="/dashboard" className="btn-gold block w-full">
                Go to Dashboard →
              </Link>
            </div>
          )}
        </div>

        <p className="mt-5 text-center text-xs text-muted">
          Need help?{" "}
          <Link href="/dashboard" className="text-gold-400 underline">
            Skip for now
          </Link>
        </p>
      </div>
    </main>
  );
}
