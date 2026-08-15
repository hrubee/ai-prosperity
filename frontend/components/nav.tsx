import Link from "next/link";

export function Logo({ className = "" }: { className?: string }) {
  return (
    <Link href="/" className={`group inline-flex items-center gap-2 ${className}`}>
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-gold-500 text-ink-950 font-bold">
        A
      </span>
      <span className="text-base font-semibold tracking-tight text-white">
        AI&nbsp;Prosperity
      </span>
    </Link>
  );
}

export function SiteHeader({ cta = true }: { cta?: boolean }) {
  return (
    <header className="sticky top-0 z-30 border-b border-ink-800/80 bg-ink-950/70 backdrop-blur">
      <div className="container-x flex h-16 items-center justify-between">
        <Logo />
        <nav className="hidden items-center gap-7 text-sm text-muted sm:flex">
          <a href="/#how" className="hover:text-white">How it works</a>
          <a href="/#pricing" className="hover:text-white">Pricing</a>
          <Link href="/dashboard" className="hover:text-white">Dashboard</Link>
        </nav>
        {cta && (
          <div className="flex items-center gap-3">
            <Link href="/login" className="text-sm text-muted hover:text-white">
              Log in
            </Link>
            <Link href="/#pricing" className="btn-gold !px-4 !py-2">
              Get started
            </Link>
          </div>
        )}
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-ink-800 py-10 text-sm text-muted">
      <div className="container-x flex flex-col items-center justify-between gap-4 sm:flex-row">
        <Logo />
        <p className="text-center sm:text-right text-xs">
          Trading involves risk of loss. Not financial advice. You control your funds at all times.
          <br className="hidden sm:block" />
          © {new Date().getFullYear()} AI Prosperity. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
