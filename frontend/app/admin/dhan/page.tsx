"use client";

import { useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { AdminNav } from "@/components/AdminNav";
import { api } from "@/lib/api";

const steps = ["Generate API Keys", "Setup TOTP", "Connect"] as const;

export default function DhanConnectPage() {
  const [step, setStep] = useState(0);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [totpSeed, setTotpSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");

  async function submitConnect(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setSubmitting(true);
    try {
      await api.dhanConnect(apiKey, apiSecret, totpSeed);
      setErr("");
      setApiKey("");
      setApiSecret("");
      setTotpSeed("");
      alert("Dhan Master account added successfully.");
      window.location.href = "/admin";
    } catch (e: any) {
      setErr(e.message || "Could not connect — check your API Key, Secret, and TOTP Seed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-ink-950">
      <AdminNav />
      <div className="px-5 py-10">
        <div className="mx-auto max-w-lg">

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
              <h1 className="text-xl font-semibold text-white">Generate DhanHQ API Keys</h1>
              <p className="text-sm text-muted">
                Dhan Master accounts are used to monitor and fetch trades for the copier.
                You must generate an API Key and Secret from the Dhan web platform.
              </p>
              <ul className="space-y-2 rounded-xl border border-ink-700 bg-ink-800/40 p-4 text-sm text-slate-300">
                <li>✓ Log in to <a href="https://web.dhan.co/" target="_blank" rel="noreferrer" className="text-gold-400 underline">Dhan Web</a></li>
                <li>✓ Go to <b>My Profile &gt; Access DhanHQ APIs</b></li>
                <li>✓ Toggle to <b>API Key</b> mode</li>
                <li>✓ Enter an App Name and generate the API Key</li>
                <li>✓ Copy the <b>API Key</b> and <b>API Secret</b></li>
              </ul>
              <button className="btn-gold w-full" onClick={() => setStep(1)}>
                I have my API Key & Secret
              </button>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <h1 className="text-xl font-semibold text-white">Set up TOTP</h1>
              <p className="text-sm text-muted">
                Dhan APIs require TOTP (Time-based One Time Password) for automated daily authentication.
              </p>
              <ul className="space-y-2 rounded-xl border border-ink-700 bg-ink-800/40 p-4 text-sm text-slate-300">
                <li>✓ On the Dhan API page, find the <b>Setup TOTP</b> section</li>
                <li>✓ Verify with the OTP sent to your phone/email</li>
                <li>✓ You will see a QR code. Look for the text code / Secret Key below it (e.g. SPRXFSV...)</li>
                <li>✓ Copy that <b>Secret Key (TOTP Seed)</b></li>
              </ul>
              <button className="btn-gold w-full" onClick={() => setStep(2)}>
                I have my TOTP Seed
              </button>
              <button className="btn-ghost w-full" onClick={() => setStep(0)}>
                Back
              </button>
            </div>
          )}

          {step === 2 && (
            <form className="space-y-4" onSubmit={submitConnect}>
              <div>
                <h1 className="text-xl font-semibold text-white">
                  Connect Dhan Master Account
                </h1>
                <p className="mt-2 text-sm text-muted">
                  Paste your Dhan API Key, Secret, and TOTP Seed. We encrypt these securely
                  and use them to mint daily tokens for monitoring trades.
                </p>
              </div>

              {err && (
                <p className="rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
                  {err}
                </p>
              )}

              <div>
                <label className="label" htmlFor="dhan-api-key">
                  API Key
                </label>
                <input
                  id="dhan-api-key"
                  className="input font-mono"
                  required
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="e.g. 57dc84cd"
                  autoComplete="off"
                />
              </div>

              <div>
                <label className="label" htmlFor="dhan-api-secret">
                  API Secret
                </label>
                <input
                  id="dhan-api-secret"
                  type="password"
                  className="input font-mono"
                  required
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="e.g. 7ed484f5-a6ae-409a-..."
                  autoComplete="off"
                />
              </div>

              <div>
                <label className="label" htmlFor="dhan-totp">
                  TOTP Seed (Secret)
                </label>
                <input
                  id="dhan-totp"
                  type="password"
                  className="input font-mono"
                  required
                  value={totpSeed}
                  onChange={(e) => setTotpSeed(e.target.value)}
                  placeholder="e.g. SPRXFSV6M5Z..."
                  autoComplete="off"
                />
              </div>

              <p className="text-xs text-muted">
                Your credentials are encrypted at rest using AES-256 (Fernet). We never log them in plaintext.
              </p>

              <button type="submit" className="btn-gold w-full" disabled={submitting}>
                {submitting ? "Connecting…" : "Connect Master Account"}
              </button>
              <button type="button" className="btn-ghost w-full" onClick={() => setStep(1)}>
                Back
              </button>
            </form>
          )}
        </div>

        <p className="mt-5 text-center text-xs text-muted">
          Need help?{" "}
          <Link href="/admin" className="text-gold-400 underline">
            Cancel
          </Link>
        </p>
      </div>
      </div>
    </main>
  );
}
