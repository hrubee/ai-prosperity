"use client";

import { useEffect, useRef, useState } from "react";
import { api, type OrderBookData } from "@/lib/api";

// Live-traded coins (quick-pick); any USDT-M perp can be typed in.
const LIVE = ["BTC", "ETH", "SOL", "XRP", "BNB"];
const REFRESH_MS = 4000; // backend caches ~2s
const W = 1000;
const H = 180;

function fmtUsd(n: number): string {
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
  return "$" + n.toFixed(0);
}
function fmtPrice(n: number): string {
  if (!n) return "—";
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (n >= 1) return n.toFixed(3);
  if (n >= 0.01) return n.toFixed(5);
  return n.toPrecision(3);
}

export function OrderBook() {
  const [symbol, setSymbol] = useState("SOL");
  const [input, setInput] = useState("");
  const [d, setD] = useState<OrderBookData | null>(null);
  const [err, setErr] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const symRef = useRef(symbol);
  symRef.current = symbol;

  function load() {
    api
      .adminOrderbook(symRef.current)
      .then((r) => {
        setD(r);
        setUpdatedAt(Date.now());
        setErr("");
      })
      .catch((e: any) => {
        setD(null);
        setErr(e.message || "orderbook unavailable");
      });
  }

  useEffect(() => {
    setD(null);
    load();
    const id = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) load();
    }, REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  // ---- build the SVG depth chart ----
  let chart: React.ReactNode = null;
  let axis: React.ReactNode = null;
  if (d && d.chart && d.chart.bids.length > 1 && d.chart.asks.length > 1) {
    const bidPts = [...d.chart.bids].reverse(); // ascending price
    const askPts = d.chart.asks; // ascending price
    const xMin = bidPts[0].price;
    const xMax = askPts[askPts.length - 1].price;
    const yMax = Math.max(...bidPts.map((p) => p.cum), ...askPts.map((p) => p.cum), 1);
    const X = (p: number) => ((p - xMin) / (xMax - xMin || 1)) * W;
    const Y = (c: number) => H - (c / yMax) * H;
    const area = (pts: { price: number; cum: number }[]) =>
      `M ${X(pts[0].price)},${H} ` +
      pts.map((p) => `L ${X(p.price)},${Y(p.cum)}`).join(" ") +
      ` L ${X(pts[pts.length - 1].price)},${H} Z`;
    const midX = X(d.mid);
    chart = (
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-44 w-full">
        <path d={area(bidPts)} fill="rgba(34,197,94,0.22)" stroke="rgb(34,197,94)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <path d={area(askPts)} fill="rgba(239,68,68,0.20)" stroke="rgb(239,68,68)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <line x1={midX} y1={0} x2={midX} y2={H} stroke="rgba(148,163,184,0.7)" strokeWidth={1} strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
        {d.walls.map((w, i) => {
          const wx = X(w.price);
          if (wx < 0 || wx > W) return null;
          const col = w.role === "support" ? "rgb(34,197,94)" : "rgb(239,68,68)";
          return (
            <g key={i}>
              <line x1={wx} y1={0} x2={wx} y2={H} stroke={col} strokeWidth={1} opacity={0.45} vectorEffect="non-scaling-stroke" />
              <circle cx={wx} cy={9} r={4} fill={col} />
            </g>
          );
        })}
      </svg>
    );
    axis = (
      <div className="flex justify-between px-1 pt-1 text-[10px] tabular-nums text-muted">
        <span>{fmtPrice(xMin)}</span>
        <span className="text-slate-300">mid {fmtPrice(d.mid)}</span>
        <span>{fmtPrice(xMax)}</span>
      </div>
    );
  }

  const t = d?.trend;
  const imb = d ? d.imbalance : 0.5;

  return (
    <div className="mt-8">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">Order book · live depth</h2>
          <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-400">
            Context
          </span>
          {updatedAt && !err && (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gain" />
              live · {new Date(updatedAt).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {LIVE.map((c) => (
            <button
              key={c}
              onClick={() => setSymbol(c)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                d?.symbol?.replace("USDT", "") === c ? "bg-sky-500/20 text-sky-300" : "btn-ghost"
              }`}
            >
              {c}
            </button>
          ))}
          <input
            className="input max-w-[7rem] !py-1.5 text-sm"
            placeholder="symbol…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && input.trim()) {
                setSymbol(input.trim().toUpperCase());
                setInput("");
              }
            }}
          />
        </div>
      </div>

      {err && <p className="card border-loss/40 p-4 text-sm text-loss">{err}</p>}
      {!err && !d && <p className="py-6 text-center text-sm text-muted">Loading order book…</p>}

      {!err && d && (
        <div className="card overflow-hidden p-0">
          {/* header strip: price + trend + congestion + spread */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-ink-800 px-4 py-2.5 text-sm">
            <span className="font-semibold text-white">{d.symbol.replace("USDT", "")}</span>
            <span className="tabular-nums text-slate-200">{fmtPrice(d.mid)}</span>
            {t && !t.err && (
              <span className={t.bull ? "text-gain" : "text-loss"}>
                {t.bull ? "▲ BULLISH" : "▼ BEARISH"} · sep {t.sep_pct?.toFixed(3)}%
                {t.recent_cross && " · FRESH cross"}
              </span>
            )}
            {d.congestion_48h != null && (
              <span className={d.congestion_48h >= 5 ? "text-loss" : "text-muted"}>
                {d.congestion_48h} crosses/48h{d.congestion_48h >= 5 && " ⚠ congested (blocks entry)"}
              </span>
            )}
            <span className="text-muted">spread {d.spread_bps.toFixed(2)} bps</span>
          </div>

          {/* depth chart */}
          <div className="px-2 pt-2">{chart}</div>
          {axis}

          {/* imbalance + depth */}
          <div className="flex flex-wrap items-center gap-4 px-4 py-2.5 text-xs">
            <span className="text-muted">depth(±0.15%)</span>
            <span className="tabular-nums text-gain">bid {fmtUsd(d.bid_depth)}</span>
            <div className="flex h-2 w-40 overflow-hidden rounded-full bg-loss/40">
              <div className="h-full bg-gain" style={{ width: `${Math.round(imb * 100)}%` }} />
            </div>
            <span className="tabular-nums text-loss">ask {fmtUsd(d.ask_depth)}</span>
            <span className={`tabular-nums ${imb >= 0.5 ? "text-gain" : "text-loss"}`}>
              {Math.round(imb * 100)}% {imb >= 0.5 ? "bid-heavy" : "ask-heavy"}
            </span>
          </div>

          {/* walls */}
          <div className="border-t border-ink-800 px-4 py-2.5">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
              Standout walls (S/R magnets, away from price)
            </div>
            {d.walls.length === 0 ? (
              <p className="text-xs text-muted">No standout walls within ±1.5% — book is even.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {d.walls.map((w, i) => (
                  <span
                    key={i}
                    className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs tabular-nums ${
                      w.role === "support" ? "border-gain/40 text-gain" : "border-loss/40 text-loss"
                    }`}
                    title={`${w.role} ${fmtUsd(w.notional)} @ ${fmtPrice(w.price)} (${w.mult.toFixed(0)}× a normal level)`}
                  >
                    {w.role === "support" ? "▼" : "▲"} {fmtUsd(w.notional)} @ {fmtPrice(w.price)}
                    <span className="text-muted">
                      ({w.dist_pct >= 0 ? "+" : ""}
                      {w.dist_pct.toFixed(2)}%, {w.mult.toFixed(0)}×)
                    </span>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-ink-800 px-4 py-2 text-[11px] text-muted">
            Live Binance USDT-M futures book. Green = bid depth, red = ask depth; walls = outsized
            resting orders away from price. <b>Context for your read — the bot does not trade on this</b>
            (order-book has no predictive edge at 5m).
          </div>
        </div>
      )}
    </div>
  );
}
