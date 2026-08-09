"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Logo } from "./nav";

export function AdminNav() {
  const pathname = usePathname();
  const [isOpen, setIsOpen] = useState(false);

  const navItems = [
    { href: "/admin", label: "Overview", activeClasses: "text-white bg-ink-800" },
    { href: "/admin/approvals", label: "Approvals", activeClasses: "text-amber-400 bg-amber-400/10" },
    { href: "/admin/copier", label: "Copier", activeClasses: "text-blue-400 bg-blue-400/10" },
    { href: "/admin/vol2b2t", label: "Vol2b2t", activeClasses: "text-emerald-400 bg-emerald-400/10" },
    { href: "/admin/dhan", label: "Dhan", activeClasses: "text-gold-500 bg-gold-500/10" },
    { href: "/admin/poller", label: "Poller", activeClasses: "text-purple-400 bg-purple-400/10" },
  ];

  return (
    <header className="border-b border-ink-800 bg-ink-950/90 sticky top-0 z-50 backdrop-blur-md">
      <div className="container-x flex h-16 items-center justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="pill hidden sm:inline-flex">Admin</span>
        </div>
        
        {/* Desktop Nav */}
        <nav className="hidden lg:flex items-center gap-1">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  isActive ? item.activeClasses : "text-muted hover:text-white hover:bg-ink-800/50"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
          <div className="w-px h-5 bg-ink-800 mx-2" />
          <button className="px-3 py-1.5 rounded-md text-sm font-medium text-loss hover:bg-loss/10 transition-colors">
            Global kill switch
          </button>
        </nav>

        {/* Mobile Hamburger Button */}
        <button 
          className="lg:hidden p-2 text-muted hover:text-white"
          onClick={() => setIsOpen(!isOpen)}
        >
          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={isOpen ? "M6 18L18 6M6 6l12 12" : "M4 6h16M4 12h16M4 18h16"} />
          </svg>
        </button>
      </div>

      {/* Mobile Nav Dropdown */}
      {isOpen && (
        <div className="lg:hidden border-t border-ink-800 bg-ink-900 px-4 py-4 space-y-2">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setIsOpen(false)}
                className={`block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive ? item.activeClasses : "text-muted hover:text-white hover:bg-ink-800/50"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
          <div className="h-px w-full bg-ink-800 my-2" />
          <button className="w-full text-left px-3 py-2 rounded-md text-sm font-medium text-loss hover:bg-loss/10 transition-colors">
            Global kill switch
          </button>
        </div>
      )}
    </header>
  );
}
