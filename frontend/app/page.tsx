import Link from "next/link";
import { SiteHeader, Footer } from "@/components/nav";
import { formatInr } from "@/lib/packages";

export const dynamic = "force-dynamic";

export default async function Home() {
  const plansObj: Record<string, { name: string; price_inr: number; months: number }> = await fetch("http://127.0.0.1:8000/plans", { cache: "no-store" })
    .then((res) => res.json())
    .catch(() => ({}));
  const plans = Object.values(plansObj).sort((a, b) => a.months - b.months);

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
              <Link href="/#pricing" className="btn-gold w-full sm:w-auto">
                Choose your plan
              </Link>
              <a href="/#how" className="btn-ghost w-full sm:w-auto">
                See how it works
              </a>
            </div>
            <p className="mt-5 text-xs text-muted">
              No withdrawal access · You can disconnect anytime · Cancel anytime
            </p>
          </div>
        </div>
      </section>


      {/* How it works */}
      <section id="how" className="container-x py-20">
        <h2 className="text-center text-3xl font-bold text-white">How it works</h2>
        <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
          {[
            ["1", "Create Account", "Sign up and complete your payment via UPI to activate your account. Billed monthly, cancel anytime."],
            ["2", "Connect Tradejini", "Create a Tradejini API key. Connect with your password and 2FA."],
            ["3", "Go live", "When the AI fires a signal, your account opens the trade — sized and stop-lossed automatically."],
          ].map(([n, t, d]) => (
            <div key={n} className="card p-6">
              <span className="grid h-9 w-9 place-items-center rounded-lg bg-ink-700 font-semibold text-gold-400">
                {n}
              </span>
              <h3 className="mt-4 text-lg font-semibold text-white">{t}</h3>
              <p className="mt-2 text-sm text-muted">{d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="container-x py-12 pb-24">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-bold text-white">Simple time-based pricing</h2>
          <p className="mt-3 text-muted">
            Unlimited trades are the same on every plan. Subscribe for longer to save.
          </p>
        </div>

        <div className="mt-12 grid grid-cols-1 gap-6 lg:grid-cols-4">
          {plans.map((p) => (
            <div
              key={p.months}
              className={`card relative flex flex-col p-7 ${
                p.months === 3 || p.months === 6 ? "border-gold-500/60 shadow-glow" : ""
              }`}
            >
              {(p.months === 3 || p.months === 6) && (
                <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-gold-500 px-3 py-1 text-xs font-semibold text-ink-950">
                  Most popular
                </span>
              )}
              <p className="text-sm font-semibold uppercase tracking-wider text-gold-400">
                {p.name}
              </p>
              <p className="mt-1 text-sm text-muted">Complete automation</p>
              <div className="mt-5 flex items-end gap-1">
                <span className="text-4xl font-bold text-white">
                  {formatInr(p.price_inr)}
                </span>
                <span className="pb-1 text-sm text-muted">/{p.months}mo</span>
              </div>
              <p className="mt-4 pill">Unlimited Trades</p>
              <ul className="mt-6 space-y-3 text-sm">
                <li className="flex gap-2.5 text-slate-300">
                  <span className="mt-0.5 text-gain">✓</span>
                  Every AI signal executed, all days
                </li>
              </ul>
              <Link
                href={`/signup?plan=${p.months}`}
                className={`mt-7 w-full ${(p.months === 3 || p.months === 6) ? "btn-gold" : "btn-ghost"}`}
              >
                Get {p.name}
              </Link>
            </div>
          ))}
        </div>
        <p className="mt-8 text-center text-xs text-muted">
          Prices in INR. Your subscription period starts only when you connect your Tradejini account.
        </p>
      </section>

      <Footer />
    </>
  );
}
