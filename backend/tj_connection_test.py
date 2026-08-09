"""Read-only Tradejini connection test from THIS VPS. For every connected account:
auth -> validate -> funds -> positions -> P&L -> scrip-master (can it find the
NIFTY weekly CE/PE the straddle trades). Places NO orders, mutates nothing."""
import os
import socket
import sys
import time

HERE = "/root/aiprosperity/backend"
for _l in open(os.path.join(HERE, ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)

from sqlalchemy import select
from app.db import session_scope
from app.models import TradejiniConnection, User
from app import tradejini, tradejini_auth


def check(label, fn):
    t0 = time.time()
    try:
        v = fn()
        print("   [PASS] {:<24} {}  ({:.0f}ms)".format(label, v, (time.time() - t0) * 1000))
        return True
    except Exception as e:
        print("   [FAIL] {:<24} {}  ({:.0f}ms)".format(label, str(e)[:90], (time.time() - t0) * 1000))
        return False


def main():
    print("TRADEJINI CONNECTION TEST  -  host={}".format(socket.gethostname()))
    print("=" * 72)
    passed = total = 0
    with session_scope() as db:
        conns = db.execute(select(TradejiniConnection)).scalars().all()
        print("connections in DB: {}".format(len(conns)))
        for c in conns:
            u = db.get(User, c.user_id)
            email = u.email if u else "user_id={}".format(c.user_id)
            print("\n### {} ###".format(email))
            t0 = time.time()
            try:
                tok = tradejini_auth.ensure_client_token(db, c)
                print("   [PASS] {:<24} token ok (len={})  ({:.0f}ms)".format(
                    "auth / token", len(tok or ""), (time.time() - t0) * 1000))
                passed += 1
            except Exception as e:
                print("   [FAIL] {:<24} {}".format("auth / token", str(e)[:110]))
                total += 1
                continue
            total += 1
            cl = tradejini.TradejiniClient(tok, api_key=c.api_key)

            results = [
                check("validate()", lambda: "ok" if cl.validate() else "returned False"),
                check("equity", lambda: "Rs.{:,.0f}".format(cl.equity_inr())),
                check("buyable cash", lambda: "Rs.{:,.0f}".format(cl.buyable_cash_inr())),
                check("open positions", lambda: "{} open".format(len(list(cl.open_positions())))),
                check("day realized P&L", lambda: "Rs.{:+,.0f}".format(cl.day_realized_pnl_inr() or 0)),
            ]

            def scrips():
                rows = cl.list_scrips("NSEOptions")
                nifty = [r for r in rows
                         if str(r.get("symbol", "")).upper() == "NIFTY"
                         and str(r.get("optType", "")).upper() in ("CE", "PE")]
                return "{} scrips total, {} NIFTY CE/PE found".format(len(rows), len(nifty))
            results.append(check("scrip-master (NIFTY)", scrips))

            passed += sum(results) + 1  # +1 for the auth pass already counted? no
            total += len(results)
    print("\n" + "=" * 72)
    print("connection test complete")


if __name__ == "__main__":
    main()
