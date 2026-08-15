import Link from "next/link";
import { SiteHeader, Footer } from "@/components/nav";
import { PROFIT_SPLIT_MODEL } from "@/lib/packages";

export const dynamic = "force-dynamic";

export default async function Home() {
  return (
    <>
      <SiteHeader />

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="container-x py-20 sm:py-28">
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="text-4xl font-bold leading-tight tracking-tight text-white sm:text-6xl">
              One AI brain.{" "}
              <span className="text-gold-400">Your account on autopilot.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-muted">
              Zero monthly subscriptions. Pay 40% performance share only when the strategy makes you net profits.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link href="/signup" className="btn-gold w-full sm:w-auto px-8 py-3 text-base">
                Get Started (Free)
              </Link>
              <a href="#how" className="btn-ghost w-full sm:w-auto px-8 py-3 text-base">
                See how it works
              </a>
            </div>
            <p className="mt-5 text-xs text-muted">
              No withdrawal access · High-water mark protection · Admin-approved access · Disconnect anytime
            </p>
          </div>
        </div>
      </section>

      {/* Model Cards */}
      <section className="container-x pb-12">
        <div className="card p-8 border-gold-500/30 bg-ink-900/80 backdrop-blur">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-center">
            <div className="p-4 border-r border-ink-800 last:border-r-0">
              <p className="text-xs uppercase tracking-wider text-muted font-semibold">Upfront Fee</p>
              <p className="mt-2 text-3xl font-bold text-white">₹0 / Month</p>
              <p className="mt-1 text-xs text-muted">No credit card or recurring plan</p>
            </div>
            <div className="p-4 border-r border-ink-800 last:border-r-0">
              <p className="text-xs uppercase tracking-wider text-muted font-semibold">Your Profit Retained</p>
              <p className="mt-2 text-3xl font-bold text-gain">60%</p>
              <p className="mt-1 text-xs text-muted">Direct to your equity</p>
            </div>
            <div className="p-4">
              <p className="text-xs uppercase tracking-wider text-muted font-semibold">Platform Performance Share</p>
              <p className="mt-2 text-3xl font-bold text-gold-400">40%</p>
              <p className="mt-1 text-xs text-muted">Pay only on net realized profits</p>
            </div>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="container-x py-20 border-t border-ink-800">
        <h2 className="text-center text-3xl font-bold text-white">How it works</h2>
        <p className="mt-3 text-center text-muted max-w-xl mx-auto">
          Get started in 3 simple steps — zero subscription fees upfront.
        </p>
        <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
          {[
            ["1", "Sign Up & Provide ID", "Enter your Name, Email, Phone, and your Tradejini Client ID."],
            ["2", "Admin Approval", "Your account enters a pending state for rapid admin verification."],
            ["3", "Connect & Copy Trade", "Once approved, connect your exchange account. Keep 60% of all generated profits."],
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
