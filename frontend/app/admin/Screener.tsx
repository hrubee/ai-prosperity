"use client";

import { useEffect, useState } from "react";
import { api, type ScreenerRow, type Movers, type MoverRow } from "@/lib/api";

// Each "view" maps to a server-side ranking of the CoinDCX USDT markets board.
// "Most liquid" (volume desc) is the default — the operator's "highest volume to
// the top".
const VIEWS: { id: string; label: string; sort: string; dir: string; clientSort?: string; clientDir?: string }[] = [
  { id: "liquid", label: "Most liquid", sort: "volume", dir: "desc" },
  { id: "gainers", label: "Top gainers", sort: "change", dir: "desc" },
  { id: "losers", label: "Top losers", sort: "change", dir: "asc" },
  { id: "movers", label: "Biggest movers", sort: "abs_change", dir: "desc" },
  { id: "gainers15m", label: "Top gainers (15m)", sort: "volume", dir: "desc", clientSort: "change_15m", clientDir: "desc" },
  // Vol rate = how hot a coin trades vs its own 12h baseline (computed for the top liquid set).
  // Fetch by volume to get the rated universe, then re-rank client-side by volume_rate descending
  // (hottest first); un-rated coins sink to the bottom.
  { id: "volrate", label: "Vol rate ↓ (high→low)", sort: "volume", dir: "desc", clientSort: "volume_rate", clientDir: "desc" },
];

// Floor out dead/illiquid symbols (their 24h change% is noise) without hiding
// anything real. Liquid coins clear this comfortably.
const MIN_VOLUME = 1_000_000;

// Auto-refresh cadence. 2s here: the backend caches the Binance board ~4s, but the
// CoinAPI depth snapshot refreshes ~1s, so a faster poll surfaces fresher depth.
// Polling also keeps the sidecar's panel-heartbeat warm (it only streams — and
// only burns CoinAPI credits — while this panel is open).
const REFRESH_MS = 2000;

const fmtPct = (n: number) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";

function fmtVol(n: number): string {
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return "$" + n.toFixed(0);
}

function fmtPrice(n: number): string {
  if (!n) return "—";
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (n >= 1) return n.toFixed(3);
  if (n >= 0.01) return n.toFixed(5);
  return n.toPrecision(3);
}

const fmtSpread = (b: number | null) =>
  b == null ? "—" : (b < 10 ? b.toFixed(2) : b.toFixed(1)) + " bps";

function fmtDepth(bid: number | null, ask: number | null): string {
  if (bid == null || ask == null) return "—";
  return fmtVol(bid + ask);
}

const fmtRate = (r: number | null) => (r == null ? "—" : r.toFixed(1) + "×");

// Volume-rate heat: >1 = trading hotter than its own 12h baseline.
function rateClass(r: number | null): string {
  if (r == null) return "text-muted";
  if (r >= 3) return "text-amber-300 font-semibold";
  if (r >= 1.5) return "text-amber-400";
  return "text-slate-400";
}

// They're all USDT-margined perps, so the quote suffix is redundant noise —
// show just the coin (BTC, not BTCUSDT). Full symbol kept on hover.
const baseCoin = (sym: string) => (sym.endsWith("USDT") ? sym.slice(0, -4) : sym);

// Always-visible "Top movers (24h)" row of chips — biggest % movers regardless of
// the table's current sort, with a vol-rate badge when the coin is trading hot.
function MoverStrip({ label, dir, items }: { label: string; dir: "up" | "down"; items: MoverRow[] }) {
  if (!items.length) return null;
  const up = dir === "up";
  return (
    <div className="flex items-center gap-2">
      <span className={`w-16 shrink-0 text-[10px] font-semibold uppercase tracking-wide ${up ? "text-gain" : "text-loss"}`}>
        {up ? "▲" : "▼"} {label}
      </span>
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {items.map((m) => (
          <span
            key={m.symbol}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-ink-800 bg-ink-900/50 px-2 py-1 text-xs"
            title={`${m.symbol} · ${fmtVol(m.volume)} 24h vol${m.volume_rate != null ? ` · ${m.volume_rate.toFixed(1)}× vol rate` : ""}`}
          >
            <span className="font-medium text-white">{baseCoin(m.symbol)}</span>
            <span className={up ? "text-gain" : "text-loss"}>{fmtPct(m.change_pct)}</span>
            {m.volume_rate != null && m.volume_rate >= 1.5 && (
              <span className={rateClass(m.volume_rate)}>{m.volume_rate.toFixed(1)}×</span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

export function Screener() {
  const [viewId, setViewId] = useState("liquid");
  const [rows, setRows] = useState<ScreenerRow[] | null>(null);
  const [movers, setMovers] = useState<Movers | null>(null);
  const [feedLive, setFeedLive] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);

  function load() {
    const v = VIEWS.find((x) => x.id === viewId) ?? VIEWS[0];
    api
      .adminScreener(v.sort, v.dir, MIN_VOLUME, 100)
      .then((r) => {
        let rows = r.rows;
        if (v.clientSort) {
          const key = v.clientSort;
          const asc = v.clientDir !== "desc";
          rows = [...rows].sort((a, b) => {
            const av = (a as any)[key], bv = (b as any)[key];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;          // un-rated coins always sink to the bottom
            if (bv == null) return -1;
            return asc ? av - bv : bv - av;
          });
        }
        setRows(rows);
        setMovers(r.movers ?? null);
        setFeedLive(r.feed_live);
        setUpdatedAt(Date.now());
        setErr("");
      })
      .catch((e: any) => {
        setRows([]);
        setErr(e.message || "screener unavailable");
      });
  }

  // Load on view change + poll every REFRESH_MS, skipping ticks while the tab is
  // hidden (no point fetching — or streaming CoinAPI — for a page nobody's on).
  useEffect(() => {
    load();
    const id = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) load();
    }, REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewId]);

  const filtered = (rows ?? []).filter((r) => r.symbol.toUpperCase().includes(q.toUpperCase()));

  return (
    <div className="mt-8">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
            Market screener · CoinDCX USDT markets
          </h2>
          <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-400">
            Crypto
          </span>
          {rows && <span className="text-xs text-muted">{filtered.length}</span>}
          {updatedAt && !err && (
            <span
              className="inline-flex items-center gap-1.5 text-xs text-muted"
              title={feedLive ? "Live CoinAPI depth streaming" : "Board live; CoinAPI depth idle"}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${feedLive ? "animate-pulse bg-gain" : "bg-amber-400"}`} />
              {feedLive ? "depth live" : "board live"} · {new Date(updatedAt).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input !py-1.5 text-sm"
            value={viewId}
            onChange={(e) => setViewId(e.target.value)}
            aria-label="Screener view"
          >
            {VIEWS.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
          <input
            className="input max-w-[10rem] !py-1.5 text-sm"
            placeholder="Filter symbol…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={load}>
            Refresh
          </button>
        </div>
      </div>

      {movers && (movers.gainers.length > 0 || movers.losers.length > 0) && (
        <div className="mb-3 space-y-1.5 rounded-lg border border-ink-800 bg-ink-950/40 p-2.5">
          <div className="px-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
            Top movers · 24h
          </div>
          <MoverStrip label="Gainers" dir="up" items={movers.gainers} />
          <MoverStrip label="Losers" dir="down" items={movers.losers} />
        </div>
      )}

      {err && <p className="card border-loss/40 p-4 text-sm text-loss">{err}</p>}

      {!err && rows === null && <p className="py-6 text-center text-sm text-muted">Loading…</p>}

      {!err && rows !== null && filtered.length === 0 && (
        <div className="card p-8 text-center">
          <p className="text-sm text-muted">No symbols match.</p>
        </div>
      )}

      {!err && filtered.length > 0 && (
        <div className="card overflow-hidden p-0">
          <div className="max-h-[32rem] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-ink-950/95 text-left text-xs uppercase tracking-wider text-muted backdrop-blur">
                <tr>
                  <th className="px-4 py-2.5">Symbol</th>
                  <th className="px-4 py-2.5 text-right">Price</th>
                  <th className="px-4 py-2.5 text-right">24h %</th>
                  <th className="px-4 py-2.5 text-right" title="% change over the last ~15 minutes (top liquid coins)">15m %</th>
                  <th className="px-4 py-2.5 text-right">24h Vol</th>
                  <th
                    className="px-4 py-2.5 text-right"
                    title="Volume rate: last closed 1h volume ÷ median of prior 12h. >1 = trading hotter than usual (activity, not direction)"
                  >
                    Vol rate
                  </th>
                  <th className="px-4 py-2.5 text-right" title="Top-of-book spread (CoinAPI live)">
                    Spread
                  </th>
                  <th className="px-4 py-2.5 text-right" title="Σ bid+ask depth over top-20 levels (CoinAPI live)">
                    Depth
                  </th>
                  <th className="px-4 py-2.5 text-right" title="Bid share of top-20 depth — &gt;50% bid-heavy (buy pressure)">
                    Imbalance
                  </th>
                  <th className="px-4 py-2.5 text-right" title="EMA9/150 crosses in 48h — the bot's congestion gate (tracked symbols only)">
                    48h ✕
                  </th>
                </tr>
              </thead>
              <tbody className="text-slate-300">
                {filtered.map((r) => {
                  const imb = r.imbalance;
                  return (
                    <tr key={r.symbol} className="border-t border-ink-800">
                      <td className="px-4 py-2.5 font-medium text-white">
                        <span className="inline-flex items-center gap-1.5">
                          {r.live && (
                            <span
                              className="h-1.5 w-1.5 rounded-full bg-gain"
                              title="Live order-book depth"
                            />
                          )}
                          <span title={r.symbol}>{baseCoin(r.symbol)}</span>
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{fmtPrice(r.price)}</td>
                      <td
                        className={`px-4 py-2.5 text-right font-medium tabular-nums ${
                          r.change_pct >= 0 ? "text-gain" : "text-loss"
                        }`}
                      >
                        {fmtPct(r.change_pct)}
                      </td>
                      <td
                        className={`px-4 py-2.5 text-right font-medium tabular-nums ${
                          ((r as any).change_15m ?? 0) >= 0 ? "text-gain" : "text-loss"
                        }`}
                      >
                        {(r as any).change_15m == null ? "\u2014" : fmtPct((r as any).change_15m)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{fmtVol(r.volume)}</td>
                      <td className={`px-4 py-2.5 text-right tabular-nums ${rateClass(r.volume_rate)}`}>
                        {fmtRate(r.volume_rate)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted">
                        {fmtSpread(r.spread_bps)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted">
                        {fmtDepth(r.bid_depth_usd, r.ask_depth_usd)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {imb == null ? (
                          <span className="text-muted">—</span>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-loss/30">
                              <div
                                className="h-full bg-gain"
                                style={{ width: `${Math.round(imb * 100)}%` }}
                              />
                            </div>
                            <span
                              className={`w-9 text-right tabular-nums ${
                                imb >= 0.5 ? "text-gain" : "text-loss"
                              }`}
                            >
                              {Math.round(imb * 100)}%
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {r.crosses_48h == null ? (
                          <span className="text-muted">—</span>
                        ) : (
                          <span
                            className={r.crosses_48h >= 5 ? "text-loss" : "text-slate-300"}
                            title={
                              (r.crosses_48h >= 5 ? "Congested (≥5 = bot blocks entry). " : "") +
                              (r.crosses_fresh ? "live" : "stale")
                            }
                          >
                            {r.crosses_48h}
                            {!r.crosses_fresh && <span className="ml-0.5 text-amber-400">·</span>}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="border-t border-ink-800 px-4 py-2 text-[11px] text-muted">
            Depth · spread · imbalance stream live (CoinAPI) for the top ~30 by volume. Vol rate = last
            1h volume ÷ median of prior 12h (&gt;1 = trading hot; an activity signal, not direction). ✕ =
            EMA9/150 crosses in 48h (the bot&apos;s congestion gate) for tracked symbols; ≥5 blocks new entries.
          </div>
        </div>
      )}
    </div>
  );
}
