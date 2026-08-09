"""Live broker read: list OPEN Tradejini positions across every connected account.
Read-only — places no orders. Used to confirm nothing is left open before retiring
the old VPS / after disabling the brain."""
import os
import sys

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


def main():
    any_open = False
    with session_scope() as db:
        conns = db.execute(select(TradejiniConnection)).scalars().all()
        print("Tradejini connections in DB: {}".format(len(conns)))
        print("=" * 64)
        for c in conns:
            u = db.get(User, c.user_id)
            email = u.email if u else "user_id={}".format(c.user_id)
            try:
                cl = tradejini.TradejiniClient(
                    tradejini_auth.ensure_client_token(db, c), api_key=c.api_key)
                pos = list(cl.open_positions())
                openp = []
                for p in pos:
                    qty = p.get("size", p.get("netQty", p.get("net_quantity", 0)))
                    try:
                        q = float(qty)
                    except Exception:
                        q = 0.0
                    if abs(q) > 0:
                        openp.append((p, q))
                day = cl.day_realized_pnl_inr()
                cash = cl.buyable_cash_inr()
                print("\n{}".format(email))
                print("   day realized Rs.{:+,.0f} | buyable cash Rs.{:,.0f}".format(day, cash))
                if openp:
                    any_open = True
                    print("   !! {} OPEN POSITION(S):".format(len(openp)))
                    for p, q in openp:
                        print("      {} side={} qty={} avg={}".format(
                            p.get("symbol", "?"), p.get("side", "?"), q, p.get("avgPrice", p.get("avg", "?"))))
                else:
                    print("   FLAT — 0 open positions")
            except Exception as e:
                print("\n{}\n   ERROR reading broker: {}".format(email, str(e)[:150]))
    print("\n" + "=" * 64)
    print("RESULT: {}".format("OPEN POSITIONS FOUND (see above)" if any_open else "ALL ACCOUNTS FLAT — nothing open on Tradejini"))


if __name__ == "__main__":
    main()
