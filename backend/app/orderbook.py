"""Order-book + trend data for the admin order-book panel.

On-demand snapshot from Binance USDT-M futures REST (/depth + /klines) — free,
works for every coin incl. BTC/ETH, and it's the exact book the bot trades. Mirrors
shared_scripts/book_view.py: live depth/spread/imbalance over a comparable ±0.15%
band, standout S/R "walls" away from the touch, plus the bot's trend context
(EMA9/150 + 48h congestion). Returns a downsampled cumulative-depth curve for an
SVG depth chart. This is read-only CONTEXT for the operator — NOT a bot signal
(the order book has ~0 predictive edge at 5m).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

import httpx

FAPI = "https://fapi.binance.com/fapi/v1"
DEPTH_BAND = 0.0015     # depth/imbalance over ±0.15% of mid (comparable across coins)
WALL_INNER = 0.0005     # ignore levels within 0.05% of mid (that's the spread)
WALL_OUTER = 0.015      # scan walls out to ±1.5%
WALL_MULT = 3.0         # >= this × median in-band notional = a standout wall
CHART_BAND = 0.02       # depth chart spans ±2% of mid
CHART_PTS = 60          # points per side after downsampling

_CACHE_TTL = 2.0
_lock = threading.Lock()
_cache: dict = {}


def _norm(sym: str) -> str:
    s = (sym or "").upper().replace("/", "").replace("-", "")
    return s if s.endswith("USDT") else s + "USDT"


def _ema(vals, n):
    a = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = a * v + (1 - a) * e
    return e


def _ema_seq(vals, n):
    a = 2.0 / (n + 1)
    e = vals[0]
    out = []
    for v in vals:
        e = a * v + (1 - a) * e
        out.append(e)
    return out


def _trend(sym):
    r = httpx.get(FAPI + "/klines", params={"symbol": sym, "interval": "5m", "limit": 200}, timeout=8.0)
    r.raise_for_status()
    closes = [float(c[4]) for c in r.json()]
    if len(closes) < 151:
        return {"err": "insufficient history"}
    e9, e150 = _ema(closes, 9), _ema(closes, 150)
    s9, s150 = _ema_seq(closes, 9), _ema_seq(closes, 150)
    recent = any((s9[i] - s150[i]) * (s9[i - 1] - s150[i - 1]) < 0 for i in range(-3, 0))
    price = closes[-1]
    return {"ema9": e9, "ema150": e150, "bull": e9 > e150,
            "sep_pct": abs(e9 - e150) / price * 100, "recent_cross": recent}


def _cross_count(sym, db, hours=48):
    if not db or not os.path.exists(db):
        return None
    dbsym = sym[:-4] + "/USDT" if sym.endswith("USDT") else sym
    try:
        cutoff = (time.time() - hours * 3600) * 1000
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2.0)
        n = con.execute("SELECT COUNT(*) FROM crosses WHERE symbol=? AND cross_time>=?",
                        (dbsym, cutoff)).fetchone()[0]
        con.close()
        return int(n)
    except sqlite3.Error:
        return None


def _curve(levels, mid, side):
    lo, hi = mid * (1 - CHART_BAND), mid * (1 + CHART_BAND)
    seq = sorted([(p, q) for p, q in levels if lo <= p <= hi],
                 key=lambda x: (-x[0] if side == "bid" else x[0]))  # from mid outward
    pts = []
    cum = 0.0
    for p, q in seq:
        cum += p * q
        pts.append((p, cum))
    if len(pts) > CHART_PTS:
        step = len(pts) / CHART_PTS
        pts = [pts[int(i * step)] for i in range(CHART_PTS)] + [pts[-1]]
    return [{"price": p, "cum": c} for p, c in pts]


def _compute(symbol, cross_db):
    sym = _norm(symbol)
    d = httpx.get(FAPI + "/depth", params={"symbol": sym, "limit": 1000}, timeout=10.0)
    d.raise_for_status()
    ob = d.json()
    bids = [(float(p), float(q)) for p, q in ob.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in ob.get("asks", [])]
    if not bids or not asks:
        raise ValueError("empty book for %s" % sym)
    bid, ask = bids[0][0], asks[0][0]
    mid = (bid + ask) / 2.0
    bd = sum(p * q for p, q in bids if p >= mid * (1 - DEPTH_BAND))
    ad = sum(p * q for p, q in asks if p <= mid * (1 + DEPTH_BAND))
    imb = bd / (bd + ad) if (bd + ad) else 0.5
    scan = []
    for p, q in bids:
        if WALL_INNER <= (mid - p) / mid <= WALL_OUTER:
            scan.append((p, q, p * q, "support"))
    for p, q in asks:
        if WALL_INNER <= (p - mid) / mid <= WALL_OUTER:
            scan.append((p, q, p * q, "resistance"))
    med = sorted(x[2] for x in scan)[len(scan) // 2] if scan else 0
    walls = sorted([x for x in scan if med > 0 and x[2] >= WALL_MULT * med], key=lambda x: -x[2])[:6]
    walls = [{"price": p, "notional": n, "dist_pct": (p / mid - 1) * 100, "role": role,
              "mult": (n / med if med else 0)} for (p, q, n, role) in walls]
    try:
        trend = _trend(sym)
    except Exception as e:
        trend = {"err": str(e)}
    return {
        "symbol": sym, "mid": mid, "bid": bid, "ask": ask,
        "spread_bps": (ask - bid) / mid * 1e4,
        "bid_depth": bd, "ask_depth": ad, "imbalance": imb,
        "walls": walls, "trend": trend, "congestion_48h": _cross_count(sym, cross_db),
        "chart": {"bids": _curve(bids, mid, "bid"), "asks": _curve(asks, mid, "ask")},
        "ts": time.time(),
    }


def fetch_orderbook(symbol, cross_db=None):
    """Cached (~2s) order-book + trend snapshot for one symbol."""
    sym = _norm(symbol)
    now = time.time()
    with _lock:
        hit = _cache.get(sym)
        if hit and (now - hit[0]) < _CACHE_TTL:
            return hit[1]
    data = _compute(sym, cross_db)
    with _lock:
        _cache[sym] = (time.time(), data)
    return data
