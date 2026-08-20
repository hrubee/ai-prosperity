"use client";

import { useEffect, useState, useRef } from "react";
import { Logo } from "@/components/nav";
import { AdminNav } from "@/components/AdminNav";
import { isAuthed, api } from "@/lib/api";

type LogEntry = {
  type: string;
  account?: string;
  message?: string;
  data?: any;
  response?: any;
};

export default function CopierMonitor() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [status, setStatus] = useState<"Disconnected" | "Live">("Disconnected");
  const [copierState, setCopierState] = useState<{
    enabled: boolean;
    paused_today: boolean;
    paused_for_date: string | null;
    auto_resumes_at: string | null;
  }>({
    enabled: true,
    paused_today: false,
    paused_for_date: null,
    auto_resumes_at: null,
  });
  const [toggling, setToggling] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  async function fetchStatus() {
    try {
      const res = await fetch("/copier/api/copier-status");
      const data = await res.json();
      setCopierState({
        enabled: Boolean(data.enabled),
        paused_today: Boolean(data.paused_today),
        paused_for_date: data.paused_for_date || null,
        auto_resumes_at: data.auto_resumes_at || null,
      });
    } catch {}
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    
    // Fetch initial history
    fetch("/copier/api/logs")
      .then(r => r.json())
      .then((data: LogEntry[]) => {
        setLogs(data.reverse()); // latest first
      })
      .catch(() => {});

    fetchStatus();

    function connect() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/copier/ws/logs`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("Live");
        setLogs(prev => [{ type: "system", message: "Connected to monitoring server." }, ...prev].slice(0, 200));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setLogs(prev => [data, ...prev].slice(0, 200));
          if (data.type === "status" || data.state) {
            fetchStatus();
          }
        } catch (e) {
          setLogs(prev => [{ type: "system", message: event.data }, ...prev].slice(0, 200));
        }
      };

      ws.onclose = () => {
        setStatus("Disconnected");
        setTimeout(connect, 3000);
      };
    }
    
    connect();

    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  async function handlePauseToday() {
    setToggling(true);
    try {
      const res = await fetch("/copier/api/copier-pause-today", { method: "POST" });
      const data = await res.json();
      setCopierState({
        enabled: false,
        paused_today: true,
        paused_for_date: data.paused_for_date || null,
        auto_resumes_at: "Tomorrow at 08:30 IST",
      });
    } catch(e) {
      console.error("Failed to pause copier today:", e);
    } finally {
      setToggling(false);
    }
  }

  async function handleResume() {
    setToggling(true);
    try {
      const res = await fetch("/copier/api/copier-resume", { method: "POST" });
      const data = await res.json();
      setCopierState({
        enabled: true,
        paused_today: false,
        paused_for_date: null,
        auto_resumes_at: null,
      });
    } catch(e) {
      console.error("Failed to resume copier:", e);
    } finally {
      setToggling(false);
    }
  }

  function formatLogContent(log: LogEntry) {
    if (log.type === 'signal_received') {
      return `Incoming Payload: ${JSON.stringify(log.data)}`;
    } else if (log.type === 'trade_placed') {
      return `Account [${log.account}]: ${JSON.stringify(log.response)}`;
    } else if (log.type === 'order_update') {
      return `Account [${log.account}]: ${JSON.stringify(log.data)}`;
    } else if (log.type === 'error') {
      return log.message || JSON.stringify(log);
    } else {
      return log.message || JSON.stringify(log);
    }
  }

  function getBadgeColor(type: string) {
    switch(type) {
      case 'signal_received': return 'bg-blue-500/20 text-blue-400';
      case 'trade_placed': return 'bg-green-500/20 text-green-400';
      case 'order_update': return 'bg-yellow-500/20 text-yellow-400';
      case 'error': return 'bg-red-500/20 text-red-400';
      default: return 'bg-slate-500/20 text-slate-400';
    }
  }

  return (
    <main className="flex h-screen flex-col bg-ink-950">
      <AdminNav />

      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 border-b border-ink-800 bg-ink-900/30">
        <div>
          <h1 className="text-lg font-semibold text-white">Copier Monitor</h1>
          <div className="text-xs text-slate-400">
            {copierState.paused_today ? (
              <span className="text-amber-400 font-medium">
                🛑 Paused for Today ({copierState.paused_for_date}) — Auto-resumes tomorrow at 08:30 IST
              </span>
            ) : copierState.enabled ? (
              <span className="text-emerald-400 font-medium">
                🟢 Live Active Copying across all connected client accounts
              </span>
            ) : (
              <span className="text-red-400 font-medium">
                ⏸️ Copier Paused
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          {copierState.paused_today || !copierState.enabled ? (
            <button 
              onClick={handleResume} 
              disabled={toggling}
              className="px-3.5 py-1.5 rounded-lg text-xs font-semibold cursor-pointer bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border border-emerald-500/30 transition disabled:opacity-50"
            >
              {toggling ? "Resuming..." : "▶️ Resume Trading Now"}
            </button>
          ) : (
            <button 
              onClick={handlePauseToday} 
              disabled={toggling}
              className="px-3.5 py-1.5 rounded-lg text-xs font-semibold cursor-pointer bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/30 transition disabled:opacity-50"
            >
              {toggling ? "Pausing..." : "⏸️ Pause Trading for Today"}
            </button>
          )}

          <span className={`pill text-xs font-semibold ${status === "Live" ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
            {status}
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-hidden p-6">
        <div className="flex h-full flex-col overflow-hidden rounded-xl border border-ink-800 bg-ink-900/30">
          <div className="border-b border-ink-800 bg-black/20 p-4 font-semibold text-white">
            Real-Time Event Stream
          </div>
          <div className="flex-1 overflow-y-auto p-4 font-mono text-sm">
            <div className="flex flex-col gap-2">
              {logs.map((log, i) => (
                <div key={i} className={`rounded-md border-l-4 border-slate-500 bg-ink-900/50 p-3`} 
                     style={{ borderLeftColor: log.type === 'error' ? '#ef4444' : log.type === 'trade_placed' ? '#10b981' : log.type === 'signal_received' ? '#3b82f6' : '#94a3b8' }}>
                  <div className="mb-1 text-xs text-muted">
                    {new Date().toLocaleTimeString()}
                  </div>
                  <div>
                    <span className={`mr-2 inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${getBadgeColor(log.type)}`}>
                      {log.type.replace('_', ' ')}
                    </span>
                    <span className="break-all whitespace-pre-wrap text-slate-300">
                      {formatLogContent(log)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
