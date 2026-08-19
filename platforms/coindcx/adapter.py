#!/usr/bin/env python3
"""CoinDCX INR-margin futures adapter (self-contained, no ccxt).

CoinDCX is NOT in ccxt, so this is a raw-REST client (HMAC-SHA256 auth). Everything the 2b2t bot
needs — candles, universe, live price, sizing spec, order/position lifecycle — is CoinDCX-only.

KEY QUIRKS (discovered 2026-07-02, see memory 2b2t-coindcx-deploy):
  • Cloudflare 403 (error 1010) on EVERY request unless a browser User-Agent is sent.
  • INR-margin futures: same B-<COIN>_USDT perps, but pass margin_currency_short_name="INR" and CoinDCX
    margins with INR at a conversion rate (settlement_currency_conversion_price, ~102 INR/USDT).
  • Candle API only serves [1m,15m,1h,1d] — NO 4h. get_ohlcv("4h") fetches 1h and aggregates 4→1.
  • Entry order accepts INLINE stop_loss_price + take_profit_price → native position-level bracket
    (survives bot death; no separate reduce-only orders, no poll-managed exit needed).
  • No REST futures wallet-balance endpoint exists — caller tracks equity internally.

Auth: POST body is compact JSON incl "timestamp" (ms); headers X-AUTH-APIKEY + X-AUTH-SIGNATURE =
hmac_sha256(secret, body).hexdigest(). Public GETs need only the UA.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
import ssl

try:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
except Exception:
    _ssl_ctx = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
API = "https://api.coindcx.com"
PUBLIC = "https://public.coindcx.com"


class CoinDCXError(Exception):
    pass


class CoinDCXExchangeAdapter:
    """INR-margin perpetual-futures client for the 2b2t bot."""

    def __init__(self, key: str | None = None, secret: str | None = None, timeout: float = 15.0):
        self.key = key or os.environ.get("COINDCX_LIVE_API_KEY", "")
        self.secret = secret or os.environ.get("COINDCX_LIVE_API_SECRET", "")
        self.timeout = timeout
        self._instr_cache: dict = {}
        self._prices_cache: tuple = (0.0, {})   # (fetched_at, prices dict)
        # ~102 INR per USDT (CoinDCX INR-margin conversion incl. markup); refreshed from order responses
        self.inr_per_usdt = float(os.environ.get("COINDCX_INR_PER_USDT", "102.0"))

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _get(self, url: str):
        headers = {"User-Agent": UA}
        if self.key and self.secret:
            ts = int(time.time() * 1000)
            js = json.dumps({"timestamp": ts}, separators=(",", ":"))
            sig = hmac.new(self.secret.encode(), js.encode(), hashlib.sha256).hexdigest()
            headers["X-AUTH-APIKEY"] = self.key
            headers["X-AUTH-SIGNATURE"] = sig
        req = urllib.request.Request(url, headers=headers)
        try:
            return json.load(urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_ctx))
        except urllib.error.HTTPError as e:
            raise CoinDCXError("GET %s -> %s %s" % (url, e.code, e.read().decode()[:200]))
        except Exception as e:
            raise CoinDCXError("GET %s -> %r" % (url, e))

    def _post(self, path: str, body: dict):
        body = dict(body or {})
        body["timestamp"] = int(time.time() * 1000)
        js = json.dumps(body, separators=(",", ":"))
        sig = hmac.new(self.secret.encode(), js.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            API + path, data=js.encode(),
            headers={"Content-Type": "application/json", "X-AUTH-APIKEY": self.key,
                     "X-AUTH-SIGNATURE": sig, "User-Agent": UA})
        try:
            return json.load(urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_ctx))
        except urllib.error.HTTPError as e:
            raise CoinDCXError("POST %s -> %s %s" % (path, e.code, e.read().decode()[:250]))
        except Exception as e:
            raise CoinDCXError("POST %s -> %r" % (path, e))

    # ── symbols ────────────────────────────────────────────────────────────────
    @staticmethod
    def pair(base: str) -> str:
        """2b2t 'base' (e.g. BTC) -> CoinDCX futures pair 'B-BTC_USDT'."""
        return "B-%s_USDT" % base.upper()

    @staticmethod
    def base_of(pair: str) -> str:
        """'B-BTC_USDT' -> 'BTC'."""
        return pair.split("-", 1)[-1].rsplit("_", 1)[0]

    # ── market data (candles) ──────────────────────────────────────────────────
    def _candles_1h(self, base: str, limit: int = 1000) -> list:
        url = "%s/market_data/candles?pair=%s&interval=1h&limit=%d" % (PUBLIC, self.pair(base), limit)
        rows = self._get(url)
        if not isinstance(rows, list):
            return []
        # each: {open,high,low,close,volume,time(ms)}; ensure ascending by time
        rows = sorted(rows, key=lambda r: r["time"])
        return rows

    def get_ohlcv(self, base: str, interval: str = "4h", limit: int = 210, include_forming: bool = False) -> list:
        """Return [[ts_ms,o,h,l,c,v], ...] ascending. For 4h, fetch 1h from CoinDCX and aggregate
        4-into-1 on UTC-aligned buckets (00/04/08/12/16/20). If include_forming=False, only closed bars are returned."""
        if interval in ("1h", "1m", "15m", "1d"):
            need = limit
            rows = self._candles_1h(base, limit=need) if interval == "1h" else self._raw(base, interval, need)
            return [[r["time"], float(r["open"]), float(r["high"]), float(r["low"]),
                     float(r["close"]), float(r.get("volume", 0))] for r in rows][-limit:]
        if interval not in ("4h", "30m"):
            raise CoinDCXError("unsupported interval %s" % interval)
        rows = self._candles_1h(base, limit=min(1000, (limit + 2) * 4)) if interval == "4h" else self._raw(base, "15m", min(1000, (limit + 2) * 2))
        if len(rows) < 8:
            return []
        buckets: dict = {}
        order: list = []
        for r in rows:
            bucket_size = (4 * 3600 * 1000) if interval == "4h" else (1800 * 1000)
            b = (r["time"] // bucket_size) * bucket_size
            if b not in buckets:
                buckets[b] = {"t": b, "o": float(r["open"]), "h": float(r["high"]),
                              "l": float(r["low"]), "c": float(r["close"]), "v": float(r.get("volume", 0))}
                order.append(b)
            else:
                agg = buckets[b]
                agg["h"] = max(agg["h"], float(r["high"]))
                agg["l"] = min(agg["l"], float(r["low"]))
                agg["c"] = float(r["close"])
                agg["v"] += float(r.get("volume", 0))
        out = [[buckets[b]["t"], buckets[b]["o"], buckets[b]["h"], buckets[b]["l"],
                buckets[b]["c"], buckets[b]["v"]] for b in order]
        # drop the still-forming last bucket if include_forming is False
        if not include_forming:
            now_ms = int(time.time() * 1000)
            bucket_size = (4 * 3600 * 1000) if interval == "4h" else (1800 * 1000)
            if out and (now_ms - out[-1][0]) < bucket_size:
                out = out[:-1]
        return out[-limit:]

    def _raw(self, base: str, interval: str, limit: int) -> list:
        """Fetch 100% Perpetual Futures OHLCV candle data (Binance Futures API primary)."""
        try:
            sym = f"{base}USDT"
            b_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}"
            b_req = urllib.request.Request(b_url, headers={"User-Agent": UA})
            b_rows = json.load(urllib.request.urlopen(b_req, timeout=self.timeout))
            if isinstance(b_rows, list) and len(b_rows) > 0:
                out = []
                for r in b_rows:
                    out.append({
                        "time": int(r[0]),
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5])
                    })
                return sorted(out, key=lambda r: r["time"])
        except Exception:
            pass

        # Fallback to CoinDCX candles if Binance Futures endpoint is unavailable
        url = "%s/market_data/candles?pair=%s&interval=%s&limit=%d" % (PUBLIC, self.pair(base), interval, limit)
        try:
            rows = self._get(url)
            if isinstance(rows, list) and len(rows) > 0:
                return sorted(rows, key=lambda r: r["time"])
        except Exception:
            pass

        return []

    # ── live prices + universe ─────────────────────────────────────────────────
    def _prices(self, max_age: float = 1.5) -> dict:
        now = time.time()
        if now - self._prices_cache[0] <= max_age and self._prices_cache[1]:
            return self._prices_cache[1]
        d = self._get("%s/market_data/v3/current_prices/futures/rt" % PUBLIC)
        prices = d.get("prices", {}) if isinstance(d, dict) else {}
        self._prices_cache = (now, prices)
        return prices

    def get_price(self, base: str, max_age: float = 0.2) -> float:
        """Mark price (mp) for the pair, falling back to last (ls)."""
        p = self._prices(max_age=max_age).get(self.pair(base))
        if not p:
            return 0.0
        return float(p.get("mp") or p.get("ls") or 0.0)

    def active_bases(self) -> set:
        instr = self._get("%s/exchange/v1/derivatives/futures/data/active_instruments" % API)
        return {self.base_of(p) for p in instr} if isinstance(instr, list) else set()

    def select_universe(self, top_n: int = 60, min_vol_usdt: float = 0.0) -> list:
        """Top-N CoinDCX perp bases by 24h volume (USDT), CoinDCX-sourced only."""
        active = self.active_bases()
        prices = self._prices(max_age=0)
        ranked = []
        for pr, info in prices.items():
            if not pr.startswith("B-") or not pr.endswith("_USDT"):
                continue
            base = self.base_of(pr)
            if base not in active:
                continue
            vol = float(info.get("v") or 0)           # 24h volume (USDT notional)
            if vol < min_vol_usdt:
                continue
            ranked.append((vol, base))
        ranked.sort(reverse=True)
        return [b for _, b in ranked[:top_n]]

    # ── instrument spec (sizing) ───────────────────────────────────────────────
    def instrument(self, base: str) -> dict:
        if base in self._instr_cache:
            return self._instr_cache[base]
        d = self._get("%s/exchange/v1/derivatives/futures/data/instrument?pair=%s" % (API, self.pair(base)))
        inst = d.get("instrument", {}) if isinstance(d, dict) else {}
        self._instr_cache[base] = inst
        return inst

    def floor_qty(self, base: str, qty: float) -> float:
        inst = self.instrument(base)
        step = float(inst.get("quantity_increment") or 0.001) or 0.001
        n = int(qty / step + 1e-9)
        return round(n * step, 8)

    def min_notional_usdt(self, base: str) -> float:
        return float(self.instrument(base).get("min_notional") or 60.0)

    # ── positions ──────────────────────────────────────────────────────────────
    def fetch_positions(self) -> list:
        """Active perp positions. Returns list of dicts with keys: base, side('long'/'short'),
        qty(>0), entry, sl_trigger, tp_trigger, id, locked_margin_inr."""
        raw = self._post("/exchange/v1/derivatives/futures/positions",
                         {"page": "1", "size": "100", "margin_currency_short_name": ["INR"]})
        out = []
        if not isinstance(raw, list):
            return out
        for p in raw:
            act = float(p.get("active_pos") or 0)
            if abs(act) < 1e-12:
                continue
            out.append({
                "base": self.base_of(p.get("pair", "")),
                "pair": p.get("pair", ""),
                "side": "long" if act > 0 else "short",
                "qty": abs(act),
                "entry": float(p.get("avg_price") or 0),
                "sl_trigger": p.get("stop_loss_trigger"),
                "tp_trigger": p.get("take_profit_trigger"),
                "id": p.get("id"),
                "locked_margin_inr": float(p.get("locked_margin") or 0),
                "mark_price": float(p.get("mark_price") or 0) if p.get("mark_price") else 0.0,
            })
        return out

    def get_inr_equity(self) -> float:
        """Fetch real INR equity (available balance + locked margin)."""
        raw = self._post("/exchange/v1/users/balances", {})
        if not isinstance(raw, list):
            return 0.0
        for b in raw:
            if b.get("currency") == "INR":
                return float(b.get("balance") or 0) + float(b.get("locked_balance") or 0)
        return 0.0

    def get_free_inr_balance(self) -> float:
        """Fetch available free INR balance (excluding locked margin)."""
        try:
            raw = self._post("/exchange/v1/users/balances", {})
            if isinstance(raw, list):
                for b in raw:
                    if b.get("currency") == "INR":
                        return float(b.get("balance") or 0)
        except Exception:
            pass
        return 0.0

    def fetch_executed_trade_vwap(self, base: str, side: str = "sell") -> tuple[float, float, float]:
        """Fetch exact VWAP execution price, total quantity, and fees from CoinDCX API for recent fills."""
        pair_name = self.pair(base)
        try:
            raw = self._post("/exchange/v1/derivatives/futures/trades", {"pair": pair_name})
            if isinstance(raw, list) and len(raw) > 0:
                now_ms = time.time() * 1000
                recent = [
                    f for f in raw
                    if f.get("side", "").lower() == side.lower()
                    and (now_ms - float(f.get("timestamp", 0))) <= 600000
                ]
                if recent:
                    tot_qty = sum(float(f.get("quantity") or 0) for f in recent)
                    tot_val = sum(float(f.get("quantity") or 0) * float(f.get("price") or 0) for f in recent)
                    tot_fees = sum(float(f.get("fee_amount") or 0) for f in recent)
                    if tot_qty > 0:
                        vwap_px = tot_val / tot_qty
                        return float(vwap_px), float(tot_qty), float(tot_fees)
        except Exception:
            pass
        return 0.0, 0.0, 0.0

    # ── orders ─────────────────────────────────────────────────────────────────
    def market_open_bracket(self, base: str, is_buy: bool, qty: float, leverage: int,
                            sl_price: float, tp_price: float | None = None) -> dict:
        """Market entry WITH native inline SL bracket (INR margin). Take profit is optional to allow trailing SL to run uncapped."""
        inst = self.instrument(base)
        maxlev = float(inst.get("max_leverage_short" if not is_buy else "max_leverage_long") or leverage)
        lev = int(min(int(leverage), int(maxlev))) or 1
        # Ensure SL is strictly lower than current mark price for long orders
        if is_buy:
            ltp = float(inst.get("last_price") or inst.get("mark_price") or 0)
            if ltp > 0 and sl_price >= ltp:
                sl_price = ltp * 0.995

        order = {
            "side": "buy" if is_buy else "sell",
            "pair": self.pair(base),
            "order_type": "market_order",
            "total_quantity": self.floor_qty(base, qty),
            "leverage": lev,
            "margin_type": "isolated",
            "margin_currency_short_name": "INR",
            "time_in_force": "good_till_cancel",
            "stop_loss_price": self._round_px(base, sl_price),
        }
        if tp_price and tp_price > 0:
            order["take_profit_price"] = self._round_px(base, tp_price)
            
        try:
            r = self._post("/exchange/v1/derivatives/futures/orders/create", {"order": order})
        except CoinDCXError as e:
            err_str = str(e)
            if "Max allowed leverage" in err_str:
                import re
                m = re.search(r"Max allowed leverage[^\d]*(\d+\.?\d*)", err_str)
                if m:
                    max_allowed = int(float(m.group(1)))
                    if max_allowed >= 1 and max_allowed < order["leverage"]:
                        order["leverage"] = max_allowed
                        r = self._post("/exchange/v1/derivatives/futures/orders/create", {"order": order})
                    else:
                        raise
                else:
                    raise
            else:
                raise

        od = r[0] if isinstance(r, list) and r else (r if isinstance(r, dict) else {})
        conv = od.get("settlement_currency_conversion_price")
        if conv:
            self.inr_per_usdt = float(conv)
        return od

    def limit_open_bracket(self, base: str, is_buy: bool, qty: float, price: float, leverage: int,
                           sl_price: float, tp_price: float | None = None) -> dict:
        """Limit entry WITH native inline SL bracket (INR margin) and optional Take Profit."""
        inst = self.instrument(base)
        maxlev = float(inst.get("max_leverage_short" if not is_buy else "max_leverage_long") or leverage)
        lev = int(min(int(leverage), int(maxlev))) or 1

        order = {
            "side": "buy" if is_buy else "sell",
            "pair": self.pair(base),
            "order_type": "limit_order",
            "price": self._round_px(base, price),
            "total_quantity": self.floor_qty(base, qty),
            "leverage": lev,
            "margin_type": "isolated",
            "margin_currency_short_name": "INR",
            "time_in_force": "good_till_cancel",
            "stop_loss_price": self._round_px(base, sl_price),
        }
        if tp_price and tp_price > 0:
            order["take_profit_price"] = self._round_px(base, tp_price)

        try:
            r = self._post("/exchange/v1/derivatives/futures/orders/create", {"order": order})
        except CoinDCXError as e:
            err_str = str(e)
            if "Max allowed leverage" in err_str:
                import re
                m = re.search(r"Max allowed leverage[^\d]*(\d+\.?\d*)", err_str)
                if m:
                    max_allowed = int(float(m.group(1)))
                    if max_allowed >= 1 and max_allowed < order["leverage"]:
                        order["leverage"] = max_allowed
                        r = self._post("/exchange/v1/derivatives/futures/orders/create", {"order": order})
                    else:
                        raise
                else:
                    raise
            else:
                raise

        od = r[0] if isinstance(r, list) and r else (r if isinstance(r, dict) else {})
        conv = od.get("settlement_currency_conversion_price")
        if conv:
            self.inr_per_usdt = float(conv)
        return od

    def _round_px(self, base: str, px: float) -> float:
        if px <= 0:
            return 0.0
        try:
            inst = self.instrument(base)
            inc = float(inst.get("price_increment") or 0.0) if isinstance(inst, dict) else 0.0
            if inc > 0:
                n = round(px / inc)
                val = n * inc
                return float(f"{val:.8f}")
        except Exception:
            pass
        return float(f"{px:.8f}")

    def cancel(self, oid: str):
        return self._post("/exchange/v1/derivatives/futures/orders/cancel", {"id": oid})

    def position_exit(self, pos_id: str) -> dict:
        """Market-close a position by id (native bracket cancelled with it)."""
        return self._post("/exchange/v1/derivatives/futures/positions/exit", {"id": pos_id})

    def update_tpsl(self, pos_id: str, base: str, sl_price: float | None = None, tp_price: float | None = None) -> dict:
        """Update active position Stop Loss / Take Profit prices on CoinDCX."""
        pid = pos_id
        if not pid or pid == "shadow":
            try:
                for p in self.fetch_positions():
                    if p.get("base") == base and p.get("id"):
                        pid = p.get("id")
                        break
            except Exception:
                pass
        if not pid:
            raise CoinDCXError("No active position ID found for %s" % base)
            
        body = {"id": pid}
        if sl_price is not None:
            body["stop_loss"] = {
                "order_type": "stop_market",
                "stop_price": self._round_px(base, sl_price)
            }
        if tp_price is not None:
            body["take_profit"] = {
                "order_type": "take_profit_market",
                "stop_price": self._round_px(base, tp_price)
            }
        return self._post("/exchange/v1/derivatives/futures/positions/create_tpsl", body)

    def cancel_open_orders(self, base: str):
        try:
            return self._post("/exchange/v1/derivatives/futures/positions/cancel_all_open_orders",
                              {"pair": self.pair(base)})
        except CoinDCXError:
            return None

    # ── order book (depth gate) ────────────────────────────────────────────────
    def order_book(self, base: str, depth: int = 20):
        """Return (bids, asks) as sorted (price, qty) lists — bids desc, asks asc."""
        d = self._get("%s/market_data/v3/orderbook/%s-futures/%d" % (PUBLIC, self.pair(base), depth))
        bids = d.get("bids", {}) if isinstance(d, dict) else {}
        asks = d.get("asks", {}) if isinstance(d, dict) else {}
        b = sorted(((float(p), float(q)) for p, q in bids.items()), reverse=True)
        a = sorted((float(p), float(q)) for p, q in asks.items())
        return b, a

    def depth_fillable_qty(self, base: str, is_buy: bool, ref_price: float, max_slip_pct: float) -> float:
        """Max base-qty fillable within max_slip_pct of ref_price: asks ≤ ref*(1+slip) for a buy,
        bids ≥ ref*(1-slip) for a sell. Used to decline/trim entries the thin book can't absorb."""
        try:
            bids, asks = self.order_book(base)
        except CoinDCXError:
            return 0.0
        if is_buy:
            lim = ref_price * (1 + max_slip_pct)
            return sum(q for p, q in asks if p <= lim)
        lim = ref_price * (1 - max_slip_pct)
        return sum(q for p, q in bids if p >= lim)

    # ── real realized PnL (exchange fills) ─────────────────────────────────────
    def realized_pnl(self, base: str, since_ts_ms: float, is_long: bool):
        """Real net PnL in USDT for the round-trip on `base` from actual fills since since_ts_ms
        (gross − entry_fee − exit_fee). Returns None if the fills aren't visible yet (caller falls
        back to a mark-based estimate). VWAP-matches the entry and exit legs."""
        tr = self._post("/exchange/v1/derivatives/futures/trades", {"page": "1", "size": "100"})
        if not isinstance(tr, list):
            return None
        pair = self.pair(base)
        entry_side = "buy" if is_long else "sell"
        exit_side = "sell" if is_long else "buy"
        cutoff = since_ts_ms - 2000                       # 2s grace for clock skew
        ef = [t for t in tr if t.get("pair") == pair and t.get("side") == entry_side
              and float(t.get("timestamp") or 0) >= cutoff]
        xf = [t for t in tr if t.get("pair") == pair and t.get("side") == exit_side
              and float(t.get("timestamp") or 0) >= cutoff]
        if not ef or not xf:
            return None

        def vwap_fee(fills):
            q = sum(float(f["quantity"]) for f in fills)
            notl = sum(float(f["quantity"]) * float(f["price"]) for f in fills)
            fee = sum(float(f.get("fee_amount") or 0) for f in fills)
            return (notl / q if q else 0.0), fee, q

        evwap, efee, eq = vwap_fee(ef)
        xvwap, xfee, xq = vwap_fee(xf)
        q = min(eq, xq)
        gross = (xvwap - evwap) * q if is_long else (evwap - xvwap) * q
        return gross - efee - xfee
