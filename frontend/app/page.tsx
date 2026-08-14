import Link from "next/link";
import { SiteHeader, Footer } from "@/components/nav";

export const dynamic = "force-dynamic";

export default async function Home() {
  return (
    <>
      <SiteHeader />

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="container-x py-20 sm:py-28">
          <div className="mx-auto max-w-3xl text-center">
            <span className="pill mx-auto">
              <span className="h-1.5 w-1.5 rounded-full bg-gain" />
              Live AI brain · Indian F&amp;O on Tradejini
            </span>
            <h1 className="mt-6 text-4xl font-bold leading-tight tracking-tight text-white sm:text-6xl">
              One AI brain.{" "}
              <span className="text-gold-400">Your account on autopilot.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-muted">
              Our AI decides the direction. Your Tradejini account places the trade.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link href="/signup" className="btn-gold w-full sm:w-auto px-8 py-3 text-base">
                Get Started
              </Link>
              <a href="#how" className="btn-ghost w-full sm:w-auto px-8 py-3 text-base">
                See how it works
              </a>
            </div>
            <p className="mt-5 text-xs text-muted">
              No withdrawal access · Admin-approved access · Disconnect anytime
            </p>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="container-x py-20 border-t border-ink-800">
        <h2 className="text-center text-3xl font-bold text-white">How it works</h2>
        <p className="mt-3 text-center text-muted max-w-xl mx-auto">
          Get started in 3 simple steps — no payment required upfront.
        </p>
        <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
          {[
            ["1", "Create Account", "Fill in your registration details including your Tradejini Client ID."],
            ["2", "Admin Approval", "Your account enters a pending state for rapid admin review and approval."],
            ["3", "Connect & Go Live", "Once approved, connect your Tradejini API key to start automated ai trading."],
          ].map(([n, t, d]) => (
            <div key={n} className="card p-7 hover:border-gold-500/40 transition-colors">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-gold-500/10 font-bold text-gold-400 text-lg">
                {n}
              </span>
              <h3 className="mt-5 text-xl font-semibold text-white">{t}</h3>
              <p className="mt-2 text-sm text-muted leading-relaxed">{d}</p>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
