"use client";

import { useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api } from "@/lib/api";

// The static egress IP clients must whitelist in their Tradejini app.
const STATIC_EGRESS_IP = "187.127.132.39";

const steps = ["Create app", "Whitelist IP", "Connect"] as const;

export default function TradejiniConnectPage() {
  const [step, setStep] = useState(0);
  const [apiKey, setApiKey] = useState("");
  const [password, setPassword] = useState("");
  const [totpSeed, setTotpSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");

  async function submitConnect(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setSubmitting(true);
    try {
      await api.tradejiniConnect(apiKey, password, totpSeed);
      window.location.href = "/dashboard?tradejini=connected";
    } catch (e: any) {
      setErr(e.message || "Could not connect — check your API key, login PIN, and TOTP seed");
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

        {/* Prominent Static IP Banner */}
        <div className="mb-6 rounded-2xl border border-gold-500/40 bg-gold-500/10 p-4 text-center">
          <p className="text-xs uppercase tracking-wider text-gold-400 font-semibold mb-1">
            🌐 Tradejini Server Static IP (Egress)
          </p>
          <div className="flex items-center justify-center gap-3 mt-1">
            <code className="font-mono text-xl font-bold text-white tracking-wider">{STATIC_EGRESS_IP}</code>
            <CopyButton text={STATIC_EGRESS_IP} />
          </div>
          <p className="text-xs text-muted mt-2">
            Whitelist this IP in your Tradejini App developer portal settings.
          </p>
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
              <h1 className="text-xl font-semibold text-white">Create your Tradejini app</h1>
              <p className="text-sm text-muted">
                Tradejini requires each client to create their own app on the developer portal. Log
                in and create a new app to get your API key.
              </p>
              <p className="text-sm text-muted">
                Open the{" "}
                <a
                  href="https://developer.tradejini.com/dashboard"
                  target="_blank"
                  rel="noreferrer"
                  className="text-gold-400 underline"
                >
                  Tradejini developer portal
                </a>{" "}
                and create a new app. Keep the <b className="text-slate-200">API key</b> it
                generates — you&apos;ll need it in a moment.
              </p>
              <ul className="space-y-2 rounded-xl border border-ink-700 bg-ink-800/40 p-4 text-sm text-slate-300">
                <li>✓ Log in with your Tradejini trading account</li>
                <li>✓ Create a new app (any name)</li>
                <li>✓ Copy the <b>API key</b> shown after creation</li>
              </ul>
              <button className="btn-gold w-full" onClick={() => setStep(1)}>
                I&apos;ve created the app
              </button>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <h1 className="text-xl font-semibold text-white">Whitelist our server IP</h1>
              <p className="text-sm text-muted">
                Tradejini apps only accept requests from whitelisted IPs. Add the following IP as
                your app&apos;s <b className="text-slate-200">Static IP</b> so our server can place
                trades on your behalf:
              </p>
              <div className="flex items-center justify-between rounded-xl border border-gold-500/40 bg-ink-800/60 px-4 py-3">
                <code className="font-mono text-lg text-gold-400">{STATIC_EGRESS_IP}</code>
                <CopyButton text={STATIC_EGRESS_IP} />
              </div>
              <p className="text-xs text-muted">
                This is the only IP that can use your key. A stolen key is useless from anywhere
                else.
              </p>
              <div className="flex gap-3">
                <button className="btn-ghost flex-1" onClick={() => setStep(0)}>
                  Back
                </button>
                <button className="btn-gold flex-1" onClick={() => setStep(2)}>
                  IP whitelisted
                </button>
              </div>
            </div>
          )}

          {step === 2 && (
            <form className="space-y-4" onSubmit={submitConnect}>
              <div>
                <h1 className="text-xl font-semibold text-white">
                  Connect once — we handle the rest
                </h1>
                <p className="mt-2 text-sm text-muted">
                  Paste your Tradejini API key, login PIN, and your authenticator&apos;s
                  TOTP seed (the base32 &ldquo;manual entry&rdquo; / &ldquo;setup key&rdquo; shown
                  when you set up your authenticator app — <b className="text-slate-200">not</b>{" "}
                  the 6-digit code). We encrypt all three and auto-renew your daily Tradejini
                  session for you —{" "}
                  <b className="text-slate-200">you never have to reconnect.</b>
                </p>
              </div>

              {err && (
                <p className="rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
                  {err}
                </p>
              )}

              <div>
                <label className="label" htmlFor="tj-api-key">
                  API Key
                </label>
                <input
                  id="tj-api-key"
                  className="input font-mono"
                  required
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="xxxxxxxxxxxxxxxx"
                  autoComplete="off"
                />
              </div>

              <div>
                <label className="label" htmlFor="tj-password">
                  Login PIN
                </label>
                <input
                  id="tj-password"
                  type="password"
                  inputMode="numeric"
                  className="input"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="your Tradejini login PIN"
                  autoComplete="off"
                />
                <p className="mt-1 text-xs text-muted">
                  The PIN you enter to log in to Tradejini (not the 6-digit code).
                </p>
              </div>

              <div>
                <label className="label" htmlFor="tj-totp-seed">
                  TOTP Seed
                </label>
                <input
                  id="tj-totp-seed"
                  type="password"
                  className="input font-mono"
                  required
                  value={totpSeed}
                  onChange={(e) => setTotpSeed(e.target.value)}
                  placeholder="base32 secret e.g. JBSWY3DPEHPK3PXP"
                  autoComplete="off"
                />
              </div>

              <p className="text-xs text-muted">
                Your credentials are sent only to Tradejini to mint your session and are encrypted
                at rest.
              </p>

              <button type="submit" className="btn-gold w-full" disabled={submitting}>
                {submitting ? "Connecting…" : "Connect Tradejini"}
              </button>
              <button type="button" className="btn-ghost w-full" onClick={() => setStep(1)}>
                Back
              </button>
            </form>
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

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn-ghost !px-3 !py-1.5 text-xs"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
