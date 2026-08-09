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

        {/* Indian F&O via Tradejini — bring-your-own API key flow */}
        <div className="mb-6 rounded-xl border border-gold-500/30 bg-ink-800/40 p-4 text-center">
          <p className="text-sm text-slate-300">
            Trading <b className="text-slate-100">Indian F&amp;O</b> (NIFTY / BankNIFTY)?
          </p>
          <p className="mt-1 text-xs text-muted">
            Connect your Tradejini account with your own API key — re-authorize daily with password
            + 2FA. Your credentials are sent only to Tradejini to mint a daily token and never
            stored.
          </p>
          <Link href="/connect/tradejini" className="btn-gold mt-3 w-full">
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
