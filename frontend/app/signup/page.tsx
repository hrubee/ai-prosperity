"use client";

import { useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, setToken } from "@/lib/api";

export default function SignupPage() {
  const [step, setStep] = useState<1 | 2>(1);

  // Step 1 fields
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");

  // Step 2 field
  const [clientId, setClientId] = useState("");

  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  function handleStep1Submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!name.trim() || !email.trim() || !phone.trim() || password.length < 8) {
      setErr("Please complete all fields with a valid password (at least 8 characters).");
      return;
    }
    // Proceed to Step 2
    setStep(2);
  }

  async function handleFinalSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!clientId.trim()) {
      setErr("Tradejini Client ID is required to submit for approval.");
      return;
    }

    setBusy(true);
    try {
      const r = await api.register(name, email, phone, password, clientId);
      setToken(r.token);
      // Registration successful -> redirect to dashboard (shows pending approval)
      window.location.href = "/dashboard";
    } catch (e: any) {
      setErr(e.message || "Could not create your account. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center px-5 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>

        <div className="card p-7">
          {/* Progress Indicator */}
          <div className="mb-6 flex items-center justify-between border-b border-ink-800 pb-4">
            <div className="flex items-center gap-2">
              <span className={`grid h-7 w-7 place-items-center rounded-full text-xs font-bold ${step === 1 ? "bg-gold-500 text-ink-950" : "bg-gain text-ink-950"}`}>
                {step === 1 ? "1" : "✓"}
              </span>
              <span className={`text-xs font-medium ${step === 1 ? "text-white" : "text-muted"}`}>
                Basic Details
              </span>
            </div>
            <span className="text-xs text-muted">────</span>
            <div className="flex items-center gap-2">
              <span className={`grid h-7 w-7 place-items-center rounded-full text-xs font-bold ${step === 2 ? "bg-gold-500 text-ink-950" : "bg-ink-800 text-muted"}`}>
                2
              </span>
              <span className={`text-xs font-medium ${step === 2 ? "text-white" : "text-muted"}`}>
                Tradejini Client ID
              </span>
            </div>
          </div>

          {err && (
            <p className="mb-4 rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">{err}</p>
          )}

          {/* STEP 1 FORM */}
          {step === 1 && (
            <div>
              <h1 className="text-xl font-semibold text-white">Create your account (Step 1 of 2)</h1>
              <p className="mt-1 text-sm text-muted">
                Enter your contact details to begin registration.
              </p>

              <form className="mt-6 space-y-4" onSubmit={handleStep1Submit}>
                <div>
                  <label className="label" htmlFor="name">Full name</label>
                  <input id="name" type="text" required autoComplete="name" className="input"
                    placeholder="Your full name" value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div>
                  <label className="label" htmlFor="email">Email address</label>
                  <input id="email" type="email" required autoComplete="email" className="input"
                    placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                <div>
                  <label className="label" htmlFor="phone">Phone number</label>
                  <input id="phone" type="tel" required autoComplete="tel" className="input"
                    placeholder="+91 98765 43210" value={phone} onChange={(e) => setPhone(e.target.value)} />
                </div>
                <div>
                  <label className="label" htmlFor="password">Password</label>
                  <input id="password" type="password" required minLength={8} autoComplete="new-password"
                    className="input" placeholder="At least 8 characters" value={password}
                    onChange={(e) => setPassword(e.target.value)} />
                </div>

                <button type="submit" className="btn-gold w-full mt-6">
                  Continue to Step 2 →
                </button>
              </form>
            </div>
          )}

          {/* STEP 2 FORM */}
          {step === 2 && (
            <div>
              <h1 className="text-xl font-semibold text-white">Tradejini Integration (Step 2 of 2)</h1>
              <p className="mt-1 text-sm text-muted">
                Provide your Tradejini Client ID for admin verification and approval.
              </p>

              <form className="mt-6 space-y-4" onSubmit={handleFinalSubmit}>
                <div className="rounded-xl border border-gold-500/20 bg-gold-500/5 p-4 mb-4">
                  <p className="text-xs text-gold-400 font-medium">
                    👤 Registering for: <span className="text-white font-semibold">{name}</span> ({email})
                  </p>
                </div>

                <div>
                  <label className="label" htmlFor="clientId">Tradejini Client ID</label>
                  <input id="clientId" type="text" required autoFocus className="input"
                    placeholder="e.g. 10be2d096436" value={clientId} onChange={(e) => setClientId(e.target.value)} />
                  <p className="mt-1.5 text-xs text-muted">
                    Your Tradejini F&amp;O client ID will be sent directly for admin review.
                  </p>
                </div>

                <div className="flex gap-3 pt-2">
                  <button type="button" className="btn-ghost w-1/3" onClick={() => setStep(1)} disabled={busy}>
                    ← Back
                  </button>
                  <button type="submit" className="btn-gold w-2/3" disabled={busy}>
                    {busy ? "Submitting…" : "Submit for Admin Approval"}
                  </button>
                </div>
              </form>
            </div>
          )}

          <p className="mt-6 text-center text-sm text-muted">
            Already registered?{" "}
            <Link href="/login" className="text-gold-400 hover:underline">Log in</Link>
          </p>
        </div>

        <p className="mt-5 text-center text-xs text-muted">
          By continuing you agree to our Terms &amp; Risk Disclosure.
        </p>
      </div>
    </main>
  );
}
