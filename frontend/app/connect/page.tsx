"use client";


import Link from "next/link";
import { Logo } from "@/components/nav";
export default function ConnectPage() {
  return (
    <main className="min-h-screen px-5 py-10">
      <div className="mx-auto max-w-lg">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>

        {/* CoinDCX (Crypto Futures & Spot) */}
        <div className="mb-6 rounded-xl border border-blue-500/40 bg-ink-800/40 p-5 text-center">
          <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-xl bg-blue-500/10 text-xl font-bold text-blue-400">
            ⚡
          </div>
          <p className="text-base font-semibold text-white">
            CoinDCX (Crypto Futures &amp; Spot)
          </p>
          <p className="mt-1 text-xs text-muted leading-relaxed">
            Automate Crypto Futures trading 24/7 on CoinDCX. Connect your account using your read/trade API Key &amp; Secret. Credentials are encrypted at rest.
          </p>
          <Link href="/connect/coindcx" className="btn-gold mt-4 block w-full">
            Connect CoinDCX Account →
          </Link>
        </div>

        {/* Indian F&O via Tradejini — bring-your-own API key flow */}
        <div className="mb-6 rounded-xl border border-gold-500/30 bg-ink-800/40 p-5 text-center">
          <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-xl bg-gold-500/10 text-xl font-bold text-gold-400">
            📈
          </div>
          <p className="text-base font-semibold text-white">
            Indian F&amp;O (Tradejini)
          </p>
          <p className="mt-1 text-xs text-muted leading-relaxed">
            Connect your Tradejini account with your API key. Auto-renews daily via password + 2FA TOTP seed — you never have to re-login.
          </p>
          <Link href="/connect/tradejini" className="btn-ghost mt-4 block w-full">
            Connect Tradejini →
          </Link>
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
