"""REAL 1-lot fill test on ONE cash-funded Tradejini account.

Places a 1-lot MARKET BUY of the ~Rs.50 NIFTY weekly CE, confirms the FILL price,
then immediately MARKET SELLs to flatten — measuring the real round-trip cost
(spread + charges = live slippage). This DOES cost a small amount (the spread);
run only on a consenting, cash-funded account. ALWAYS flattens, even on error.

Uses the NEXT weekly (not today's expiry) so a stuck position can't hit same-day
settlement. Run on the VPS with env sourced:
    set -a; . ./.env; set +a
    .venv/bin/python3 test_fill.py radianmedia.org@gmail.com
"""
from __future__ import annotations

import csv
import io
import sys
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db import session_scope
from app.models import TradejiniConnection, User
from app import tradejini, tradejini_auth
from app.straddle_runner import ltp, _kget

TARGET_PREMIUM = 50.0


def _ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def discover_weekly(expiry_index: int = 1) -> dict:
    """NIFTY weekly chain at expiry_index (0=nearest, 1=next). Uses next by
    default so the fill test can't hit same-day settlement."""
    rows = list(csv.DictReader(io.StringIO(_kget("/instruments/NFO").decode("utf-8", "ignore"))))
    nopt = [r for r in rows if r.get("name") == "NIFTY" and r.get("instrument_type") in ("CE", "PE")]
    exps = sorted({r["expiry"] for r in nopt})
    expiry = exps[min(expiry_index, len(exps) - 1)]
    lot = int(nopt[0]["lot_size"])
    exp = [r for r in nopt if r["expiry"] == expiry]
    CE = {float(r["strike"]): r["instrument_token"] for r in exp if r["instrument_type"] == "CE"}
    return {"expiry": expiry, "lot": lot, "CE": CE}


def _order_row(cl, oid):
    j = cl._get("/api/oms/orders", {"symDetails": "true"})
    rows = (j or {}).get("d", []) if isinstance(j, dict) else []
    for o in rows:
        if str(o.get("orderId")) == str(oid):
            return o
    return None


def _await_fill(cl, oid, tries: int = 10):
    """Poll until the order is terminal; return (status, avg_price, fill_qty)."""
    for _ in range(tries):
        o = _order_row(cl, oid)
        if o:
            st = str(o.get("status", "")).lower()
            fq = float(o.get("fillQty") or 0)
            av = float(o.get("avgPrice") or 0)
            if fq > 0 or st in ("complete", "filled", "executed") or "reject" in st or "cancel" in st:
                return st, av, fq
        time.sleep(0.8)
    o = _order_row(cl, oid) or {}
    return (str(o.get("status", "unknown")).lower(),
            float(o.get("avgPrice") or 0), float(o.get("fillQty") or 0))


def main(email: str) -> int:
    ist = _ist_now()
    mo = ist.weekday() < 5 and "09:15" <= ist.strftime("%H:%M") <= "15:30"
    print(f"IST {ist:%Y-%m-%d %H:%M:%S} | market_open={mo}")
    if not mo:
        print("ABORT: market closed."); return 1
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

    cash = cl.buyable_cash_inr()
    print(f"client: {name} <{email}> | buyable cash Rs.{cash:,.2f}")

    chain = discover_weekly(1)
    expiry, lot, ce = chain["expiry"], int(chain["lot"]), chain["CE"]
    q = ltp(list(ce.values()))
    prem = {s: q.get(str(t)) for s, t in ce.items() if q.get(str(t)) is not None}
    if not prem:
        print("ABORT: no premiums"); return 1
    strike = min(prem, key=lambda s: abs(prem[s] - TARGET_PREMIUM))
    premium = prem[strike]
    need = premium * lot * 1.05
    print(f"target (NEXT weekly {expiry}): {strike:.0f} CE @ LTP Rs.{premium:.2f} | "
          f"1 lot={lot} | needs ~Rs.{need:,.0f}")
    if cash < need:
        print(f"WARN: availCash Rs.{cash:,.0f} < ~Rs.{need:,.0f} needed. A pay-in may not "
              f"have swept into spendable cash yet — attempting anyway as a probe; the broker "
              f"rejects (harmlessly) if truly insufficient, fills if the pay-in counts.")

    meta = cl.resolve_weekly_option("NIFTY", expiry, strike, "CE")
    sym_id, sym = meta["sym_id"], meta.get("symbol")
    print(f"contract: {sym} ({sym_id})")
    print("-" * 64)

    # BUY 1 lot at market
    print(f"MARKET BUY {lot} {sym} ...")
    buy_oid = cl.place_order(sym_id, "buy", lot, order_type="market")
    bst, bavg, bfq = _await_fill(cl, buy_oid)
    print(f"  buy {buy_oid}: status={bst} fillQty={bfq:.0f} avgPrice=Rs.{bavg:.2f}")

    # If the buy did NOT fill, there is nothing to test — report honestly, do not
    # claim success. (Defensively confirm no stray position.)
    if bfq <= 0:
        pos = [p for p in cl.open_positions() if p["sym_id"] == sym_id]
        if pos:
            print(f"  unexpected position despite no fill {pos} — flattening")
            try:
                cl.close_position(sym_id)
            except Exception as e:
                print("  flatten err:", e)
        print("=" * 64)
        print(f"RESULT: BUY NOT FILLED (status={bst}). Order reached the broker but was "
              f"rejected — check the order-book reason. No fill, no position, no loss.")
        return 2

    # FLATTEN — sell exactly what filled, and confirm via the SELL ORDER's fillQty
    # (authoritative), NOT the positions endpoint, which LAGS a fill by ~1-2s. A
    # stale position read here could trigger a re-close → oversell → naked short.
    sell_avg = 0.0
    sell_filled = 0.0
    try:
        time.sleep(0.5)
        res = cl.close_position(sym_id)
        print(f"  MARKET SELL -> {res}")
        if res.get("order_id"):
            sst, sell_avg, sell_filled = _await_fill(cl, res["order_id"])
            print(f"  sell {res['order_id']}: status={sst} fillQty={sell_filled:.0f} avgPrice=Rs.{sell_avg:.2f}")
    except Exception as e:
        print("  SELL ERROR:", e)

    # Trust the fill: if the sell filled the bought qty, we ARE flat (ignore a
    # lagging positions read). Only if it did NOT fill do we verify + retry.
    flat = sell_filled >= bfq - 1e-6
    if not flat:
        time.sleep(2.0)  # let the endpoint settle before trusting a position read
        for _ in range(3):
            pos = [p for p in cl.open_positions() if p["sym_id"] == sym_id]
            if not pos:
                flat = True; break
            print(f"  residual {pos} — closing remainder")
            try:
                r2 = cl.close_position(sym_id)
                if r2.get("order_id"):
                    _await_fill(cl, r2["order_id"])
            except Exception as e:
                print("   retry err:", e)
            time.sleep(1.5)

    print("-" * 64)
    if not flat:
        print(f"!!! NOT FLAT — POSITION OPEN in {sym}. CLOSE MANUALLY NOW in the TJ app.")
        return 3

    if bavg > 0 and sell_avg > 0:
        rt = (sell_avg - bavg) * lot
        print(f"ROUND TRIP: bought Rs.{bavg:.2f} / sold Rs.{sell_avg:.2f} "
              f"-> Rs.{rt:,.2f} gross (this is the spread cost for 1 lot)")
        print(f"  vs mid Rs.{premium:.2f}: buy slip Rs.{(bavg - premium) * lot:,.2f}, "
              f"sell slip Rs.{(premium - sell_avg) * lot:,.2f}")
    try:
        rp = cl.day_realized_pnl_inr()
        if rp is not None:
            print(f"  broker realized P&L (net of charges): Rs.{rp:,.2f}")
    except Exception:
        pass
    print("=" * 64)
    print("RESULT: FILL CONFIRMED both ways, account flat. Live order path fully working.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "radianmedia.org@gmail.com"))
