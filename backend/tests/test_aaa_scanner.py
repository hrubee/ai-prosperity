"""AAA scanner orchestration invariants (Kite stubbed — no network).

Verifies the sweep wiring around the (separately-tested) detectors: pattern rows
are assembled with symbol/name/signal_date, a still-forming TODAY candle is
dropped so detection runs only on completed bars, and a per-stock fetch error is
counted (errors) without aborting the sweep.

Run:  .venv/bin/python -m pytest tests/test_aaa_scanner.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import aaa_scanner  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))


def _rows(prefix_closes, pattern_rows, end_dt):
    """Daily [ts,o,h,l,c,v] rows whose LAST candle lands on end_dt (IST)."""
    seq = [(c + 0.6, c + 0.9, c - 0.6, c) for c in prefix_closes]
    seq += [tuple(r[:4]) for r in pattern_rows]
    n = len(seq)
    out = []
    for i, (o, h, l, c) in enumerate(seq):
        dt = end_dt - timedelta(days=(n - 1 - i))
        out.append([int(dt.timestamp() * 1000), o, h, l, c, 1000])
    return out


def _down(n=13, top=120.0, bottom=100.0):
    step = (top - bottom) / (n - 1)
    return [top - step * i for i in range(n)]


def _flat(n=13, px=100.0):
    return [px + (0.1 if i % 2 else -0.1) for i in range(n)]


_HAMMER = (100.0, 106.0, 98.8, 99.0)  # body in bottom 40%, long upper wick


def _stub(monkeypatch, universe, data_by_token):
    # The scanner's default universe is "nifty200"; stub that resolver.
    monkeypatch.setattr(aaa_scanner.kite_data, "nifty200_equities", lambda: universe)
    monkeypatch.setattr(aaa_scanner.kite_data, "nse_equities", lambda: universe)

    def fake_daily(token, count):
        rows = data_by_token.get(str(token))
        if isinstance(rows, Exception):
            raise rows
        return rows

    monkeypatch.setattr(aaa_scanner.kite_data, "daily_bars_for_token", fake_daily)


def test_scan_finds_hammer_and_skips_flat(monkeypatch):
    yest = datetime.now(_IST) - timedelta(days=2)  # a completed past session
    universe = [
        {"tradingsymbol": "HAMMERCO", "instrument_token": "1", "name": "Hammer Co"},
        {"tradingsymbol": "FLATCO", "instrument_token": "2", "name": "Flat Co"},
    ]
    data = {
        "1": _rows(_down(), [_HAMMER], yest),
        "2": _rows(_flat(), [(100.0, 100.5, 99.5, 100.1)], yest),
    }
    _stub(monkeypatch, universe, data)

    payload = aaa_scanner.scan(throttle=0)
    assert payload["scanned"] == 2 and payload["errors"] == 0
    syms = {s["symbol"]: s for s in payload["setups"]}
    assert "HAMMERCO" in syms and "FLATCO" not in syms
    h = syms["HAMMERCO"]
    assert h["code"] == "hammer" and h["pattern"] == "Hammer"
    assert h["action"] == "buy" and h["name"] == "Hammer Co"
    assert h["signal_date"]  # YYYY-MM-DD attached
    assert payload["count"] == len(payload["setups"])


def test_scan_drops_today_forming_candle(monkeypatch):
    # Hammer completes yesterday; a fresh non-pattern candle is timestamped TODAY.
    # The scanner must drop today's partial bar and still detect the hammer.
    today = datetime.now(_IST)
    universe = [{"tradingsymbol": "DROPCO", "instrument_token": "9", "name": "Drop Co"}]
    rows = _rows(_down(), [_HAMMER, (101.0, 101.8, 100.5, 101.2)], today)
    _stub(monkeypatch, universe, {"9": rows})

    payload = aaa_scanner.scan(throttle=0)
    codes = {s["code"] for s in payload["setups"]}
    assert "hammer" in codes


def test_scan_failsoft_on_fetch_error(monkeypatch):
    universe = [
        {"tradingsymbol": "OKCO", "instrument_token": "1", "name": "OK Co"},
        {"tradingsymbol": "BADCO", "instrument_token": "2", "name": "Bad Co"},
    ]
    yest = datetime.now(_IST) - timedelta(days=2)
    data = {"1": _rows(_down(), [_HAMMER], yest), "2": RuntimeError("kite 500")}
    _stub(monkeypatch, universe, data)

    payload = aaa_scanner.scan(throttle=0)
    assert payload["errors"] == 1 and payload["scanned"] == 1
    assert {s["symbol"] for s in payload["setups"]} == {"OKCO"}


def test_payload_shape(monkeypatch):
    _stub(monkeypatch, [], {})
    payload = aaa_scanner.scan(throttle=0)
    for key in ("generated_at", "generated_at_ist", "universe", "scanned", "errors", "count", "setups"):
        assert key in payload
    assert payload["setups"] == [] and payload["count"] == 0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
