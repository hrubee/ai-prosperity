"use client";

import { useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, setToken } from "@/lib/api";

export default function SignupPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
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
      const r = await api.register(name, email, phone, password);
      setToken(r.token);
      // Redirect to dashboard with plan so it can show the right QR amount
      window.location.href = `/dashboard${plan ? `?plan=${plan}` : ""}`;
    } catch (e: any) {
      setErr(e.message || "Could not create your account");
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
          <h1 className="text-xl font-semibold text-white">Create your account</h1>
          <p className="mt-1 text-sm text-muted">
            {plan ? `Sign up to continue to your selected plan.` : "Just your details — no verification code needed."}
          </p>

          {err && (
            <p className="mt-4 rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">{err}</p>
          )}

          <form className="mt-6 space-y-4" onSubmit={submit}>
            <div>
              <label className="label" htmlFor="name">Full name</label>
              <input id="name" type="text" required autoComplete="name" className="input"
                placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="email">Email</label>
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
            <button type="submit" className="btn-gold w-full" disabled={busy}>
              {busy ? "Creating…" : "Create account"}
            </button>
          </form>

          <p className="mt-5 text-center text-sm text-muted">
            Already have an account?{" "}
            <Link href={`/login${planQS}`} className="text-gold-400 hover:underline">Log in</Link>
          </p>
        </div>
        <p className="mt-5 text-center text-xs text-muted">
          By continuing you agree to our Terms &amp; Risk Disclosure.
        </p>
      </div>
    </main>
  );
}
