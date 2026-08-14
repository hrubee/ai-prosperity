"use client";

import React, { useState } from "react";

interface CoinDCXConnectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: (usdtBalance: number) => void;
}

export default function CoinDCXConnectModal({ isOpen, onClose, onSuccess }: CoinDCXConnectModalProps) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccessMsg(null);

    try {
      const res = await fetch("/api/coindcx/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: apiKey.trim(),
          api_secret: apiSecret.trim(),
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to validate CoinDCX API credentials.");
      }

      setSuccessMsg(`Connected successfully! CoinDCX Balance: $${data.balance_usdt.toFixed(2)} USDT`);
      if (onSuccess) onSuccess(data.balance_usdt);
      
      setTimeout(() => {
        onClose();
        setSuccessMsg(null);
        setApiKey("");
        setApiSecret("");
      }, 2000);
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-2xl max-w-md w-full p-6 text-white relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white text-lg font-bold"
        >
          ✕
        </button>

        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg bg-blue-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400 font-bold text-xl">
            ⚡
          </div>
          <div>
            <h3 className="text-lg font-semibold text-white">Connect CoinDCX Account</h3>
            <p className="text-xs text-slate-400">Automated FibVOL Signal Execution</p>
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-xs">
            {error}
          </div>
        )}

        {successMsg && (
          <div className="mb-4 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs">
            {successMsg}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">
              CoinDCX API Key
            </label>
            <input
              type="text"
              required
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="e.g. 5f4e3d2c1b..."
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">
              CoinDCX API Secret
            </label>
            <input
              type="password"
              required
              value={apiSecret}
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder="Enter your encrypted CoinDCX secret"
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
            />
          </div>

          <div className="text-[11px] text-slate-400 bg-slate-950 p-3 rounded-lg border border-slate-800/80">
            <span className="font-semibold text-slate-200">Security Guarantee:</span> Credentials are encrypted using Fernet envelope encryption. Ensure your key has <span className="text-blue-400">Trading</span> permissions enabled.
          </div>

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 py-2 px-4 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 py-2 px-4 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-xs font-semibold text-white transition-colors"
            >
              {loading ? "Validating..." : "Connect Account"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
