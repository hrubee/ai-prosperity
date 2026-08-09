"""Crypto market screener for the admin panel.

The board itself (symbol / last price / 24h change% / 24h quote volume, ranked) is
sourced from Binance's public USDT-M futures 24h ticker — free, keyless, works
from the VPS, and gives the *whole* market in one call. That is the reliable
backbone and the sort key the operator asked for ("highest volume to the top").

Two enrichments are layered on, both read from sidecar files (never blocking on a
network call inside the request):

  - **Order-book depth** (bid/ask, spread, depth, imbalance) from the coinapi-feed
    sidecar's screener_snapshot.json. CoinAPI streams this live (~1s) for the
    top-N watchlist; only those symbols carry depth, the rest fall back to the
    Binance last price. See coinapi_screener_feed.py for why a sidecar.
  - **48h EMA-cross count** from the cross-alerter sidecar's SQLite DB — the bot's
    congestion gate, the genuine selectivity signal. Only the ~5 bot-tracked
    symbols populate; everything else shows blank.

Fetches fail fast (5s timeout) and the caller maps any failure to a clean 502 the
admin UI renders as an error card — never a hang or an opaque 500. The
enrichments degrade silently to None (a missing/stale sidecar must never blank the
board).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

import httpx

BINANCE_FUTURES_24HR_URL = "https://api.coindcx.com/exchange/ticker"
VALID_SORTS = ("volume", "change", "abs_change")

_CACHE_TTL = 4.0  # seconds — the admin panel polls ~5s; a 4s server cache keeps
# data fresh-every-poll while collapsing all viewers to ≤15 Binance calls/min
# (weight 40 each ≈ 600/min, well under the 2400/min IP limit shared with the
# live trading bot).
_lock = threading.Lock()
_cache = {"rows": None, "ts": 0.0}

# Depth from the coinapi-feed snapshot is only trusted this fresh (the sidecar
# writes ~1×/s; older than this means it's idle/down → fall back to Binance px).
_FEED_MAX_AGE = 6.0
_CROSS_WINDOW_HOURS = 48          # matches the bot's 576-bar (48h on 5m) gate
_CROSS_SYNC_STALE_MS = 180_000    # cross-alerter rewrites sync_state each sweep


def parse_screener(payload):
    rows = []
    for t in payload or []:
        symbol = t.get("market", "")
        if not symbol.endswith("USDT") or "_" in symbol:
            continue
        try:
            change_pct = float(t.get("change_24_hour"))
            last_price = float(t.get("last_price"))
            volume = float(t.get("volume")) * last_price
        except (TypeError, ValueError, AttributeError):
            continue
        rows.append({
            "symbol": symbol,
            "last_price": last_price,
            "change_pct": change_pct,
            "volume": volume,
        })
    return rows


def filter_sort_screener(rows, sort="volume", ascending=False, min_volume=0.0, top=0):
    """min-volume floor → order by key/direction → optional top-N cap. Pure.
    sort: 'volume' | 'change' (signed 24h change%) | 'abs_change' (|change%|)."""
    if sort not in VALID_SORTS:
        sort = "volume"
    out = [r for r in rows if r.get("volume", 0.0) >= (min_volume or 0.0)]
    if sort == "change":
        key = lambda r: r.get("change_pct", 0.0)
    elif sort == "abs_change":
        key = lambda r: abs(r.get("change_pct", 0.0))
    else:
        key = lambda r: r.get("volume", 0.0)
    out.sort(key=key, reverse=not ascending)
    if top and top > 0:
        out = out[:top]
    return out


def _fetch_board():
    """Return the parsed board, cached for _CACHE_TTL. On a fresh cache miss it
    refetches; if the refetch fails but a previous snapshot exists, that stale
    snapshot is served (a transient Binance blip shouldn't blank the panel). With
    no snapshot at all, the exception propagates for the caller to map to 502."""
    now = time.time()
    with _lock:
        if _cache["rows"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["rows"]
        stale = _cache["rows"]
    try:
        resp = httpx.get(BINANCE_FUTURES_24HR_URL, timeout=5.0)
        resp.raise_for_status()
        rows = parse_screener(resp.json())
    except Exception:
        if stale is not None:
            return stale
        raise
    with _lock:
        _cache["rows"] = rows
        _cache["ts"] = time.time()
    return rows


# ── sidecar enrichment (read-only, fail-soft) ──────────────────────────────


def read_feed_snapshot(path, max_age=_FEED_MAX_AGE):
    """coinapi-feed depth snapshot → {symbol: metrics}. Empty dict if the file is
    missing or staler than max_age (sidecar idle/down). Never raises."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if (time.time() - float(data.get("ts", 0))) > max_age:
        return {}
    syms = data.get("symbols")
    return syms if isinstance(syms, dict) else {}


def _cross_db_symbol(binance_sym):
    """BTCUSDT -> BTC/USDT (the cross-alerter's symbol format)."""
    return binance_sym[:-4] + "/USDT" if binance_sym.endswith("USDT") else binance_sym


def read_cross_counts(db_path, window_hours=_CROSS_WINDOW_HOURS):
    """{cross-db-symbol: 48h cross count} for tracked symbols, with a `fresh` flag
    per symbol from sync_state. Empty dict if the DB is absent/unreadable."""
    if not os.path.exists(db_path):
        return {}
    now_ms = time.time() * 1000.0
    cutoff_ms = now_ms - window_hours * 3600_000.0
    out = {}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=2.0)
        try:
            counts = conn.execute(
                "SELECT symbol, COUNT(*) FROM crosses WHERE cross_time >= ? GROUP BY symbol",
                (cutoff_ms,),
            ).fetchall()
            sync = dict(conn.execute("SELECT symbol, last_sync FROM sync_state").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    for sym, cnt in counts:
        out[sym] = {"count": int(cnt), "fresh": (now_ms - float(sync.get(sym, 0))) <= _CROSS_SYNC_STALE_MS}
    # symbols tracked but with 0 crosses in window still want a (fresh) 0
    for sym, last in sync.items():
        out.setdefault(sym, {"count": 0, "fresh": (now_ms - float(last)) <= _CROSS_SYNC_STALE_MS})
    return out


# ── volume-rate (surge) signal — background-refreshed, AI-consumable ─────────
# "Volume change rate" = how hot the latest CLOSED hour traded vs the coin's own
# recent baseline (last 1h quote-vol ÷ median of the prior 12h). >1 ⇒ trading
# hotter than usual right now.
# IMPORTANT — this is an ACTIVITY signal, NOT a direction forecast. High volume
# lands on down-hours as much as up-hours (BTW 2026-06-05 06:00 = 2.4× volume on a
# −11% hour). The AI should weigh it as conviction *alongside* a trend/direction
# signal (e.g. the EMA cross), never as a standalone buy trigger, and only after a
# backtest shows it adds edge. Exposed per-row as `volume_rate` for that future use.
_BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
_VOLRATE_REFRESH = 90.0     # background recompute cadence (s)
_VOLRATE_TOPN = 40          # compute for the top-N USDT perps by 24h volume
_CHG15_TOPN = 100           # 15m-change coverage (top-N USDT perps by volume)
_VOLRATE_KLINES = 14        # 1h candles: last closed + 12 baseline (+1 in-progress)
_volrate = {"data": {}, "ts": 0.0}
_volrate_lock = threading.Lock()
_refresher_started = False


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _compute_volume_rates(symbols):
    """{symbol: latest-closed-1h quote-vol ÷ median(prior 12h)}. Fail-soft per
    symbol — a flaky kline call just omits that coin (its rate stays None)."""
    out = {}
    for sym in symbols:
        try:
            r = httpx.get(_BINANCE_KLINES_URL,
                          params={"symbol": sym, "interval": "1h", "limit": _VOLRATE_KLINES},
                          timeout=5.0)
            r.raise_for_status()
            ks = r.json()
            if len(ks) < 4:
                continue
            qv = [float(c[7]) for c in ks]   # kline index 7 = quote-asset volume
            closed = qv[-2]                  # last fully-closed hour (qv[-1] is in-progress)
            base = _median(qv[:-2])          # prior hours
            if base > 0:
                out[sym] = round(closed / base, 2)
        except Exception:
            continue
    return out


def _compute_15m_change(symbols):
    """{symbol: % price change over the last ~15m} = (latest 15m close - prior 15m close)/prior * 100.
    Fail-soft per symbol (a flaky kline call just omits it)."""
    out = {}
    for sym in symbols:
        try:
            r = httpx.get(_BINANCE_KLINES_URL,
                          params={"symbol": sym, "interval": "15m", "limit": 2}, timeout=5.0)
            r.raise_for_status()
            ks = r.json()
            if len(ks) < 2:
                continue
            prev = float(ks[-2][4]); cur = float(ks[-1][4])   # kline index 4 = close
            if prev > 0:
                out[sym] = round((cur - prev) / prev * 100.0, 2)
        except Exception:
            continue
    return out


def _volrate_refresh_loop():
    while True:
        try:
            board = _fetch_board()
            by_vol = sorted(board, key=lambda r: r.get("volume", 0.0), reverse=True)
            syms = [r["symbol"] for r in by_vol[:_VOLRATE_TOPN]]
            # Also cover the biggest movers (often lower-volume, outside the top-N
            # by volume) so the vol-rate badge populates for exactly the coins the
            # movers strip surfaces — "is this % move backed by a volume surge?".
            pool = [r for r in board if r.get("volume", 0.0) >= 1_000_000.0]
            by_chg = sorted(pool, key=lambda r: r.get("change_pct", 0.0))
            mover_syms = [r["symbol"] for r in by_chg[:8]] + [r["symbol"] for r in by_chg[-8:]]
            seen, union = set(), []
            for x in syms + mover_syms:
                if x not in seen:
                    seen.add(x)
                    union.append(x)
            data = _compute_volume_rates(union)
            chg15 = _compute_15m_change([r["symbol"] for r in by_vol[:_CHG15_TOPN]])
            with _volrate_lock:
                _volrate["data"] = data
                _volrate["chg15"] = chg15
                _volrate["ts"] = time.time()
        except Exception:
            pass
        time.sleep(_VOLRATE_REFRESH)


def ensure_volrate_refresher():
    """Start the background vol-rate thread once (lazy, daemon). Cheap free
    Binance REST — runs always, so the rate is queryable even with no panel open."""
    global _refresher_started
    with _volrate_lock:
        if _refresher_started:
            return
        _refresher_started = True
    threading.Thread(target=_volrate_refresh_loop, name="volrate-refresher", daemon=True).start()


def get_volume_rates():
    with _volrate_lock:
        return dict(_volrate["data"])


def get_15m_changes():
    with _volrate_lock:
        return dict(_volrate.get("chg15", {}))


def build_movers(all_rows, volrate=None, n=8, min_volume=1_000_000.0):
    """Top-N gainers + losers by 24h % change across the WHOLE board (above a
    liquidity floor), each enriched with volume_rate. Always present so the biggest
    movers surface regardless of the panel's current sort."""
    volrate = volrate or {}
    pool = [r for r in all_rows if r.get("volume", 0.0) >= min_volume]

    def entry(r):
        return {
            "symbol": r["symbol"],
            "price": r.get("last_price", 0.0),
            "change_pct": r.get("change_pct", 0.0),
            "volume": r.get("volume", 0.0),
            "volume_rate": volrate.get(r["symbol"]),
        }

    gainers = [entry(r) for r in sorted(pool, key=lambda r: r.get("change_pct", 0.0), reverse=True)[:n]]
    losers = [entry(r) for r in sorted(pool, key=lambda r: r.get("change_pct", 0.0))[:n]]
    return {"gainers": gainers, "losers": losers}


def enrich_rows(rows, feed, crosses, volrate=None, chg15=None):
    """Attach depth metrics + live price + 48h cross count + volume_rate to each
    board row. Pure: takes the already-fetched feed/cross/volrate dicts. Adds keys:
      price (live mid if depth present, else Binance last), live (bool),
      bid, ask, spread_bps, bid_depth_usd, ask_depth_usd, imbalance,
      crosses_48h (int|None), crosses_fresh (bool), volume_rate (float|None)."""
    volrate = volrate or {}
    out = []
    for r in rows:
        sym = r["symbol"]
        d = feed.get(sym)
        cx = crosses.get(_cross_db_symbol(sym))
        row = dict(r)
        if d:
            row["live"] = True
            row["price"] = d.get("mid") or r.get("last_price", 0.0)
            for k in ("bid", "ask", "spread_bps", "bid_depth_usd", "ask_depth_usd", "imbalance"):
                row[k] = d.get(k)
        else:
            row["live"] = False
            row["price"] = r.get("last_price", 0.0)
            for k in ("bid", "ask", "spread_bps", "bid_depth_usd", "ask_depth_usd", "imbalance"):
                row[k] = None
        row["crosses_48h"] = cx["count"] if cx else None
        row["crosses_fresh"] = bool(cx and cx["fresh"])
        row["volume_rate"] = volrate.get(sym)
        row["change_15m"] = (chg15 or {}).get(sym)
        out.append(row)
    return out


def fetch_screener(sort="volume", ascending=False, min_volume=0.0, top=0,
                   feed_path=None, cross_db=None):
    """Live (≤4s cached) Binance USDT-M futures board, ranked + capped, enriched
    with coinapi-feed depth + cross-alerter 48h cross counts + volume-rate when
    available. Returns (rows, feed_live, movers): feed_live tells the UI whether
    depth is streaming; movers is the always-on top gainers/losers board."""
    ensure_volrate_refresher()
    board = _fetch_board()
    ranked = filter_sort_screener(board, sort=sort, ascending=ascending,
                                  min_volume=min_volume, top=top)
    feed = read_feed_snapshot(feed_path) if feed_path else {}
    crosses = read_cross_counts(cross_db) if cross_db else {}
    volrate = get_volume_rates()
    chg15 = get_15m_changes()
    rows = enrich_rows(ranked, feed, crosses, volrate, chg15)
    movers = build_movers(board, volrate, n=8)
    return rows, bool(feed), movers
