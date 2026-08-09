"""F&O instrument universe + near-month FUTURE resolution.

The NIFTY brain watches a fixed set of liquid index futures. For each, it must
drive its signal/SL math off the *future's* own price (not the spot index) so
the price domain matches what the client actually trades — a future trades at a
basis to its index (NIFTY ~135 pts, BANKNIFTY ~400 pts), and stopping a future
at an index level silently multiplies the client's risk.

Futures roll monthly, so the Kite instrument_token for "the near-month NIFTY
future" changes every expiry. We therefore resolve it dynamically from Kite's
daily instruments dump rather than hardcoding a token. The matching Tradejini
execution symbol (e.g. ``NIFTY26JUNFUT``) is built from the same resolved expiry
so data and execution always reference the same contract.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import kite_data
from .config import settings

log = logging.getLogger("fno-instruments")

_IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS3 = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
            "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


@dataclass(frozen=True)
class FnoInstrument:
    key: str            # short name used everywhere (NIFTY, BANKNIFTY, …)
    kite_name: str      # the `name` field in Kite's instruments dump
    kite_exchange: str  # NFO (NSE F&O) or BFO (BSE F&O)
    tj_underlying: str  # underlying as Tradejini encodes it in the FUT symbol
    index_token: str    # Kite spot-index token (reference / fallback display only)


# The fixed universe. SENSEX is on BSE (BFO segment); the rest are NSE (NFO).
UNIVERSE: list[FnoInstrument] = [
    FnoInstrument("NIFTY", "NIFTY", "NFO", "NIFTY", "256265"),
    FnoInstrument("BANKNIFTY", "BANKNIFTY", "NFO", "BANKNIFTY", "260105"),
    FnoInstrument("FINNIFTY", "FINNIFTY", "NFO", "FINNIFTY", "257801"),
    FnoInstrument("MIDCPNIFTY", "MIDCPNIFTY", "NFO", "MIDCPNIFTY", "288009"),
    FnoInstrument("SENSEX", "SENSEX", "BFO", "SENSEX", "265"),
]
_BY_KEY = {i.key: i for i in UNIVERSE}


@dataclass(frozen=True)
class NearMonth:
    kite_token: str   # Kite instrument_token for the near-month future (data)
    tj_symbol: str    # Tradejini execution symbol, e.g. NIFTY26JUNFUT
    expiry: str       # YYYY-MM-DD
    lot_size: int     # Kite-reported lot (reference; executor uses Tradejini's)


# Resolution is stable within a trading day; cache per instrument until ~06:00 IST.
_resolve_cache: dict[str, dict] = {}  # key -> {"val": NearMonth, "exp": ts}


def active_instruments() -> list[FnoInstrument]:
    """The configured subset to watch (env FNO_INSTRUMENTS, default = all 5)."""
    raw = (settings.fno_instruments or "").strip()
    if not raw:
        return list(UNIVERSE)
    out = []
    for k in (p.strip().upper() for p in raw.split(",")):
        if k in _BY_KEY:
            out.append(_BY_KEY[k])
        elif k:
            log.warning("unknown FNO instrument key %r in FNO_INSTRUMENTS — ignored", k)
    return out or list(UNIVERSE)


def _next_6am_ist_ts() -> float:
    nxt = datetime.now(_IST).replace(hour=6, minute=0, second=0, microsecond=0)
    if datetime.now(_IST) >= nxt:
        nxt += timedelta(days=1)
    return nxt.timestamp()


def _parse_date(s: str):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def resolve_near_month(inst: FnoInstrument) -> NearMonth:
    """Resolve the near-month future for `inst` → (kite data token, Tradejini
    execution symbol, expiry, lot). Raises on failure so the caller can SKIP the
    instrument (skipping = no trade = safe; never fall back to index data, which
    would re-introduce the basis bug)."""
    now = time.time()
    cached = _resolve_cache.get(inst.key)
    if cached and now < cached["exp"]:
        return cached["val"]

    rows = kite_data.instruments(inst.kite_exchange)
    today = datetime.now(_IST).date()
    best = None  # (expiry_date, row)
    for r in rows:
        if (r.get("instrument_type") or "").upper() != "FUT":
            continue
        if (r.get("name") or "").upper() != inst.kite_name.upper():
            continue
        exp = _parse_date(r.get("expiry"))
        if exp is None or exp < today:
            continue
        if best is None or exp < best[0]:
            best = (exp, r)
    if best is None:
        raise kite_data.KiteError(
            f"no near-month FUT for {inst.key} in Kite {inst.kite_exchange} dump")

    exp_date, row = best
    token = str(row.get("instrument_token") or "").strip()
    if not token:
        raise kite_data.KiteError(f"{inst.key} near-month row missing instrument_token")
    try:
        lot = int(float(row.get("lot_size") or 0))
    except (TypeError, ValueError):
        lot = 0
    yy = exp_date.year % 100
    mon = _MONTHS3[exp_date.month - 1]
    tj_symbol = f"{inst.tj_underlying}{yy:02d}{mon}FUT"

    val = NearMonth(kite_token=token, tj_symbol=tj_symbol,
                    expiry=exp_date.isoformat(), lot_size=lot)
    _resolve_cache[inst.key] = {"val": val, "exp": _next_6am_ist_ts()}
    log.info("resolved %s near-month: kite_token=%s tj=%s expiry=%s lot=%d",
             inst.key, token, tj_symbol, val.expiry, lot)
    return val
