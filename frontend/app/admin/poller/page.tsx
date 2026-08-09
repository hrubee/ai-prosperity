
"use client";
import { api } from "@/lib/api";

import { useEffect, useState, useRef } from "react";
import { Logo } from "@/components/nav";
import { AdminNav } from "@/components/AdminNav";
import { isAuthed } from "@/lib/api";

type LogEntry = {
  type: string;
  timestamp?: string;
  account?: string;
  message?: string;
  data?: any;
  response?: any;
  dhan_order_id?: string;
};

type DhanOrderState = {
  orderId: string;
  symbol: string;
  action: string;
  qty: number;
  status: string;
  timestamp: string;
  dateStr?: string;
  price?: number;
};

type TradejiniExecState = {
  account: string;
  status: "pending" | "success" | "failed";
  response?: any;
  message?: string;
  timestamp: string;
};

export default function PollerMonitor() {
  const [dhanOrders, setDhanOrders] = useState<DhanOrderState[]>([]);
  const [executions, setExecutions] = useState<Record<string, TradejiniExecState[]>>({});
  const [status, setStatus] = useState<"Disconnected" | "Live">("Disconnected");
  
  const [tokenInput, setTokenInput] = useState("");
  const [updatingToken, setUpdatingToken] = useState(false);

  const [tokenStatus, setTokenStatus] = useState<{ expires_at: string | null; is_valid: boolean } | null>(null);

  const fetchTokenStatus = async () => {
    try {
      const res = await api.dhanTokenStatus();
      setTokenStatus(res);
    } catch (e) {}
  };

  useEffect(() => {
    fetchTokenStatus();
  }, []);


  async function handleUpdateToken(e: React.FormEvent) {
    e.preventDefault();
    if (!tokenInput.trim()) return;
    setUpdatingToken(true);
    try {
      await api.dhanUpdateToken(tokenInput.trim());
      setTokenInput("");
      await fetchTokenStatus();
      alert("Token updated and Poller restarted successfully!");
    } catch (err: any) {
      alert("Failed to update token: " + err.message);
    } finally {
      setUpdatingToken(false);
    }
  }

  const [lastHeartbeat, setLastHeartbeat] = useState<string>("Waiting...");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    
    // Fetch initial history just to parse out recent trades
    fetch("/copier/api/logs")
      .then(r => r.json())
      .then((data: LogEntry[]) => {
        processLogs(data);
      })
      .catch(() => {});

    function connect() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/copier/ws/logs`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("Live");
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          processLogs([data]);
        } catch (e) {
          // ignore
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

  function processLogs(logs: LogEntry[]) {
    // Process oldest first if it's an array to build state naturally
    const sorted = [...logs];
    
    sorted.forEach(log => {
      let ts = null;
      let dateStr = "";
      if (log.type === "signal_received" && log.data && (log.data.updateTime || log.data.createTime)) {
        const dStr = log.data.updateTime || log.data.createTime;
        const d = new Date(dStr.replace(' ', 'T') + '+05:30');
        ts = d.toLocaleTimeString();
        dateStr = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
      }
      if (!ts) {
        const d = log.timestamp ? new Date(log.timestamp) : new Date();
        ts = d.toLocaleTimeString();
        dateStr = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
      }
      
      if (log.type === "signal_received" && log.data) {
        if (log.data.type === "status") {
          setLastHeartbeat(`${ts} - ${log.data.message}`);
          return;
        }
        
        // Is a Dhan Order?
        const oid = log.data.orderId;
        if (oid) {
          setDhanOrders(prev => {
            if (prev.find(o => o.orderId === oid && o.status === log.data.orderStatus)) return prev;
            // Remove older state for this order if exists
            const filtered = prev.filter(o => o.orderId !== oid);
            return [{
              orderId: oid,
              symbol: log.data.tradingSymbol || "Unknown",
              action: log.data.transactionType === "B" || log.data.transactionType === "BUY" ? "BUY" : "SELL",
              qty: parseInt(log.data.quantity || "0"),
              status: log.data.orderStatus,
              timestamp: ts,
              dateStr: dateStr,
              price: log.data.price
            }, ...filtered].slice(0, 100);
          });
        }
      } else if (log.type === "trade_placed" && log.dhan_order_id) {
        setExecutions(prev => {
          const oid = log.dhan_order_id as string;
          const ex = prev[oid] || [];
          return {
            ...prev,
            [oid]: [...ex, {
              account: log.account || "Unknown",
              status: "success",
              response: log.response,
              timestamp: ts
            }]
          };
        });
      } else if (log.type === "error" && log.dhan_order_id) {
        setExecutions(prev => {
          const oid = log.dhan_order_id as string;
          const ex = prev[oid] || [];
          return {
            ...prev,
            [oid]: [...ex, {
              account: log.account || "Unknown",
              status: "failed",
              message: log.message,
              timestamp: ts
            }]
          };
        });
      }
    });
  }

  return (
    <main className="flex h-screen flex-col bg-ink-950">
      <AdminNav />

      <div className="flex items-center justify-between px-6 py-4 border-b border-ink-800 bg-ink-900/30">
        <h1 className="text-lg font-semibold text-white">Dhan Poller & Execution Monitor</h1>
        <div className="flex items-center gap-4 text-xs font-mono">
          <span className="text-muted">Last Heartbeat:</span>
          <span className="flex items-center gap-2 text-blue-400 font-semibold bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">
            {lastHeartbeat && <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
            </span>}
            {lastHeartbeat || "Awaiting signal..."}
          </span>
          <span className={`pill font-semibold ${status === "Live" ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
            WebSocket: {status}
          </span>
        </div>
      </div>

      
      <div className="flex-1 overflow-hidden p-6 flex flex-col">

        <div className="mb-4 rounded-xl border border-ink-800 bg-ink-900/50 p-4">
          <form onSubmit={handleUpdateToken} className="flex flex-col sm:flex-row sm:items-end gap-4">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-medium text-slate-400">
                Daily Dhan Access Token (Valid for 24h)
              </label>

              <div className="flex items-center gap-2 mt-2">
                {tokenStatus ? (
                  tokenStatus.is_valid ? (
                    <span className="text-xs text-green-400 font-semibold bg-green-500/10 px-2 py-0.5 rounded border border-green-500/20">
                      ✓ Active (Expires: {new Date(tokenStatus.expires_at || "").toLocaleString()})
                    </span>
                  ) : (
                    <span className="text-xs text-red-400 font-semibold bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20 animate-pulse">
                      ⚠ Token Expired or Invalid
                    </span>
                  )
                ) : null}
              </div>

              <input
                type="password"
                placeholder="Paste your new JWT Token from hq.dhan.co here..."
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                className="w-full rounded bg-ink-950 px-3 py-2 text-sm text-slate-200 outline-none ring-1 ring-inset ring-ink-800 focus:ring-gold-500/50"
              />
            </div>
            <button
              type="submit"
              disabled={updatingToken || !tokenInput.trim()}
              className="rounded bg-gold-600 px-4 py-2 text-sm font-semibold text-ink-950 transition-colors hover:bg-gold-500 disabled:opacity-50"
            >
              {updatingToken ? "Updating & Restarting..." : "Update Token"}
            </button>
          </form>
        </div>

        {/* Headers */}
        <div className="grid grid-cols-2 gap-6 mb-4 shrink-0">
          <div className="rounded-xl border border-ink-800 bg-ink-900/80 p-4 font-semibold text-blue-400 flex justify-between items-center">
            <span>Pooling Mechanism: Dhan Orders</span>
            <span className="text-xs bg-blue-500/20 px-2 py-1 rounded">Scanning Active</span>
          </div>
          <div className="rounded-xl border border-ink-800 bg-ink-900/80 p-4 font-semibold text-green-400">
            Tradejini Replication Results
          </div>
        </div>

        {/* Scrollable Body */}
        <div className="flex-1 overflow-y-auto space-y-4 pr-2">
          {(() => {
            if (dhanOrders.length === 0) {
              return <div className="text-center text-muted py-10">No orders scanned yet.</div>;
            }

            const grouped: Record<string, typeof dhanOrders> = {};
            dhanOrders.forEach(order => {
              const d = order.dateStr || "Unknown Date";
              if (!grouped[d]) grouped[d] = [];
              grouped[d].push(order);
            });

            return Object.keys(grouped).map(date => (
              <div key={date}>
                <div className="text-sm font-bold text-slate-400 mt-6 mb-4 border-b border-ink-800 pb-2">{date}</div>
                <div className="space-y-4">
                  {grouped[date].map(order => {
                                        const allResults = executions[order.orderId] || [];
                    const results = allResults.filter((res: any) => {
                      if (res.account === "radianmedia" && res.status === "success") {
                        return false;
                      }
                      return true;
                    });
                    return (
                      <div key={order.orderId + order.status} className="grid grid-cols-2 gap-6">
                        {/* Left Card: Dhan */}
                        <div className="border border-ink-800 bg-ink-900/50 rounded-xl p-4">
                          <div className="flex justify-between items-start mb-3">
                            <div className="font-mono text-xs text-muted">{order.timestamp}</div>
                            <div className="text-xs bg-black/40 border border-ink-800 px-2 py-0.5 rounded font-mono text-slate-300">ID: {order.orderId}</div>
                          </div>
                          <div className="flex items-center gap-3 font-bold text-white mb-3 text-lg">
                            <span className={order.action === "BUY" ? "text-green-400" : "text-red-400"}>{order.action}</span>
                            <span className="bg-ink-800/50 px-2 py-0.5 rounded">{order.qty}x</span>
                            <span>{order.symbol}</span>
                          </div>
                          <div className="flex justify-between items-center text-sm border-t border-ink-800 pt-3">
                            <span className="text-slate-400">Price: <span className="text-white font-mono">{order.price || 'MKT'}</span></span>
                            <span className="bg-blue-500/10 text-blue-400 border border-blue-500/20 px-3 py-1 rounded text-xs font-semibold uppercase tracking-wider">{order.status}</span>
                          </div>
                        </div>

                        {/* Right Card: Tradejini */}
                        <div className="border border-ink-800 bg-ink-900/30 rounded-xl p-4 flex flex-col justify-center">
                          {results.length === 0 ? (
                            <div className="text-center text-slate-500 text-sm animate-pulse font-medium bg-black/20 p-3 rounded-lg border border-dashed border-ink-800">Processing replication...</div>
                          ) : (
                            <div className="space-y-2">
                              {results.map((res, i) => (
                                <div key={i} className="flex justify-between items-center text-sm p-3 bg-black/40 border border-ink-800 rounded-lg">
                                  <span className="font-mono text-muted text-xs">{res.timestamp}</span>
                                  <span className="font-semibold text-slate-300 w-24 truncate">{res.account}</span>
                                  {res.status === "success" ? (
                                    <span className="bg-green-500/10 text-green-400 px-3 py-1 rounded text-xs font-bold border border-green-500/20">
                                      SUCCESS {res.response?.order_id ? `(${res.response.order_id})` : ''}
                                    </span>
                                  ) : (
                                    <span className="bg-red-500/10 text-red-400 px-3 py-1 rounded text-xs font-bold border border-red-500/20 max-w-[200px] truncate" title={res.message}>
                                      FAILED: {res.message}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ));
          })()}
        </div>
      </div>
    </main>
  );
}
