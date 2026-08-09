"""One-off live verification of the F&O brain rebuild (run on the VPS).

Checks the advisor's verify-early gates before go-live:
  1. all configured futures resolve on Kite (token + tj symbol + expiry),
  2. Kite serves candles for each (incl. SENSEX on BFO),
  3. the future-vs-index basis is real (proves we're on future data),
  4. each tj symbol resolves on Tradejini (so clients won't error),
  5. the Vision path actually renders a chart + returns a model verdict.
Loads .env itself (manual parser — no bash sourcing) BEFORE importing app.*,
so frozen Settings picks up the live keys.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(_HERE, ".env"))

from app import fno_instruments, kite_data, tradejini, vision   # noqa: E402
from app.crypto import decrypt_secret                            # noqa: E402
from app.db import session_scope                                 # noqa: E402
from app.models import TradejiniConnection                       # noqa: E402
from app.nifty_brain import (build_signal, TIMEFRAME_MIN, EMA_FAST,  # noqa: E402
                             EMA_SLOW, CHART_BARS, _fetch_count)

print("vision_available:", vision.vision_available())
print()

resolved = {}
print("=== 1-3. Kite resolution + candles + basis ===")
for inst in fno_instruments.active_instruments():
    try:
        nm = fno_instruments.resolve_near_month(inst)
    except Exception as e:
        print(f"{inst.key:11s} RESOLVE FAIL: {e}")
        continue
    try:
        full = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())
        last = full[-1][4]
        try:
            idx = kite_data.bars_for_token(inst.index_token, TIMEFRAME_MIN, 5)[-1][4]
            basis = f"{last - idx:+.1f}"
        except Exception:
            idx, basis = 0.0, "n/a"
        r = build_signal(full)
        print(f"{inst.key:11s} kite={nm.kite_token:>10s} tj={nm.tj_symbol:16s} exp={nm.expiry} "
              f"lot={nm.lot_size:>3d} bars={len(full)} fut={last:.1f} idx={idx:.1f} basis={basis} "
              f"| signal={r['signal']} ref={r['ref_price']:.1f} sl={r['sl_price']:.1f} atr={r['atr']:.1f}")
        resolved[inst.key] = nm
    except Exception as e:
        print(f"{inst.key:11s} CANDLE FAIL (tj={nm.tj_symbol}): {e}")

print("\n=== 4. Tradejini resolution (client-facing — must not error) ===")
tok = None
with session_scope() as db:
    conn = (db.query(TradejiniConnection)
            .filter(TradejiniConnection.status == "connected").first())
    if conn:
        tok = decrypt_secret(conn.access_token_encrypted)
if not tok:
    print("no connected Tradejini token in DB — re-run this check after a client connects")
else:
    cli = tradejini.TradejiniClient(tok)
    for key, nm in resolved.items():
        try:
            meta = cli.resolve(nm.tj_symbol)
            print(f"{key:11s} {nm.tj_symbol:16s} -> sym_id={meta['sym_id']} lot={meta['lot_size']}")
        except Exception as e:
            print(f"{key:11s} {nm.tj_symbol:16s} -> RESOLVE FAIL: {e}")

print("\n=== 5. Vision smoke (one real chart + model call on NIFTY) ===")
if vision.vision_available() and "NIFTY" in resolved:
    nm = resolved["NIFTY"]
    full = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())
    out = "/tmp/verify_nifty.png"
    dots = vision.render_crossover_chart(full, EMA_FAST, EMA_SLOW, out,
                                         f"NIFTY {nm.tj_symbol}", CHART_BARS, TIMEFRAME_MIN)
    print(f"chart: {os.path.getsize(out)} bytes, red_dots={dots}")
    congested, reason, vsl = vision.vision_veto(nm.tj_symbol, out, "buy",
                                                full[-1][4], CHART_BARS, TIMEFRAME_MIN)
    print(f"VISION verdict: congested={congested} visual_sl={vsl} reason={reason[:240]}")
else:
    print("vision unavailable or NIFTY unresolved — skipped")
