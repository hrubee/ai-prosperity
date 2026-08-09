"""AAA daily candlestick-setup scanner for Indian stocks.

Runs once per day BEFORE market open (a systemd timer fires it ~08:00 IST, well
ahead of the 09:15 open). It:

  1. resolves the NSE equity universe from Kite's instrument dump,
  2. pulls ~60 daily (1D) candles per stock from Kite (throttled to respect the
     ~3 req/s historical limit so the data account is never banned),
  3. drops any still-forming candle for *today* so detection runs only on
     COMPLETED bars (yesterday's close — the setups to watch when the bell rings),
  4. runs every detector in ``candle_patterns`` against the most recent candles,
  5. writes the matches to a sidecar JSON atomically.

The FastAPI ``/aaa/setups`` endpoint only READS that sidecar, so a live request
never waits on Kite. Fail-soft per stock: a flaky/halted symbol is skipped and
counted in ``errors``, never aborting the sweep.

CLI:
  python -m app.aaa_scanner                 # full scan → settings.aaa_setups_path
  python -m app.aaa_scanner --limit 30      # quick smoke on the first 30 symbols
  python -m app.aaa_scanner --no-write      # scan + print JSON, write nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from . import kite_data
from .candle_patterns import detect_all, to_candles
from .config import settings

log = logging.getLogger("aaa-scanner")
_IST = timezone(timedelta(hours=5, minutes=30))
MIN_CANDLES = 14  # enough history for the longest pattern + its trend lookback


def _ist_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, _IST).strftime("%Y-%m-%d")


def _is_today_ist(ts_ms: int) -> bool:
    return datetime.fromtimestamp(ts_ms / 1000, _IST).date() == datetime.now(_IST).date()


def scan(limit: int = 0, throttle: float | None = None, history: int | None = None) -> dict:
    """Sweep the NSE equity universe for candlestick setups on the 1D timeframe.
    Returns the sidecar payload (does not write it)."""
    throttle = settings.aaa_throttle_sec if throttle is None else throttle
    history = settings.aaa_history_days if history is None else history

    if settings.aaa_universe == "nse_all":
        universe = kite_data.nse_equities()
        universe_label = "NSE equities · 1D"
    else:
        universe = kite_data.nifty200_equities()
        universe_label = "NIFTY 200 · 1D"
    cap = limit if (limit and limit > 0) else settings.aaa_max_symbols
    if cap and cap > 0:
        universe = universe[:cap]
    total = len(universe)
    log.info("AAA scan: %s — %d stocks, history=%d days, throttle=%.2fs",
             universe_label, total, history, throttle)

    setups: list[dict] = []
    scanned = 0
    errors = 0
    for i, inst in enumerate(universe):
        try:
            raw = kite_data.daily_bars_for_token(inst["instrument_token"], history)
            candles = to_candles(raw)
            # Only detect on COMPLETED candles — drop a partial bar for today.
            if candles and _is_today_ist(candles[-1].t):
                candles = candles[:-1]
            if len(candles) >= MIN_CANDLES:
                sig = candles[-1]
                sig_date = _ist_date(sig.t)
                for m in detect_all(candles):
                    row = m.to_dict()
                    row["symbol"] = inst["tradingsymbol"]
                    row["name"] = inst["name"]
                    row["signal_date"] = sig_date
                    setups.append(row)
            scanned += 1
        except Exception as e:  # fail-soft per stock — never abort the sweep
            errors += 1
            log.debug("scan %s failed: %s", inst.get("tradingsymbol"), e)
        if throttle:
            time.sleep(throttle)
        if (i + 1) % 200 == 0:
            log.info("AAA scan progress %d/%d (setups=%d errors=%d)", i + 1, total, len(setups), errors)

    # Group bullish before bearish; surface volume-confirmed setups first; then A→Z.
    setups.sort(key=lambda s: (s["direction"], not bool(s.get("volume_confirm")), s["symbol"]))
    now_utc = datetime.now(timezone.utc)
    payload = {
        "generated_at": now_utc.isoformat(),
        "generated_at_ist": datetime.now(_IST).strftime("%Y-%m-%d %H:%M IST"),
        "universe": universe_label,
        "scanned": scanned,
        "errors": errors,
        "count": len(setups),
        "setups": setups,
    }
    log.info("AAA scan complete: %d setups (scanned=%d errors=%d)", len(setups), scanned, errors)
    return payload


def write_payload(payload: dict, path: str) -> None:
    """Atomically write the sidecar (tmp + os.replace) so the API never reads a
    half-written file."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="AAA daily candlestick-setup scanner (NSE, 1D).")
    ap.add_argument("--limit", type=int, default=0, help="scan only the first N symbols (smoke test)")
    ap.add_argument("--out", default=settings.aaa_setups_path, help="sidecar JSON path")
    ap.add_argument("--throttle", type=float, default=None, help="seconds between Kite calls")
    ap.add_argument("--no-write", action="store_true", help="print JSON instead of writing the sidecar")
    args = ap.parse_args()

    if not kite_data.configured():
        log.error("Kite is not configured (set KITE_API_KEY/SECRET/USER_ID/PASSWORD/TOTP_SECRET).")
        sys.exit(1)

    t0 = time.time()
    payload = scan(limit=args.limit, throttle=args.throttle)
    if args.no_write:
        print(json.dumps(payload, indent=2))
    else:
        write_payload(payload, args.out)
        log.info("wrote %d setups → %s in %.0fs", payload["count"], args.out, time.time() - t0)


if __name__ == "__main__":
    main()
