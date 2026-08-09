"""Probe: which NIFTY weekly-option expiries are still resolvable in Kite's CURRENT
instrument dump (determines how far back we can pull option-chain minute data for a
2026 straddle backtest). Expired weeklies drop off the dump -> not fetchable."""
import os
import sys

HERE = "/root/aiprosperity/backend"
for _l in open(os.path.join(HERE, ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)

from app import kite_data

rows = kite_data.instruments("NFO")
nifty_opt = [r for r in rows
             if str(r.get("name")) == "NIFTY"
             and str(r.get("instrument_type")) in ("CE", "PE")]
exps = sorted({str(r.get("expiry")) for r in nifty_opt if r.get("expiry")})
print("total NFO rows:", len(rows))
print("NIFTY option rows:", len(nifty_opt))
print("distinct NIFTY option expiries available:", len(exps))
if exps:
    print("earliest resolvable expiry:", exps[0])
    print("latest   resolvable expiry:", exps[-1])
    print("all expiries:")
    for e in exps:
        n = sum(1 for r in nifty_opt if str(r.get("expiry")) == e)
        print("   {}  ({} strikes/legs)".format(e, n))
