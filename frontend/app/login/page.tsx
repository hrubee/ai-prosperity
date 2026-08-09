"use client";

import { useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const plan =
    typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("plan") : null;
  const planQS = plan ? `?plan=${plan}` : "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const r = await api.login(identifier, password);
      setToken(r.token);
      // Redirect to dashboard with plan so it can show the right QR amount
      window.location.href = `/dashboard${plan ? `?plan=${plan}` : ""}`;
    } catch (e: any) {
      setErr(e.message || "Could not log in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center px-5">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>
        <div className="card p-7">
          <h1 className="text-xl font-semibold text-white">Welcome back</h1>
          <p className="mt-1 text-sm text-muted">Log in with your email or phone and password.</p>

          {err && (
            <p className="mt-4 rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">{err}</p>
          )}

          <form className="mt-6 space-y-4" onSubmit={submit}>
            <div>
              <label className="label" htmlFor="identifier">Email or phone</label>
              <input id="identifier" type="text" required autoComplete="username" className="input"
                placeholder="you@example.com or +91…" value={identifier}
                onChange={(e) => setIdentifier(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="password">Password</label>
              <input id="password" type="password" required autoComplete="current-password" className="input"
                placeholder="Your password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <button type="submit" className="btn-gold w-full" disabled={busy}>
              {busy ? "Logging in…" : "Log in"}
            </button>
          </form>

          <p className="mt-5 text-center text-sm text-muted">
            New here?{" "}
            <Link href={`/signup${planQS}`} className="text-gold-400 hover:underline">Create an account</Link>
          </p>
        </div>
        <p className="mt-5 text-center text-xs text-muted">
          By continuing you agree to our Terms &amp; Risk Disclosure.
        </p>
      </div>
    </main>
  );
}
