// API client for the FastAPI backend.
// Prod: Caddy maps app.diffraction.in/api/* -> backend (prefix stripped).
// Dev:  set NEXT_PUBLIC_API_BASE=http://localhost:8000

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";
const TOKEN_KEY = "aiprosperity_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  window.localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}
export function isAuthed(): boolean {
  return !!getToken();
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || res.statusText;
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data as T;
}

// ── types ──────────────────────────────────────────────────
export interface Me {
  email: string;
  name?: string | null;
  phone?: string | null;
  client_id?: string | null;
  role: string;
  subscription: { package: string; status: string } | null;
  connection: { status: string; paused: boolean } | null;
  payment_status: string;  // pending | approved | rejected
}
export interface Position {
  base: string;
  side: string;
  coin_size: number;
  entry: number;
  symbol: string;
}
export interface OrderRow {
  symbol: string;
  side: string;
  status: string;
  detail: string | null;
  size: number | null;
  fill_px: number | null;
  at: string | null;
}

// ── calls ──────────────────────────────────────────────────
export const api = {
  // Sign up a NEW user (409 if email/phone already exists).
  register: (name: string, email: string, phone: string, password: string, client_id?: string) =>
    req<{ token: string; email: string; role: string; name: string | null; client_id?: string | null }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ name, email, phone, password, client_id }),
    }),
  // Returning user: email OR phone + password.
  login: (identifier: string, password: string) =>
    req<{ token: string; email: string; role: string; name: string | null }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password }),
    }),
  changePassword: (current_password: string, new_password: string) =>
    req<{ ok: boolean }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
  me: () => req<Me>("/me"),
  connect: (api_key: string, api_secret: string) =>
    req<{ status: string }>("/connect", { method: "POST", body: JSON.stringify({ api_key, api_secret }) }),
  pause: (paused: boolean) =>
    req<{ paused: boolean }>(`/connection/pause?paused=${paused}`, { method: "POST" }),
  disconnect: () => req<{ status: string }>("/disconnect", { method: "POST" }),
  checkout: (pkg: string) =>
    req<{ ok: boolean }>("/checkout", { method: "POST", body: JSON.stringify({ package: pkg }) }),
  getPlans: () =>
    req<Record<string, { name: string; price_inr: number; months: number }>>("/plans"),

  // Offline/UPI Payment Flow
  getQRCode: () =>
    req<{ qr_base64: string; amount_inr: number; name: string; upi_id: string }>("/payment/qr"),
  uploadScreenshot: (imageBase64: string, mimeType: string) =>
    req<{ ok: boolean; status: string }>("/payment/upload-screenshot", {
      method: "POST",
      body: JSON.stringify({ image_b64: imageBase64, mime_type: mimeType }),
    }),
  deleteScreenshot: () =>
    req<{ ok: boolean }>("/payment/screenshot", { method: "DELETE" }),
  paymentStatus: () =>
    req<{
      payment_status: string;
      screenshot: { status: string; mime_type: string; image_b64: string; created_at: string; reviewed_at: string | null; review_note: string | null } | null;
    }>("/payment/status"),

  // Admin payment approvals
  adminApprovals: () =>
    req<Array<{
      user_id: string;
      name: string | null;
      email: string;
      phone: string | null;
      client_id?: string | null;
      payment_status: string;
      screenshot?: { status: string; mime_type: string; image_b64: string; created_at: string; reviewed_at: string | null; review_note: string | null };
    }>>("/admin/approvals"),
  adminApprovePayment: (userId: string, approve: boolean, note?: string) =>
    req<{ ok: boolean; status: string }>(`/admin/approve-payment/${userId}`, {
      method: "POST",
      body: JSON.stringify({ approve, note }),
    }),

  myPositions: () =>
    req<{ connected: boolean; equity: number; positions: Position[]; error?: string }>("/me/positions"),
  myOrders: () => req<OrderRow[]>("/me/orders"),
  // Tradejini (Indian F&O) — one-time auto-connect flow
  tradejiniConnect: (apiKey: string, password: string, totpSeed: string) =>
    req<{ connected: boolean; expires_at: string }>("/tradejini/connect", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey, password, totp_seed: totpSeed }),
    }),
  myTradejini: () =>
    req<{
      connected: boolean;
      status?: string;
      connected_once?: boolean;
      paused?: boolean;
      expires_at?: string | null;
      equity_inr?: number;
      positions?: Array<{ symbol: string; size: number; side: string }>;
      error?: string | null;
    }>("/me/tradejini"),
  tradejiniPause: (paused: boolean) =>
    req<{ paused: boolean }>(`/tradejini/pause?paused=${paused}`, { method: "POST" }),
  tradejiniDisconnect: () => req<{ status: string }>("/tradejini/disconnect", { method: "POST" }),

  // CoinDCX (Crypto Futures & Spot) API connections
  coindcxConnect: (apiKey: string, apiSecret: string) =>
    req<{ status: string; message: string; balance_usdt: number }>("/coindcx/connect", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret }),
    }),
  coindcxStatus: () =>
    req<{
      connected: boolean;
      status: string;
      paused: boolean;
      api_key?: string;
      balance_usdt?: number;
      updated_at?: string | null;
    }>("/coindcx/status"),
  coindcxPause: () =>
    req<{ status: string; paused: boolean }>("/coindcx/toggle-pause", { method: "POST" }),
  coindcxDisconnect: () => req<{ status: string }>("/coindcx/disconnect", { method: "POST" }),
  
  // Dhan API connections
  dhanConnect: (apiKey: string, apiSecret: string, totpSeed: string) =>
    req<{ connected: boolean }>("/admin/dhan/connect", {
      method: "POST",
      body: JSON.stringify({ client_id: apiKey, access_token: apiSecret }),
    }),
  myDhan: () =>
    req<{
      connected: boolean;
      status?: string;
      paused?: boolean;
      client_id?: string;
    }>("/me/dhan"),
  dhanPause: (paused: boolean) =>
    req<{ paused: boolean }>(`/dhan/pause?paused=${paused}`, { method: "POST" }),
  dhanDisconnect: () => req<{ status: string }>("/dhan/disconnect", { method: "POST" }),
  dhanTokenStatus: () =>
    req<{ is_valid: boolean; client_id?: string; access_token?: string }>("/admin/dhan/token-status"),
  dhanUpdateToken: (token: string) =>
    req<{ ok: boolean }>("/admin/dhan/update-token", {
      method: "POST",
      body: JSON.stringify({ access_token: token }),
    }),
  adminClients: () =>
    req<
      Array<{ id: string; email: string; name: string | null; phone: string | null; client_id: string | null; package: string | null; subscription: string | null; payment_status: string; connection: string | null; tradejini: string | null; paused: boolean | null; sandbox: boolean | null }>
    >("/admin/clients"),
  adminClientDetail: (id: string) =>
    req<{
      id: string;
      email: string;
      name: string | null;
      phone: string | null;
      client_id: string | null;
      role: string;
      subscription: { package: string; status: string; current_period_end: string | null } | null;
      connection: { status: string; paused: boolean; sandbox: boolean } | null;
      equity: number;
      positions: Position[];
      live_error: string | null;
      tradejini: {
        status: string;
        paused: boolean;
        expires_at: string | null;
        equity_inr: number;
        positions: Array<{ symbol: string; size: number; side: string }>;
        error: string | null;
      } | null;
      orders: OrderRow[];
    }>(`/admin/clients/${id}`),
  adminPause: (id: string, paused: boolean) =>
    req<{ paused: boolean }>(`/admin/clients/${id}/pause?paused=${paused}`, { method: "POST" }),
  adminForceClose: (id: string) =>
    req<{ result: string }>(`/admin/clients/${id}/force-close`, { method: "POST" }),
  adminDisconnect: (id: string) =>
    req<{ status: string }>(`/admin/clients/${id}/disconnect`, { method: "POST" }),
  adminDeleteClient: (id: string) =>
    req<{ result: string }>(`/admin/clients/${id}`, { method: "DELETE" }),
  adminChangeClientPassword: (id: string, new_password: string) =>
    req<{ result: string }>(`/admin/clients/${id}/change-password`, {
      method: "POST",
      body: JSON.stringify({ new_password }),
    }),
  adminUpdateSubscription: (id: string, current_period_end_iso: string | null) =>
    req<{ result: string }>(`/admin/clients/${id}/subscription`, {
      method: "POST",
      body: JSON.stringify({ current_period_end_iso }),
    }),
  adminLedgerClients: () =>
    req<{
      clients: Array<{
        user_id: string;
        email: string;
        name: string;
        phone: string | null;
        client_id: string | null;
        status: string;
        tradejini_connected: boolean;
        total_trades: number;
        wins: number;
        booked_pnl_inr: number;
        total_fees_inr: number;
        is_deleted: boolean;
      }>;
    }>("/admin/ledger"),
  adminClientProfits: (id: string) =>
    req<{
      client: {
        user_id: string;
        email: string;
        name: string;
        phone: string | null;
        client_id: string | null;
        status: string;
      };
      tradejini_connected: boolean;
      booked_pnl_inr: number;
      db_realized_pnl_inr: number;
      tradejini_booked_pnl_inr: number | null;
      total_fees_inr: number;
      total_trades: number;
      tradejini_positions: Array<any>;
      entries: Array<{
        id: string;
        symbol: string;
        side: string;
        size: number;
        entry_price: number;
        exit_price: number | null;
        realized_pnl_inr: number;
        realized_pnl_usd: number;
        fee_inr: number;
        status: string;
        executed_at: string | null;
      }>;
    }>(`/admin/ledger/${id}/profits`),
  adminStats: () =>
    req<{
      total_clients: number;
      active_subscribers: number;
      mrr_inr: number;
      by_package: Record<string, { name: string; price_inr: number; active: number; total: number }>;
    }>("/admin/stats"),
  updatePlan: (months: number, price_inr: number) =>
    req<{ ok: boolean; price_inr: number }>(`/admin/plans/${months}`, {
      method: "PUT",
      body: JSON.stringify({ price_inr }),
    }),
  adminBrainEvents: (source?: string) =>
    req<BrainEvent[]>(`/admin/brain/events?limit=50${source ? `&source=${encodeURIComponent(source)}` : ""}`),
  adminBrainChart: (id: string) => req<{ chart_b64: string }>(`/admin/brain/events/${id}/chart`),
  adminScreener: (sort = "volume", dir = "desc", minVolume = 0, top = 100) =>
    req<{ rows: ScreenerRow[]; count: number; source: string; feed_live: boolean; movers: Movers }>(
      `/admin/screener?sort=${encodeURIComponent(sort)}&dir=${dir}&min_volume=${minVolume}&top=${top}`,
    ),
  adminOrderbook: (symbol: string) =>
    req<OrderBookData>(`/admin/orderbook?symbol=${encodeURIComponent(symbol)}`),
  // PUBLIC — the day's candlestick setups for NSE stocks (1D). No auth needed.
  aaaSetups: () => req<AaaSetupsResponse>("/aaa/setups"),
};

export interface AaaSetup {
  symbol: string; // NSE trading symbol, e.g. "RELIANCE"
  name: string; // company name
  code: string; // pattern slug, e.g. "bullish_engulfing"
  pattern: string; // pattern display name, e.g. "Bullish Engulfing"
  direction: "bullish" | "bearish";
  action: "buy" | "short";
  candles: number; // 1 | 2 | 3 candles in the pattern
  trend: string; // trend it formed in: "up" | "down" | "flat"
  trend_move_pct: number; // net % move of the run into the pattern
  confirmation_required: boolean; // next (today's) candle should confirm before acting
  signal_date: string; // YYYY-MM-DD — the completed candle the pattern printed on
  pattern_high: number;
  pattern_low: number;
  last_close: number;
  trigger: number; // price beyond which today confirms the reversal
  stop_suggest: number; // protective stop (pattern low for buys, high for shorts)
  volume_confirm: boolean | null; // signal candle traded above its recent avg volume
  note?: string;
}
export interface AaaSetupsResponse {
  generated_at: string | null; // ISO UTC of the last scan, null if never run
  generated_at_ist: string | null; // human IST stamp
  universe: string; // e.g. "NSE equities · 1D"
  scanned: number; // stocks successfully scanned
  errors: number; // stocks skipped on a fetch error
  count: number; // total setups found
  setups: AaaSetup[];
  stale?: boolean; // true ⇒ sidecar missing (scan hasn't produced data yet)
}

export interface ScreenerRow {
  symbol: string;
  last_price: number; // Binance 24h last price (fallback when no live depth)
  change_pct: number; // 24h price change, percent
  volume: number; // 24h quote (USDT) volume
  // Live CoinAPI order-book depth — present only for the streamed watchlist.
  price: number; // live mid when streaming, else last_price
  live: boolean; // true ⇒ depth fields below are live (~1s)
  bid: number | null;
  ask: number | null;
  spread_bps: number | null; // (ask-bid)/mid in basis points
  bid_depth_usd: number | null; // Σ price·size over top-20 bids
  ask_depth_usd: number | null;
  imbalance: number | null; // bid_depth / (bid+ask depth), 0..1; >0.5 = bid-heavy
  // Bot's 48h EMA9/150 cross count (congestion gate). Only ~5 tracked symbols.
  crosses_48h: number | null;
  crosses_fresh: boolean;
  // Volume change rate: last closed 1h quote-vol ÷ median(prior 12h). >1 = hot.
  // ACTIVITY signal, not direction — weigh alongside trend, never standalone.
  volume_rate: number | null;
}

export interface MoverRow {
  symbol: string;
  price: number;
  change_pct: number;
  volume: number;
  volume_rate: number | null;
}

export interface OrderBookWall {
  price: number;
  notional: number;
  dist_pct: number;
  role: "support" | "resistance";
  mult: number;
}
export interface OrderBookData {
  symbol: string;
  mid: number;
  bid: number;
  ask: number;
  spread_bps: number;
  bid_depth: number;
  ask_depth: number;
  imbalance: number; // 0..1 bid share
  walls: OrderBookWall[];
  trend: { ema9?: number; ema150?: number; bull?: boolean; sep_pct?: number; recent_cross?: boolean; err?: string };
  congestion_48h: number | null;
  chart: { bids: { price: number; cum: number }[]; asks: { price: number; cum: number }[] };
}

export interface Movers {
  gainers: MoverRow[];
  losers: MoverRow[];
}

export interface BrainEvent {
  id: string;
  source: string; // "nifty-brain" (F&O) | "crypto-brain"
  ts: string | null;
  instrument: string;
  tj_symbol: string;
  side: "buy" | "sell" | null;
  ref_price: number | null;
  sl_price: number | null;
  atr: number | null;
  red_dots: number | null;
  vision_evaluated: boolean;
  congested: boolean | null;
  vision_reason: string | null;
  visual_sl: number | null;
  action: "published" | "vetoed" | "fallback" | "hold" | "exit-only";
  signal_id: string | null;
  has_chart: boolean;
}

export const formatInr = (n: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(n);
