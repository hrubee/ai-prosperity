"""Crypto Vision bridge — surfaces the go-trader (crypto) brain's AI-Vision
decisions on the same admin panel as the F&O brain, WITHOUT touching the crypto
stack. It only READS the crypto brain's vision log + chart files, so it can never
affect crypto trading.

The crypto brain (separate process, /root/go-trader) appends one line per fresh
cross to /var/log/go-trader-ai.log:

  [2026-05-30 01:12:37 UTC] [VISION ANALYSIS] ETH/USDT | Congestion: True | Reason: … | Stop Loss: 0.0

and renders the chart it judged to /tmp/chart_<SYM>.png. This bridge tails the
log, parses those lines, and writes a BrainEvent (source=crypto-brain) for each.

Idempotent by construction: each event's id is a deterministic hash of
(source, log-timestamp, symbol), so re-reading the tail (or a logrotate, or an
offset-file loss) can't create duplicates — no byte-offset state is kept. The
chart is attached only when its file mtime matches the log timestamp; otherwise
it's stored null (an honest "no chart" beats a stale one — that next cross would
have overwritten).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from . import brain_log
from .db import session_scope
from .models import Signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("crypto-vision-bridge")

LOG_PATH = os.environ.get("CRYPTO_VISION_LOG", "/var/log/go-trader-ai.log")
CHART_DIR = os.environ.get("CRYPTO_CHART_DIR", "/tmp")
POLL_SEC = int(os.environ.get("CRYPTO_VISION_POLL_SEC", "45"))
BACKFILL_MAX_AGE_H = int(os.environ.get("CRYPTO_VISION_MAX_AGE_H", "36"))
_MAX_READ = 4 * 1024 * 1024          # tail at most 4 MB (bounds work; survives rotation)
_CHART_MTIME_TOL = 600               # chart must be within 10 min of the log ts, else null
_SIDE_WINDOW = timedelta(seconds=180)  # correlate a published cross to its Delta Signal

_VISION_RE = re.compile(
    r"\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s*\[VISION ANALYSIS\]\s*"
    r"(?P<sym>\S+)\s*\|\s*Congestion:\s*(?P<cong>True|False)\s*\|\s*"
    r"Reason:\s*(?P<reason>.*?)\s*\|\s*Stop Loss:\s*(?P<sl>[\d.]+)")
_DOTS_RE = re.compile(r"(\d+)\s*red dots", re.IGNORECASE)


def _event_id(ts_iso: str, symbol: str) -> str:
    return hashlib.sha1(f"crypto-brain|{ts_iso}|{symbol}".encode()).hexdigest()[:32]


def _read_tail() -> list[str]:
    try:
        size = os.path.getsize(LOG_PATH)
    except OSError:
        return []
    start = max(0, size - _MAX_READ)
    with open(LOG_PATH, "rb") as f:
        f.seek(start)
        data = f.read()
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # drop the partial first line from mid-file seek
    return lines


def _chart_for(symbol: str, ts_dt: datetime) -> str | None:
    """The chart the model saw — only if its mtime matches the log line (else the
    next cross has overwritten it; null is honest)."""
    path = os.path.join(CHART_DIR, f"chart_{symbol.replace('/', '_')}.png")
    try:
        if abs(os.path.getmtime(path) - ts_dt.timestamp()) <= _CHART_MTIME_TOL:
            return path
    except OSError:
        pass
    return None


def _lookup_side_ref(db, symbol: str, ts_dt: datetime):
    sig = (db.query(Signal)
           .filter(Signal.venue == "delta", Signal.symbol == symbol,
                   Signal.created_at >= ts_dt - _SIDE_WINDOW,
                   Signal.created_at <= ts_dt + _SIDE_WINDOW)
           .order_by(Signal.created_at.desc()).first())
    return (sig.side, sig.ref_price) if sig else (None, None)


def process_once() -> int:
    """Parse new vision lines and record them. Returns how many were inserted."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=BACKFILL_MAX_AGE_H)
    parsed: dict[str, dict] = {}
    for line in _read_tail():
        m = _VISION_RE.search(line)
        if not m:
            continue
        ts_dt = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if ts_dt < cutoff:
            continue
        sym = m.group("sym")
        eid = _event_id(m.group("ts"), sym)
        dots = _DOTS_RE.search(m.group("reason"))
        try:
            vsl = float(m.group("sl"))
        except ValueError:
            vsl = 0.0
        parsed[eid] = {  # dedup within the batch on id
            "id": eid, "ts": ts_dt, "symbol": sym, "instrument": sym.split("/")[0],
            "congested": m.group("cong") == "True", "reason": m.group("reason"),
            "vsl": vsl, "red_dots": int(dots.group(1)) if dots else None,
        }
    if not parsed:
        return 0

    new_ids = set(parsed) - brain_log.existing_ids(list(parsed))
    if not new_ids:
        return 0

    inserted = 0
    with session_scope() as db:
        rows = [parsed[i] for i in new_ids]
        side_ref = {}
        for r in rows:
            side_ref[r["id"]] = (None, None) if r["congested"] else _lookup_side_ref(db, r["symbol"], r["ts"])
    for r in rows:
        side, ref = side_ref[r["id"]]
        brain_log.record(
            event_id=r["id"], source="crypto-brain", ts=r["ts"],
            instrument=r["instrument"], tj_symbol=r["symbol"], side=side,
            ref_price=ref, sl_price=None, atr=None, red_dots=r["red_dots"],
            vision_evaluated=True, congested=r["congested"], vision_reason=r["reason"],
            visual_sl=r["vsl"], action=("vetoed" if r["congested"] else "published"),
            signal_id=None, chart_path=_chart_for(r["symbol"], r["ts"]))
        inserted += 1
    return inserted


def run() -> None:
    log.info("crypto vision bridge started: tailing %s every %ds (source=crypto-brain)",
             LOG_PATH, POLL_SEC)
    while True:
        try:
            n = process_once()
            if n:
                log.info("recorded %d new crypto vision event(s)", n)
        except Exception as e:
            log.exception("bridge tick error: %s", e)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    run()
