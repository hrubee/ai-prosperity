"""Screener parse/filter/sort invariants (pure, no network).

Mirrors go-trader's shared_scripts/test_screen_binance.py and the Go
screener_test.go — the three copies must agree. The fetch test stubs httpx so it
never touches the network.

Run:  .venv/bin/python -m pytest tests/test_screener.py   (or run the file)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import screener  # noqa: E402

SAMPLE = [
    {"symbol": "BTCUSDT", "priceChangePercent": "-3.50", "quoteVolume": "9000000000"},
    {"symbol": "ETHUSDT", "priceChangePercent": "1.20", "quoteVolume": "5000000000"},
    {"symbol": "DOGEUSDT", "priceChangePercent": "12.75", "quoteVolume": "800000000"},
    {"symbol": "BTCUSDT_240927", "priceChangePercent": "-3.10", "quoteVolume": "100000000"},
    {"symbol": "ETHUSDC", "priceChangePercent": "1.10", "quoteVolume": "400000000"},
    {"symbol": "BADCOINUSDT", "priceChangePercent": "nan-ish", "quoteVolume": "x"},
]


def test_parse_filters_to_usdt_perps():
    rows = screener.parse_screener(SAMPLE)
    assert {r["symbol"] for r in rows} == {"BTCUSDT", "ETHUSDT", "DOGEUSDT"}
    btc = next(r for r in rows if r["symbol"] == "BTCUSDT")
    assert btc["change_pct"] == -3.50
    assert btc["volume"] == 9000000000.0


def test_sort_by_volume_desc_is_default():
    rows = screener.filter_sort_screener(screener.parse_screener(SAMPLE))
    assert [r["symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]


def test_sort_by_change_and_abs_change():
    rows = screener.parse_screener(SAMPLE)
    by_change = screener.filter_sort_screener(rows, sort="change")
    assert by_change[0]["symbol"] == "DOGEUSDT"   # +12.75
    assert by_change[-1]["symbol"] == "BTCUSDT"   # -3.50
    # Top losers: change ascending.
    losers = screener.filter_sort_screener(rows, sort="change", ascending=True)
    assert losers[0]["symbol"] == "BTCUSDT"
    by_abs = screener.filter_sort_screener(rows, sort="abs_change")
    assert [r["symbol"] for r in by_abs] == ["DOGEUSDT", "BTCUSDT", "ETHUSDT"]


def test_min_volume_and_top():
    rows = screener.parse_screener(SAMPLE)
    floored = screener.filter_sort_screener(rows, min_volume=1e9)
    assert {r["symbol"] for r in floored} == {"BTCUSDT", "ETHUSDT"}
    capped = screener.filter_sort_screener(rows, top=1)
    assert len(capped) == 1 and capped[0]["symbol"] == "BTCUSDT"


def test_fetch_screener_stubbed_and_cached():
    # Stub httpx.get so no network is touched; assert the cache collapses calls.
    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE

    def fake_get(url, timeout=None):
        calls["n"] += 1
        return FakeResp()

    orig_get = screener.httpx.get
    screener._cache["rows"] = None  # reset cache for a deterministic run
    screener._cache["ts"] = 0.0
    screener.httpx.get = fake_get
    try:
        rows = screener.fetch_screener(sort="abs_change", top=2)
        assert [r["symbol"] for r in rows] == ["DOGEUSDT", "BTCUSDT"]
        # Second call within the TTL must reuse the cache (no extra fetch).
        screener.fetch_screener()
        assert calls["n"] == 1
    finally:
        screener.httpx.get = orig_get
        screener._cache["rows"] = None
        screener._cache["ts"] = 0.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("ALL SCREENER TESTS PASSED")
