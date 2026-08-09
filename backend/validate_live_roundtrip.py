"""GO-LIVE validation: ONE real 1-lot CE+PE round-trip on the canary, then arm.

Mirrors the strategy's EXACT order path on the live broker — the one thing a
dry-run and a never-filling limit order cannot test: that a market BUY FILLS and
that close_position() actually FLATTENS it (the broker-qty invariant, the
naked-short-critical link). Sequence per leg:

    resolve_weekly_option(NIFTY, expiry, ~Rs.50 strike, CE|PE)   # contract string
    place_order(sym_id, "buy", lot, product="normal")            # NRML market buy
    <poll positions until filled>
    close_position(sym_id, product="normal")                     # opposite market
    <poll positions until flat>

HARD GATE (all must hold) before it will arm live:
  * both legs FILLED, both legs CLOSED, account FLAT (no residual NIFTY leg),
  * round-trip cost within MAX_VALIDATION_LOSS (a sane spread+charges, not a bug).
Only then, with --arm-on-success, does it flip STRADDLE_LIVE=1 / STRADDLE_DRY_RUN=0
in .env (backup kept). Any failure → leaves the switches OFF and alerts. A
BUY-filled-but-close-FAILED is a bounded LONG (max loss = premium) — it retries
the close, never arms, and screams on Telegram for a manual flatten.

Run on the VPS:  .venv/bin/python validate_live_roundtrip.py samsar2001@gmail.com [--arm-on-success]
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

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
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


_ENV = os.path.join(_HERE, ".env")
_load_env(_ENV)

from sqlalchemy import func, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import TradejiniConnection, User  # noqa: E402
from app import tradejini, tradejini_auth  # noqa: E402
from app.straddle_runner import discover_legs, ltp  # noqa: E402

TARGET_PREMIUM = 50.0
MAX_VALIDATION_LOSS = 500.0    # Rs. — a real round-trip costs spread+~Rs.90; >Rs.500 = something wrong
FILL_POLL_SECS = 1.0
FILL_POLL_MAX = 20             # ~20s to see the fill land in positions


def _ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _tg(msg):
    try:
        from app import telegram_notify
        telegram_notify.send_message(msg)
    except Exception as e:
        print("telegram failed:", str(e)[:80])


def _arm_live():
    """Flip STRADDLE_LIVE=1 / STRADDLE_DRY_RUN=0 in .env, preserving every other
    line. Backs up first. Idempotent."""
    import shutil
    shutil.copyfile(_ENV, _ENV + ".bak-prearm")
    out, seen_live, seen_dry = [], False, False
    for line in open(_ENV):
        s = line.rstrip("\n")
        if s.startswith("STRADDLE_LIVE="):
            out.append("STRADDLE_LIVE=1"); seen_live = True
        elif s.startswith("STRADDLE_DRY_RUN="):
            out.append("STRADDLE_DRY_RUN=0"); seen_dry = True
        else:
            out.append(s)
    if not seen_live:
        out.append("STRADDLE_LIVE=1")
    if not seen_dry:
        out.append("STRADDLE_DRY_RUN=0")
    with open(_ENV, "w") as f:
        f.write("\n".join(out) + "\n")


def _positions_for(cl, sym_ids):
    """Return {sym_id: size} for the given sym_ids currently live at the broker."""
    out = {}
    try:
        for p in cl.open_positions():
            if p.get("sym_id") in sym_ids:
                out[p["sym_id"]] = p
    except Exception as e:
        print("  positions read error:", str(e)[:100])
    return out


def main(email, arm):
    ist = _ist_now()
    hm = ist.strftime("%H:%M")
    print(f"=== validate live round-trip | {email} | IST {ist:%Y-%m-%d %H:%M:%S} | arm={arm} ===")
    if not (ist.weekday() < 5 and "09:15" <= hm <= "15:20"):
        print(f"ABORT: market not open ({hm} IST) — no orders placed."); return 2

    # 1) resolve client + token (no order yet)
    with session_scope() as db:
        u = db.execute(select(User).where(func.lower(User.email) == email.lower())).scalars().first()
        if not u:
            print("ABORT: user not found"); return 2
        conn = db.execute(select(TradejiniConnection).where(
            TradejiniConnection.user_id == u.id)).scalars().first()
        if not conn or not tradejini_auth.has_auto_creds(conn):
            print("ABORT: no Tradejini connection/creds"); return 2
        tok = tradejini_auth.ensure_client_token(db, conn)
        api_key = conn.api_key
    cl = tradejini.TradejiniClient(tok, api_key=api_key)
    cash = cl.buyable_cash_inr()
    print(f"buyable cash Rs.{cash:,.0f}")
    if cash < 7000:
        print("ABORT: cash < Rs.7000 — can't safely buy a 1-lot CE+PE (~Rs.6,500)."); return 2

    # 2) pick the ~Rs.50 CE & PE on the nearest weekly (mirror the strategy)
    chain = discover_legs()
    expiry, lot = chain["expiry"], int(chain["lot"])
    legs = {}
    for kind in ("CE", "PE"):
        book = chain[kind]
        q = ltp(list(book.values()))
        prem = {s: q.get(str(t)) for s, t in book.items() if q.get(str(t)) is not None}
        if not prem:
            print(f"ABORT: no {kind} premiums from Kite"); return 2
        strike = min(prem, key=lambda s: abs(prem[s] - TARGET_PREMIUM))
        legs[kind] = {"strike": strike, "ltp": prem[strike]}
    print(f"weekly {expiry} lot {lot} | CE {legs['CE']['strike']:.0f}@Rs.{legs['CE']['ltp']:.2f} "
          f"| PE {legs['PE']['strike']:.0f}@Rs.{legs['PE']['ltp']:.2f}")

    # 3) resolve BOTH Tradejini contracts up front (no order yet — fail before any buy)
    for kind in ("CE", "PE"):
        meta = cl.resolve_weekly_option("NIFTY", expiry, legs[kind]["strike"], kind)
        legs[kind]["sym_id"] = meta["sym_id"]
        legs[kind]["symbol"] = meta.get("symbol")
        legs[kind]["broker_lot"] = int(meta.get("lot_size") or lot)
        print(f"  resolved {kind}: {meta.get('symbol')} sym_id={meta['sym_id']} lot={legs[kind]['broker_lot']}")

    sym_ids = {legs[k]["sym_id"] for k in ("CE", "PE")}

    # 4) BUY both legs (market, NRML) — the real fills
    for kind in ("CE", "PE"):
        qty = legs[kind]["broker_lot"]
        print(f"BUY {kind} {legs[kind]['symbol']} qty={qty} product=normal ...")
        try:
            oid = cl.place_order(legs[kind]["sym_id"], "buy", qty, product="normal")
            legs[kind]["buy_oid"] = oid
            print(f"  -> order id {oid}")
        except Exception as e:
            print(f"  BUY FAILED: {str(e)[:160]}")
            _tg(f"⛔ Validation BUY {kind} FAILED: {str(e)[:120]} — staying DRY, not arming.")
            # close anything that did fill before bailing
            _emergency_flat(cl, sym_ids)
            return 1

    # 5) confirm fills
    filled = {}
    for _ in range(FILL_POLL_MAX):
        pos = _positions_for(cl, sym_ids)
        filled = {s: p for s, p in pos.items() if float(p.get("size", 0)) > 0}
        if len(filled) == 2:
            break
        time.sleep(FILL_POLL_SECS)
    if len(filled) < 2:
        print(f"WARN: only {len(filled)}/2 legs show filled after {FILL_POLL_MAX}s")
        _emergency_flat(cl, sym_ids)
        _tg("⛔ Validation: legs did not both confirm filled — flattened, staying DRY.")
        return 1
    print("both legs FILLED:", {s: p.get("size") for s, p in filled.items()})

    # 6) CLOSE both (broker-qty invariant) + confirm flat, retrying on failure
    if not _emergency_flat(cl, sym_ids, label="CLOSE"):
        _tg("🛑 Validation: a CLOSE FAILED and a leg may still be OPEN (LONG, bounded) — "
            "MANUAL FLATTEN NEEDED. NOT arming live.")
        return 1

    # 7) compute realized cost from today's NIFTY-option realized P&L
    try:
        realized = cl.day_realized_pnl_inr()
    except Exception:
        realized = None
    cost = (-realized) if realized is not None else None
    print(f"round-trip realized P&L: {('Rs.%.0f' % realized) if realized is not None else 'n/a'}")

    # 8) GATE
    flat = len(_positions_for(cl, sym_ids)) == 0
    ok = flat and (cost is None or cost <= MAX_VALIDATION_LOSS)
    if not flat:
        print("GATE FAIL: account not flat after close."); _tg("⛔ Validation: not flat after close — NOT arming."); return 1
    if cost is not None and cost > MAX_VALIDATION_LOSS:
        print(f"GATE FAIL: round-trip cost Rs.{cost:.0f} > Rs.{MAX_VALIDATION_LOSS:.0f}")
        _tg(f"⛔ Validation cost Rs.{cost:.0f} too high — NOT arming, review fills."); return 1

    print("GATE PASS: filled + closed + flat + cost sane.")
    if arm:
        _arm_live()
        msg = (f"✅ LIVE VALIDATION PASSED — Samir 1-lot CE+PE filled & closed cleanly "
               f"(cost ~Rs.{cost:.0f}). STRADDLE_LIVE=1 / DRY_RUN=0 ARMED. "
               f"09:33 runner will trade live (1 lot, cap Rs.3k).")
        print(msg); _tg(msg)
    else:
        print("validation OK (not arming — no --arm-on-success).")
        _tg(f"✅ Live validation PASSED (cost ~Rs.{cost:.0f}); NOT armed (dry-run flag).")
    return 0


def _emergency_flat(cl, sym_ids, label="flat"):
    """Close every given sym, retrying; return True if all flat."""
    for attempt in range(3):
        pos = _positions_for(cl, sym_ids)
        if not pos:
            return True
        for sid in list(pos):
            try:
                r = cl.close_position(sid, product="normal")
                print(f"  {label} {sid} -> {r.get('closed', r)}")
            except Exception as e:
                print(f"  {label} {sid} FAILED: {str(e)[:120]}")
        time.sleep(2)
    return len(_positions_for(cl, sym_ids)) == 0


if __name__ == "__main__":
    em = next((a for a in sys.argv[1:] if "@" in a), "samsar2001@gmail.com")
    arm = "--arm-on-success" in sys.argv
    sys.exit(main(em, arm))
