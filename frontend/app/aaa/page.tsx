"use client";

import { useEffect, useMemo, useState } from "react";
import { Logo } from "@/components/nav";
import { api, type AaaSetup, type AaaSetupsResponse } from "@/lib/api";

// ── formatting ──────────────────────────────────────────────
const fmtPx = (n: number) =>
  "₹" + (n ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const fmtPct = (n: number) => (n >= 0 ? "+" : "") + n.toFixed(1) + "%";

function trendLabel(t: string): { sym: string; cls: string; text: string } {
  if (t === "down") return { sym: "▼", cls: "text-loss", text: "Downtrend" };
  if (t === "up") return { sym: "▲", cls: "text-gain", text: "Uptrend" };
  return { sym: "•", cls: "text-muted", text: "Flat" };
}

type DirFilter = "all" | "bullish" | "bearish";

export default function AaaPage() {
  const [data, setData] = useState<AaaSetupsResponse | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [dir, setDir] = useState<DirFilter>("all");
  const [pattern, setPattern] = useState("all");
  const [q, setQ] = useState("");

  function load() {
    setLoading(true);
    api
      .aaaSetups()
      .then((d) => {
        setData(d);
        setErr("");
      })
      .catch((e: any) => setErr(e.message || "Could not load setups"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  const setups = data?.setups ?? [];

  // Pattern dropdown options, derived from what's actually in the result.
  const patterns = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of setups) m.set(s.code, s.pattern);
    return Array.from(m, ([code, label]) => ({ code, label })).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [setups]);

  const filtered = setups.filter((s) => {
    if (dir !== "all" && s.direction !== dir) return false;
    if (pattern !== "all" && s.code !== pattern) return false;
    if (q && !`${s.symbol} ${s.name}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  const bullCount = setups.filter((s) => s.direction === "bullish").length;
  const bearCount = setups.filter((s) => s.direction === "bearish").length;

  return (
    <main className="min-h-screen">
      <header className="border-b border-ink-800 bg-ink-950/70">
        <div className="container-x flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <Logo />
            <span className="pill">Indian Stocks · 1D</span>
          </div>
          <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={load} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      <div className="container-x py-8">
        {/* ── AAA section — on top ── */}
        <section>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-bold text-white sm:text-3xl">
                AAA Setup for Indian Stocks
              </h1>
              <p className="mt-1.5 max-w-2xl text-sm text-muted">
                Daily candlestick reversal setups across the NSE universe on the{" "}
                <span className="text-slate-300">1-day timeframe</span>. The scan refreshes
                automatically every weekday before the 9:15 market open, so this list is ready when
                the bell rings. Each setup just completed on the prior session&apos;s close — watch
                today&apos;s candle to confirm before acting.
              </p>
            </div>
            <div className="text-right text-xs text-muted">
              {data?.generated_at_ist ? (
                <>
                  <div>
                    Updated <span className="text-slate-300">{data.generated_at_ist}</span>
                  </div>
                  <div className="mt-0.5">
                    {data.scanned.toLocaleString("en-IN")} stocks scanned · {data.count} setups
                  </div>
                </>
              ) : (
                <div>Awaiting today&apos;s scan…</div>
              )}
            </div>
          </div>

          {data?.stale && (
            <p className="card mt-4 border-gold-500/30 p-3 text-sm text-gold-400">
              The scan hasn&apos;t produced results yet today. It runs automatically before market
              open — check back shortly.
            </p>
          )}
          {err && <p className="card mt-4 border-loss/40 p-4 text-sm text-loss">{err}</p>}

          {/* Summary tiles */}
          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Tile label="Total setups" value={String(data?.count ?? "—")} />
            <Tile label="Bullish (buy)" value={String(bullCount)} accent="gain" />
            <Tile label="Bearish (short)" value={String(bearCount)} accent="loss" />
            <Tile label="Stocks scanned" value={(data?.scanned ?? 0).toLocaleString("en-IN")} />
          </div>

          {/* Controls */}
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <div className="flex gap-1.5">
              {(["all", "bullish", "bearish"] as DirFilter[]).map((d) => (
                <button
                  key={d}
                  onClick={() => setDir(d)}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                    dir === d
                      ? d === "bullish"
                        ? "bg-gain/15 text-gain"
                        : d === "bearish"
                          ? "bg-loss/15 text-loss"
                          : "bg-gold-500/15 text-gold-400"
                      : "border border-ink-700 text-muted hover:text-white"
                  }`}
                >
                  {d === "all" ? "All" : d === "bullish" ? "Bullish · buy" : "Bearish · short"}
                </button>
              ))}
            </div>
            <select
              className="input !w-auto !py-1.5 text-sm"
              value={pattern}
              onChange={(e) => setPattern(e.target.value)}
              aria-label="Filter by pattern"
            >
              <option value="all">All patterns</option>
              {patterns.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.label}
                </option>
              ))}
            </select>
            <input
              className="input max-w-[12rem] !py-1.5 text-sm"
              placeholder="Search symbol / name…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <span className="text-xs text-muted">{filtered.length} shown</span>
          </div>

          {/* Table */}
          {loading && !data ? (
            <p className="py-10 text-center text-sm text-muted">Loading setups…</p>
          ) : filtered.length === 0 ? (
            <div className="card mt-4 p-10 text-center">
              <p className="text-sm text-muted">
                {setups.length === 0
                  ? "No setups in the latest scan."
                  : "No setups match these filters."}
              </p>
            </div>
          ) : (
            <div className="card mt-4 overflow-hidden p-0">
              <div className="max-h-[40rem] overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-ink-950/95 text-left text-xs uppercase tracking-wider text-muted backdrop-blur">
                    <tr>
                      <th className="px-4 py-2.5">Stock</th>
                      <th className="px-4 py-2.5">Pattern</th>
                      <th className="px-4 py-2.5">Signal</th>
                      <th className="px-4 py-2.5">Trend</th>
                      <th className="px-4 py-2.5 text-right" title="Last completed daily close">
                        Close
                      </th>
                      <th
                        className="px-4 py-2.5 text-right"
                        title="Price beyond which today's candle confirms the reversal"
                      >
                        Confirm
                      </th>
                      <th
                        className="px-4 py-2.5 text-right"
                        title="Suggested protective stop — pattern low for buys, high for shorts"
                      >
                        Stop
                      </th>
                      <th
                        className="px-4 py-2.5 text-center"
                        title="Signal candle traded above its recent average volume (higher reliability)"
                      >
                        Vol
                      </th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-300">
                    {filtered.map((s, i) => (
                      <Row key={`${s.symbol}-${s.code}-${i}`} s={s} />
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="border-t border-ink-800 px-4 py-2.5 text-[11px] leading-relaxed text-muted">
                Setups are detected on the most recent <span className="text-slate-400">completed</span>{" "}
                daily candle. <span className="text-gain">Confirm</span> is the level today&apos;s
                candle must clear to validate the reversal; <span className="text-slate-400">Stop</span>{" "}
                is a suggested protective level (pattern low for buys, high for shorts).{" "}
                <span className="text-slate-400">Vol ✓</span> means the signal candle traded above
                its recent average volume. Educational reference only — not financial advice. Always
                confirm and manage risk with a stop-loss.
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function Tile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: "gain" | "loss";
}) {
  const valCls = accent === "gain" ? "text-gain" : accent === "loss" ? "text-loss" : "text-white";
  return (
    <div className="card p-4">
      <p className="text-xs uppercase tracking-wider text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${valCls}`}>{value}</p>
    </div>
  );
}

function Row({ s }: { s: AaaSetup }) {
  const t = trendLabel(s.trend);
  const buy = s.direction === "bullish";
  return (
    <tr className="border-t border-ink-800 hover:bg-ink-800/30">
      <td className="px-4 py-2.5">
        <div className="font-medium text-white">{s.symbol}</div>
        {s.name && <div className="max-w-[14rem] truncate text-xs text-muted">{s.name}</div>}
      </td>
      <td className="px-4 py-2.5">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            buy ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"
          }`}
        >
          {buy ? "▲" : "▼"} {s.pattern}
        </span>
        <div className="mt-1 text-[11px] uppercase tracking-wide text-muted">
          {buy ? "Buy" : "Short"} · {s.candles}-candle
        </div>
      </td>
      <td className="px-4 py-2.5 text-xs text-muted">{s.signal_date}</td>
      <td className="px-4 py-2.5">
        <span className={`inline-flex items-center gap-1 text-xs ${t.cls}`} title={`${fmtPct(s.trend_move_pct)} into the pattern`}>
          {t.sym} {t.text}
        </span>
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums">{fmtPx(s.last_close)}</td>
      <td className={`px-4 py-2.5 text-right tabular-nums ${buy ? "text-gain" : "text-loss"}`}>
        {fmtPx(s.trigger)}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums text-muted">{fmtPx(s.stop_suggest)}</td>
      <td className="px-4 py-2.5 text-center">
        {s.volume_confirm == null ? (
          <span className="text-muted">—</span>
        ) : s.volume_confirm ? (
          <span className="text-gain" title="Above recent average volume">
            ✓
          </span>
        ) : (
          <span className="text-muted" title="Below recent average volume">
            ·
          </span>
        )}
      </td>
    </tr>
  );
}
