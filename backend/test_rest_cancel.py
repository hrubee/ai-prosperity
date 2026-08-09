"""CLEAN place -> RESTS -> cancel demo for ONE Tradejini client.

Proves the full order lifecycle: a buy LIMIT just inside Tradejini's
price-protection band (so it is ACCEPTED and rests in the book) but BELOW the
live best bid (so a market sell hits higher bids first, not ours), then cancels
it and confirms it cleared. Tries progressively-closer-to-market prices, FAR
first — the first accepted price is the farthest-below-market the band allows,
i.e. maximally behind the bid queue. Resting window ~1s. Near-zero (not strictly
zero) fill risk; explicitly chosen for this demo.

Run on the VPS with the env sourced:
    set -a; . ./.env; set +a
    .venv/bin/python3 test_rest_cancel.py samsar2001@gmail.com
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db import session_scope
from app.models import TradejiniConnection, User
from app import tradejini, tradejini_auth
from app.straddle_runner import discover_legs, ltp, _kget

TICK = 0.05
TARGET_PREMIUM = 50.0
DISCOUNTS = [0.20, 0.15, 0.12, 0.09, 0.06, 0.04]   # far->close; first accepted rests


def _round_tick(p: float) -> float:
    return round(round(p / TICK) * TICK, 2)


def _ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _resting(status: str | None) -> bool:
    s = status or ""
    return any(k in s for k in ("open", "pending", "trigger", "received", "new", "placed", "modif"))


def _cleared(status: str | None) -> bool:
    return status is None or "cancel" in (status or "") or "reject" in (status or "")


def _best_bid(token) -> float | None:
    try:
        j = json.loads(_kget(f"/quote?i={token}"))
        d = (j.get("data") or {}).get(str(token)) or {}
        buys = (d.get("depth") or {}).get("buy") or []
        return float(buys[0]["price"]) if buys and buys[0].get("price") else None
    except Exception:
        return None


def main(email: str) -> int:
    ist = _ist_now()
    mo = ist.weekday() < 5 and "09:15" <= ist.strftime("%H:%M") <= "15:30"
    print(f"IST {ist:%Y-%m-%d %H:%M:%S} | market_open={mo}")
    if not mo:
        print("ABORT: market closed — a resting-order demo needs live market hours.")
        return 1
    print("=" * 64)

    with session_scope() as db:
        u = db.execute(select(User).where(func.lower(User.email) == email.lower())).scalars().first()
        if not u:
            print("ABORT: no user"); return 1
        c = db.execute(select(TradejiniConnection).where(
            TradejiniConnection.user_id == u.id)).scalars().first()
        if not c or not tradejini_auth.has_auto_creds(c):
            print("ABORT: no connection/creds"); return 1
        tok = tradejini_auth.ensure_client_token(db, c)
        api, name = c.api_key, (getattr(u, "name", "") or email)
    cl = tradejini.TradejiniClient(tok, api_key=api)
    print(f"client: {name} <{email}> | margin Rs.{cl.equity_inr():,.0f}")

    chain = discover_legs()
    expiry, lot, ce = chain["expiry"], int(chain["lot"]), chain["CE"]
    q = ltp(list(ce.values()))
    prem = {s: q.get(str(t)) for s, t in ce.items() if q.get(str(t)) is not None}
    if not prem:
        print("ABORT: no premiums"); return 1
    strike = min(prem, key=lambda s: abs(prem[s] - TARGET_PREMIUM))
    premium, token = prem[strike], ce[strike]
    meta = cl.resolve_weekly_option("NIFTY", expiry, strike, "CE")
    sym_id, sym, qty = meta["sym_id"], meta.get("symbol"), lot
    bid = _best_bid(token)
    print(f"contract: {sym} ({sym_id}) | LTP Rs.{premium:.2f} | best_bid "
          f"Rs.{bid if bid is not None else 'n/a'} | 1 lot = {qty}")
    print("-" * 64)

    resting_oid = used_px = None
    for d in DISCOUNTS:
        px = _round_tick(premium * (1 - d))
        if not (0 < px < premium):
            continue
        # must sit strictly BEHIND the best bid — never become the top bid
        if bid is not None and px >= bid:
            print(f"skip Rs.{px:.2f} ({d*100:.0f}%): not below best_bid Rs.{bid:.2f}")
            continue
        print(f"try LIMIT BUY {qty} @ Rs.{px:.2f} ({d*100:.0f}% below LTP) ...", end=" ")
        try:
            oid = cl.place_order(sym_id, "buy", qty, order_type="limit", limit_price=px)
        except Exception as e:
            print(f"place err: {str(e)[:70]}"); continue
        time.sleep(0.4)
        st = None
        try:
            st = cl.get_order_status(oid)
        except Exception as e:
            print(f"(status err {e})", end=" ")
        pos = [p for p in cl.open_positions() if p["sym_id"] == sym_id]
        if pos:
            print("FILLED unexpectedly -> flattening")
            try:
                print(" flatten:", cl.close_position(sym_id))
            except Exception as e:
                print(" FLATTEN ERR — CLOSE MANUALLY NOW:", e)
            return 3
        if _resting(st):
            print(f"ACCEPTED & RESTING (status={st}) oid={oid}")
            resting_oid, used_px = oid, px
            break
        print(f"not resting (status={st}) — trying closer")

    if not resting_oid:
        print("no price rested (band tight) — no order in book, zero risk.")
        return 2

    # cancel the resting order immediately, confirm cleared
    print("-" * 64)
    print(f"CANCEL resting order {resting_oid} @ Rs.{used_px:.2f} ...")
    cancelled = False
    for i in range(4):
        try:
            if cl.cancel_order(resting_oid):
                cancelled = True; break
        except Exception as e:
            print(f"  cancel {i + 1}: {e}")
        time.sleep(0.8)
    time.sleep(0.8)
    st2 = None
    try:
        st2 = cl.get_order_status(resting_oid)
    except Exception:
        pass
    pos2 = [p for p in cl.open_positions() if p["sym_id"] == sym_id]
    if pos2:
        print(f"!!! POSITION {pos2} — flattening")
        try:
            print(" flatten:", cl.close_position(sym_id))
        except Exception as e:
            print(" FLATTEN ERR — CLOSE MANUALLY NOW:", e)
            return 3
    print(f"CANCEL call={'OK' if cancelled else 'FAIL'} | final status={st2} "
          f"-> {'CLEARED' if _cleared(st2) else 'STILL RESTING'}")
    print("=" * 64)
    if _cleared(st2) and not pos2:
        print("RESULT: order ACCEPTED, RESTED in the book, CANCELLED cleanly, "
              "account flat. Zero loss. Full place->rest->cancel path proven.")
        return 0
    print(f"RESULT: NOT CLEAN — order {resting_oid} may still rest. CHECK TJ APP NOW.")
    return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "samsar2001@gmail.com"))
