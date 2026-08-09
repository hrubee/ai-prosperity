"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { api, isAuthed, type Me } from "@/lib/api";

export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    api.me().then(setMe).catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setOk("");
    if (next !== confirm) {
      setErr("New password and confirmation don't match.");
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(current, next);
      setOk("Password changed. Use it next time you log in.");
      setCurrent(""); setNext(""); setConfirm("");
    } catch (e: any) {
      setErr(e.message || "Could not change password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen">
      <header className="border-b border-ink-800 bg-ink-950/70">
        <div className="container-x flex min-h-[4rem] flex-wrap items-center justify-between gap-4 py-3">
          <Logo />
          <Link href="/dashboard" className="btn-ghost !px-3 !py-1.5 text-sm">← Dashboard</Link>
        </div>
      </header>

      <div className="container-x grid place-items-center py-10">
        <div className="w-full max-w-md">
          {me && (
            <div className="card mb-5 p-5">
              <p className="text-xs uppercase tracking-wider text-muted">Account</p>
              <p className="mt-1 font-medium text-white">{me.name || "—"}</p>
              <p className="text-sm text-muted">{me.email}</p>
              {me.phone && <p className="text-sm text-muted">{me.phone}</p>}
            </div>
          )}

          <div className="card p-7">
            <h1 className="text-lg font-semibold text-white">Change password</h1>
            <p className="mt-1 text-sm text-muted">Enter your current password, then a new one (8+ characters).</p>

            {err && <p className="mt-4 rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">{err}</p>}
            {ok && <p className="mt-4 rounded-lg border border-gain/40 bg-gain/10 px-3 py-2 text-sm text-gain">{ok}</p>}

            <form className="mt-6 space-y-4" onSubmit={submit}>
              <div>
                <label className="label" htmlFor="current">Current password</label>
                <input id="current" type="password" required autoComplete="current-password" className="input"
                  value={current} onChange={(e) => setCurrent(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="next">New password</label>
                <input id="next" type="password" required minLength={8} autoComplete="new-password" className="input"
                  placeholder="At least 8 characters" value={next} onChange={(e) => setNext(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="confirm">Confirm new password</label>
                <input id="confirm" type="password" required minLength={8} autoComplete="new-password" className="input"
                  value={confirm} onChange={(e) => setConfirm(e.target.value)} />
              </div>
              <button type="submit" className="btn-gold w-full" disabled={busy}>
                {busy ? "Saving…" : "Update password"}
              </button>
            </form>
          </div>
        </div>
      </div>
    </main>
  );
}
