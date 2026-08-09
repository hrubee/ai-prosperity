"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { AdminNav } from "@/components/AdminNav";
import { api } from "@/lib/api";

export default function DhanConnectPage() {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");
  
  const [currentStatus, setCurrentStatus] = useState<{ is_valid: boolean; client_id?: string; access_token?: string } | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);

  useEffect(() => {
    api.dhanTokenStatus()
      .then((data) => {
        setCurrentStatus(data);
      })
      .catch((e) => {
        console.error("Failed to load Dhan token status:", e);
      })
      .finally(() => {
        setLoadingStatus(false);
      });
  }, []);

  async function submitConnect(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setSubmitting(true);
    try {
      await api.dhanConnect(apiKey, apiSecret, "");
      setErr("");
      setApiKey("");
      setApiSecret("");
      alert("Dhan Master account updated successfully.");
      window.location.href = "/admin";
    } catch (e: any) {
      setErr(e.message || "Could not connect — check your Client ID and Access Token");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-ink-950">
      <AdminNav />
      <div className="px-5 py-10">
        <div className="mx-auto max-w-lg">
        
        {!loadingStatus && currentStatus?.is_valid && (
          <div className="card p-7 mb-6 border border-gold-500/30 bg-gold-900/10">
            <h2 className="text-lg font-semibold text-gold-400 mb-4">Currently Connected Account</h2>
            <div className="space-y-3">
              <div>
                <p className="text-xs text-muted uppercase tracking-wider">Client ID</p>
                <p className="font-mono text-white text-sm bg-ink-900 p-2 rounded border border-ink-800 mt-1">{currentStatus.client_id}</p>
              </div>
              <div>
                <p className="text-xs text-muted uppercase tracking-wider">Access Token (24h)</p>
                <p className="font-mono text-white text-xs bg-ink-900 p-2 rounded border border-ink-800 mt-1 break-all">{currentStatus.access_token}</p>
              </div>
              <p className="text-xs text-green-400 mt-2">● Active and Polling</p>
            </div>
          </div>
        )}

        <div className="card p-7">
            <form className="space-y-4" onSubmit={submitConnect}>
              <div>
                <h1 className="text-xl font-semibold text-white">
                  {currentStatus?.is_valid ? "Update Master Account Token" : "Connect Dhan Master Account"}
                </h1>
                <p className="mt-2 text-sm text-muted">
                  Paste your Dhan Client ID and Access Token generated from DhanHQ. 
                  The Access Token is valid for 24 hours. Paste today's fresh token below.
                </p>
              </div>

              {err && (
                <p className="rounded-lg border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
                  {err}
                </p>
              )}

              <div>
                <label className="label" htmlFor="dhan-client-id">
                  Client ID
                </label>
                <input
                  id="dhan-client-id"
                  className="input font-mono"
                  required
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="e.g. 1000000000"
                  autoComplete="off"
                />
              </div>

              <div>
                <label className="label" htmlFor="dhan-access-token">
                  Access Token
                </label>
                <input
                  id="dhan-access-token"
                  type="password"
                  className="input font-mono"
                  required
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="Paste today's Access Token..."
                  autoComplete="off"
                />
              </div>

              <p className="text-xs text-muted">
                Your credentials are encrypted at rest using AES-256 (Fernet).
              </p>

              <button type="submit" className="btn-gold w-full" disabled={submitting}>
                {submitting ? "Connecting…" : (currentStatus?.is_valid ? "Update Token" : "Connect Master Account")}
              </button>
            </form>
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
