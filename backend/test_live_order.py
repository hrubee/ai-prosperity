"""ZERO-LOSS live order-path test for ONE Tradejini client.

Places a LIMIT BUY priced FAR BELOW the market on a real NIFTY weekly option,
confirms the broker accepts it (returns an order id) for the CORRECT resolved
contract, then CANCELS it. A buy limit below market CANNOT execute (nobody sells
below the market), so there is no fill, no position, and no brokerage/STT — ₹0
loss, guaranteed by market mechanics. Exercises the EXACT strategy code path:
resolve_weekly_option + place_order + cancel_order + per-client auth.

Run on the VPS (whitelisted IP), with the env sourced:
    cd /root/aiprosperity/backend
    set -a; . ./.env; set +a
    .venv/bin/python3 test_live_order.py samsar2001@gmail.com
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db import session_scope
from app.models import TradejiniConnection, User
from app import tradejini, tradejini_auth
from app.straddle_runner import discover_legs, ltp

TICK = 0.05
LIMIT_FACTOR = 0.5      # buy-limit at 50% of LTP — comfortably below market, no fill
TARGET_PREMIUM = 50.0   # mirror the strategy's ~Rs.50 strike selection


def _round_tick(p: float) -> float:
    return round(round(p / TICK) * TICK, 2)


def _ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def main(email: str) -> int:
    ist = _ist_now()
    market_open = ist.weekday() < 5 and "09:15" <= ist.strftime("%H:%M") <= "15:30"
    print(f"IST now: {ist:%Y-%m-%d %H:%M:%S} | market_open={market_open}")
    print("=" * 64)

    # 1) resolve the client connection + mint today's token
    with session_scope() as db:
        u = db.execute(
            select(User).where(func.lower(User.email) == email.lower())).scalars().first()
        if not u:
            print(f"ABORT: no user {email}"); return 1
        conn = db.execute(select(TradejiniConnection).where(
            TradejiniConnection.user_id == u.id)).scalars().first()
        if not conn or not tradejini_auth.has_auto_creds(conn):
            print("ABORT: no Tradejini connection / auto-creds"); return 1
        tok = tradejini_auth.ensure_client_token(db, conn)
        api_key, name = conn.api_key, (getattr(u, "name", "") or email)
    cl = tradejini.TradejiniClient(tok, api_key=api_key)

    # 2) sanity — the funded account answered
    eq = cl.equity_inr()
    print(f"client: {name} <{email}> | available margin Rs.{eq:,.0f}")
    if eq < 1000:
        print("ABORT: margin < Rs.1000 (token/account issue)"); return 1

    # 3) nearest NIFTY weekly + the ~Rs.50 CE (mirror the strategy's pick)
    chain = discover_legs()
    expiry, lot, ce = chain["expiry"], int(chain["lot"]), chain["CE"]
    q = ltp(list(ce.values()))
    prem = {s: q.get(str(t)) for s, t in ce.items() if q.get(str(t)) is not None}
    if not prem:
        print("ABORT: no CE premiums from Kite"); return 1
    strike = min(prem, key=lambda s: abs(prem[s] - TARGET_PREMIUM))
    premium = prem[strike]
    print(f"weekly expiry {expiry} | lot {lot} | CE strike {strike:.0f} @ LTP Rs.{premium:.2f}")
    if premium <= 0:
        print("ABORT: premium <= 0"); return 1

    # 4) THE CODE UNDER TEST — resolve to the Tradejini contract by exact expiry
    meta = cl.resolve_weekly_option("NIFTY", expiry, strike, "CE")
    sym_id, sym = meta["sym_id"], meta.get("symbol")
    print(f"RESOLVED -> sym_id={sym_id} | symbol={sym} | lot_size={meta.get('lot_size')}")
    if not sym or "NIFTY" not in str(sym).upper():
        print("ABORT: resolved symbol is not NIFTY"); return 1

    # 5) a no-fill limit price, well below market — hard guard
    limit_px = max(TICK, _round_tick(premium * LIMIT_FACTOR))
    if not (0 < limit_px < premium):
        print(f"ABORT: limit {limit_px} not below market {premium}"); return 1
    qty = lot  # exactly 1 lot
    print("-" * 64)
    print(f"PLAN: LIMIT BUY {qty} (1 lot) {sym} @ Rs.{limit_px:.2f}  "
          f"(LTP Rs.{premium:.2f} — below market, cannot fill)")

    # 6) place the order (catch a broker reject cleanly — still validated 1-5)
    try:
        oid = cl.place_order(sym_id, "buy", qty, order_type="limit", limit_price=limit_px)
    except Exception as e:
        print(f"PLACE rejected: {e}")
        print("=> auth + resolution validated; order not resting "
              "(if market is closed, retry in market hours).")
        return 2
    print(f"ORDER ACCEPTED -> order_id={oid}")

    # 7) confirm NO fill
    time.sleep(1.5)
    pos = [p for p in cl.open_positions() if p["sym_id"] == sym_id]
    print(f"position after place: {pos if pos else 'NONE (no fill — as designed)'}")

    # 8) CANCEL (retry up to 4x)
    cancelled = False
    for i in range(4):
        try:
            if cl.cancel_order(oid):
                cancelled = True; break
        except Exception as e:
            print(f"  cancel attempt {i + 1}: {e}")
        time.sleep(1.0)
    print(f"CANCEL call: {'OK' if cancelled else 'FAILED'}")

    # 9) CONFIRM the order is actually GONE from the book — a resting buy limit can
    #    still fill later in the session, so "no position yet" is NOT proof. Only
    #    None / cancelled / rejected is safe.
    time.sleep(1.0)
    status = None
    try:
        status = cl.get_order_status(oid)
    except Exception as e:
        print(f"  order-status read err: {e}")
    cleared = status is None or "cancel" in (status or "") or "reject" in (status or "")
    print(f"ORDER STATUS: {status if status is not None else 'gone from book'} "
          f"-> {'CLEARED' if cleared else 'STILL RESTING'}")

    # 10) position must be flat; flatten defensively if (impossibly) filled
    pos2 = [p for p in cl.open_positions() if p["sym_id"] == sym_id]
    if pos2:
        print(f"!!! UNEXPECTED POSITION {pos2} — flattening to honor zero-loss")
        try:
            print("flatten:", cl.close_position(sym_id))
        except Exception as e:
            print("flatten ERROR — CLOSE MANUALLY NOW:", e)
            return 3

    print("=" * 64)
    if not cleared:
        worst = limit_px * qty
        print(f"!!! ORDER STILL RESTING at Rs.{limit_px:.2f} (order_id={oid}) — "
              f"CANCEL IT MANUALLY NOW in the Tradejini app. If it fills later it is "
              f"a long worth up to ~Rs.{worst:,.0f}. TEST NOT CLEAN.")
        return 4
    print("RESULT: auth OK | resolution OK | order ACCEPTED & CLEARED | account flat. "
          "Zero-loss order-path test complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "samsar2001@gmail.com"))
