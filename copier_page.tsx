"use client";

import { useEffect, useState, useRef } from "react";
import { SiteHeader, Footer } from "@/components/nav";

interface LogEntry {
  type: string;
  data?: any;
  message?: string;
  account?: string;
  response?: any;
  time: string;
}

export default function CopierDashboard() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [status, setStatus] = useState<"Disconnected" | "Live">("Disconnected");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let isSubscribed = true;

    async function init() {
      // 1. Fetch historical logs
      try {
        const res = await fetch("/copier/api/logs");
        if (res.ok) {
          const history = await res.json();
          if (isSubscribed && history && history.length > 0) {
            // History is chronological, we want to prepend them to state (newest first)
            // But since state is initially empty, we can just reverse the array
            // and add a generic time if missing
            const formattedHistory = history.map((log: any) => ({
              ...log,
              time: log.time || new Date().toLocaleTimeString(),
            })).reverse();
            setLogs(formattedHistory);
          }
        }
      } catch (e) {
        console.error("Failed to fetch logs history", e);
      }

      // 2. Connect WebSocket
      if (!isSubscribed) return;
      
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/copier/ws/logs`;
      
      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("Live");
        addLog({
          type: "system",
          message: "Connected to Tradejini Multi-Account Monitor.",
          time: new Date().toLocaleTimeString(),
        });
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          addLog({
            ...data,
            time: new Date().toLocaleTimeString(),
          });
        } catch (e) {
          addLog({
            type: "system",
            message: String(event.data),
            time: new Date().toLocaleTimeString(),
          });
        }
      };

      ws.onclose = () => {
        setStatus("Disconnected");
        setTimeout(init, 3000); // Auto reconnect
      };
    }

    init();

    return () => {
      isSubscribed = false;
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  function addLog(newLog: LogEntry) {
    setLogs((prev) => {
      const updated = [newLog, ...prev];
      if (updated.length > 100) return updated.slice(0, 100);
      return updated;
    });
  }

  const getBadgeColor = (type: string) => {
    switch (type) {
      case "signal_received":
        return "bg-blue-500 text-white";
      case "trade_placed":
        return "bg-green-500 text-white";
      case "order_update":
        return "bg-yellow-500 text-black";
      case "error":
        return "bg-red-500 text-white";
      default:
        return "bg-slate-600 text-white";
    }
  };

  const formatContent = (log: LogEntry) => {
    if (log.type === "signal_received") {
      return `Incoming Payload: ${JSON.stringify(log.data)}`;
    } else if (log.type === "trade_placed") {
      return `Account [${log.account}]: ${JSON.stringify(log.response)}`;
    } else if (log.type === "order_update") {
      return `Account [${log.account}]: ${JSON.stringify(log.data)}`;
    } else if (log.type === "error") {
      return log.message || JSON.stringify(log);
    } else {
      return log.message || JSON.stringify(log);
    }
  };

  return (
    <>
      <SiteHeader />
      <main className="container-x py-10 flex flex-col min-h-screen">
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">Copier Monitor</h1>
            <p className="text-muted mt-2">Tradejini Multi-Account Execution Logs</p>
          </div>
          <span
            className={`px-4 py-1.5 rounded-full text-sm font-semibold border ${
              status === "Live"
                ? "bg-gain/20 text-gain border-gain/30"
                : "bg-red-500/20 text-red-400 border-red-500/30"
            }`}
          >
            {status}
          </span>
        </div>

        <div className="card flex-1 flex flex-col p-0 overflow-hidden border-ink-800">
          <div className="p-4 border-b border-ink-800 bg-ink-950/50">
            <h2 className="text-sm font-semibold text-white tracking-wide uppercase">Real-Time Event Stream</h2>
          </div>
          <div className="flex-1 p-4 overflow-y-auto font-mono text-xs sm:text-sm space-y-3 bg-ink-950">
            {logs.length === 0 ? (
              <div className="text-slate-500 italic">Waiting for events...</div>
            ) : (
              logs.map((log, i) => (
                <div
                  key={i}
                  className="p-3 rounded bg-slate-900 border-l-4"
                  style={{
                    borderLeftColor:
                      log.type === "signal_received"
                        ? "#3b82f6"
                        : log.type === "trade_placed"
                        ? "#10b981"
                        : log.type === "error"
                        ? "#ef4444"
                        : log.type === "order_update"
                        ? "#f59e0b"
                        : "#94a3b8",
                  }}
                >
                  <div className="text-slate-400 text-xs mb-1">{log.time}</div>
                  <div className="flex flex-col sm:flex-row sm:items-start gap-2">
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${getBadgeColor(
                        log.type
                      )}`}
                    >
                      {log.type.replace("_", " ")}
                    </span>
                    <span className="text-slate-300 break-all whitespace-pre-wrap flex-1">
                      {formatContent(log)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
