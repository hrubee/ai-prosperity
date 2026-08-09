"use client";

import { useEffect, useState } from "react";
import { api, type BrainEvent } from "@/lib/api";

// Verdict styling, derived from the brain's recorded action + whether the model ran.
function verdict(e: BrainEvent): { label: string; cls: string; dot: string } {
  if (e.action === "vetoed")
    return { label: "Vetoed", cls: "bg-loss/10 text-loss border-loss/30", dot: "bg-loss" };
  if (e.action === "exit-only")
    return { label: "Exit-only · take-profit", cls: "bg-gold-500/10 text-gold-300 border-gold-500/30", dot: "bg-gold-400" };
  if (e.action === "fallback")
    return { label: "No vision · ATR", cls: "bg-gold-400/10 text-gold-400 border-gold-400/30", dot: "bg-gold-400" };
  if (e.action === "published" && e.vision_evaluated)
    return { label: "Approved", cls: "bg-gain/10 text-gain border-gain/30", dot: "bg-gain" };
  return { label: "Published", cls: "bg-slate-500/10 text-slate-300 border-slate-500/30", dot: "bg-slate-400" };
}

function ago(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" }) +
    " " + d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

const fmt = (n: number | null, d = 1) => (n == null ? "—" : n.toLocaleString("en-IN", { maximumFractionDigits: d }));

export function BrainVision() {
  const [open, setOpen] = useState<BrainEvent | null>(null);
  return (
    <>
      <BrainSection
        source="nifty-brain"
        title="AI Vision · NIFTY F&O"
        tag="F&O"
        tagCls="bg-gold-400/10 text-gold-400"
        emptyHint="The NIFTY brain logs a card on each fresh EMA cross during market hours (09:15–15:30 IST)."
        onOpen={setOpen}
      />
      <BrainSection
        source="crypto-brain"
        title="AI Vision · Crypto"
        tag="Crypto"
        tagCls="bg-sky-500/10 text-sky-400"
        emptyHint="The crypto brain logs around the clock; quiet stretches between fresh crosses are normal."
        onOpen={setOpen}
      />
      {open && <ChartLightbox event={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function BrainSection({
  source, title, tag, tagCls, emptyHint, onOpen,
}: {
  source: string;
  title: string;
  tag: string;
  tagCls: string;
  emptyHint: string;
  onOpen: (e: BrainEvent) => void;
}) {
  const [events, setEvents] = useState<BrainEvent[] | null>(null);
  const [err, setErr] = useState("");

  function load() {
    api.adminBrainEvents(source).then(setEvents).catch((e: any) => setErr(e.message || "failed"));
  }
  useEffect(load, [source]);

  return (
    <div className="mt-8">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">{title}</h2>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tagCls}`}>{tag}</span>
          {events && <span className="text-xs text-muted">{events.length}</span>}
        </div>
        <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={load}>Refresh</button>
      </div>

      {err && <p className="card border-loss/40 p-4 text-sm text-loss">{err}</p>}

      {events && events.length === 0 && (
        <div className="card p-8 text-center">
          <p className="text-sm text-muted">No crossovers evaluated yet.</p>
          <p className="mt-1 text-xs text-muted">{emptyHint}</p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {events?.map((e) => {
          const v = verdict(e);
          const choppy = (e.red_dots ?? 0) >= 3;
          return (
            <div key={e.id} className="card p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-md bg-ink-800 px-2 py-1 text-sm font-semibold text-white">{e.instrument}</span>
                  {(e.side === "buy" || e.side === "sell") && (
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                        e.side === "buy" ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"
                      }`}
                    >
                      {e.side === "buy" ? "▲ Long" : "▼ Short"}
                    </span>
                  )}
                </div>
                <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${v.cls}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${v.dot}`} />
                  {v.label}
                </span>
              </div>

              {e.vision_reason && (
                <p className="mt-3 line-clamp-3 text-sm italic text-slate-300">“{e.vision_reason}”</p>
              )}

              <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
                <span title="EMA crossovers detected on the chart the model saw">
                  <span className={`mr-1 inline-block h-2 w-2 rounded-full ${choppy ? "bg-loss" : "bg-slate-500"}`} />
                  {e.red_dots ?? "—"} crossovers{choppy ? " · whipsaw" : ""}
                </span>
                <span>ref <span className="text-slate-300">{fmt(e.ref_price)}</span></span>
                <span>SL <span className="text-slate-300">{fmt(e.sl_price)}</span></span>
                <span>ATR <span className="text-slate-300">{fmt(e.atr)}</span></span>
                <span className="ml-auto">{ago(e.ts)}</span>
              </div>

              {e.has_chart && (
                <button className="btn-ghost mt-4 w-full !py-2 text-xs" onClick={() => onOpen(e)}>
                  View the chart the AI saw →
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ChartLightbox({ event, onClose }: { event: BrainEvent; onClose: () => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const v = verdict(event);

  useEffect(() => {
    api
      .adminBrainChart(event.id)
      .then((r) => setSrc(`data:image/png;base64,${r.chart_b64}`))
      .catch((e: any) => setErr(e.message || "could not load chart"));
    const onKey = (k: KeyboardEvent) => k.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [event.id]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div className="card max-h-[92vh] w-full max-w-5xl overflow-auto p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-white">{event.instrument}</span>
              <span className="text-xs text-muted">{event.tj_symbol}</span>
              <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${v.cls}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${v.dot}`} />
                {v.label}
              </span>
            </div>
            {event.vision_reason && <p className="mt-1 max-w-3xl text-xs italic text-slate-400">“{event.vision_reason}”</p>}
          </div>
          <button className="btn-ghost !px-3 !py-1.5 text-sm" onClick={onClose}>Close ✕</button>
        </div>
        {err && <p className="p-8 text-center text-sm text-loss">{err}</p>}
        {!err && !src && <p className="p-12 text-center text-sm text-muted">Loading chart…</p>}
        {src && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={`${event.instrument} chart`} className="w-full rounded-lg" />
        )}
      </div>
    </div>
  );
}
