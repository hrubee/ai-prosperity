"""CRASH-SAFE SQUARE-OFF (Phase-4 safety net).

Runs INDEPENDENTLY of the strategy runner — its own systemd timer. For every
client we have live exposure on TODAY (from the durable StraddlePosition rows,
not in-memory state), it force-flattens the legs WE opened by querying the
broker's ACTUAL positions. So even if the runner crashed, hung, or the Kite feed
stalled mid-session, no long leg is left running past close. Idempotent: an
already-flat account is a no-op.

SURGICAL (multi-client safe): it closes only the sym_ids that appear in OUR
rows. A NIFTY option the client opened themselves (not in our rows) is NEVER
auto-closed — it is loudly ALERTED instead. Driven by the durable rows, so it
works even if the live master switch was turned off after positions opened.

TIMING — CRITICAL (avoids an oversell→naked-short race): this is a BACKSTOP, not
the primary closer. The engine's own in-loop square-off fires at 15:20 and the
runner exits by ~15:21. Schedule THIS timer at **15:25 and 15:30 IST — NEVER
15:20**. If both this and the engine called close_position at 15:20, each would
read the same live netQty before the other's fill settled and place its own
opposite market order → double-sell → naked short (unlimited risk). By 15:25 the
engine has already closed (this reads 0 = no-op) OR the runner died (this is then
the sole closer — exactly the case it exists for).
"""
from __future__ import annotations

import fcntl
import logging
import os
from collections import defaultdict

log = logging.getLogger("straddle-squareoff")

_SQUAREOFF_LOCK = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), ".straddle_squareoff.lock")
_lock_fp = None


def _acquire_lock() -> bool:
    """Single-instance lock (Fix #1). square_off_all is the one closer an operator
    can hand-trigger AND the 15:25/15:30 timers can overlap. Two concurrent
    square-offs would each read the same live netQty before the other's sell
    settled → double-sell → NAKED SHORT (unlimited risk). This makes that
    impossible — a second square-off finds the lock held and skips."""
    global _lock_fp
    fp = open(_SQUAREOFF_LOCK, "w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return False
    _lock_fp = fp
    return True


def _is_nifty_option(symbol: str) -> bool:
    """True only for a plain-NIFTY option (the strategy's sole instrument).
    Excludes the look-alike indices whose symbols also contain 'NIFTY'
    (BANKNIFTY / FINNIFTY / MIDCPNIFTY) so the backstop never touches them."""
    s = (symbol or "").upper()
    if "NIFTY" not in s or any(x in s for x in ("BANK", "FIN", "MIDCP")):
        return False
    return s.endswith("CE") or s.endswith("PE") or "CE" in s or "PE" in s


def square_off_all() -> dict:
    from sqlalchemy import select
    from .config import settings
    from .db import session_scope
    from .models import StraddlePosition, TradejiniConnection, User
    from . import tradejini, tradejini_auth, telegram_notify
    from .straddle_executor import _ist_today, _utcnow, _live_conns

    if not _acquire_lock():
        log.warning("square-off: another square-off holds the lock — skipping (prevents double-close)")
        return {"closed": 0, "untracked": 0, "errors": 0, "scanned": 0, "skipped": "locked"}

    today = _ist_today()
    closed, errors, untracked, scanned = [], [], [], 0

    with session_scope() as db:
        # ALL of today's rows (ANY status). Using all of them for attribution is
        # what closes the orphan hole: a row mis-marked `closed` after an
        # accept-then-reject still has its sym_id here, so a broker position that
        # is actually still open gets caught — we do NOT trust the row status to
        # decide whether to look at the broker.
        all_rows = db.execute(
            select(StraddlePosition).where(
                StraddlePosition.trade_date == today)).scalars().all()
        rows_by_user: dict[str, list] = defaultdict(list)
        for r in all_rows:
            rows_by_user[r.user_id].append(r)
        our_syms_by_user = {uid: {r.sym_id for r in rs if r.sym_id}
                            for uid, rs in rows_by_user.items()}

        # Scan every client we traded today UNION every currently-live client —
        # so an orphan on a live client with NO row at all still surfaces (alert).
        user_ids = set(rows_by_user) | {c.user_id for c in _live_conns(db)}
        if not user_ids:
            log.info("square-off: no straddle activity + no live clients — nothing to do")
            return {"closed": 0, "untracked": 0, "errors": 0, "scanned": 0}

        for user_id in sorted(user_ids):
            u = db.get(User, user_id)
            email = (u.email if u else user_id) or user_id
            conn = db.execute(
                select(TradejiniConnection).where(
                    TradejiniConnection.user_id == user_id)).scalars().first()
            if conn is None or not tradejini_auth.has_auto_creds(conn):
                if user_id in rows_by_user:  # we traded them but can't reach them
                    errors.append((email, "no usable connection to square off"))
                continue
            scanned += 1
            our_syms = our_syms_by_user.get(user_id, set())
            try:
                cl = tradejini.TradejiniClient(
                    tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
                positions = cl.open_positions()
            except Exception as e:
                errors.append((email, f"connect/positions: {str(e)[:100]}"))
                continue

            live_syms = set()
            processed = set()   # dedup (Fix #1): one close per sym_id even if the
            for p in positions:  # broker returns two rows for the same symId
                if not _is_nifty_option(p.get("symbol", "")):
                    continue
                sid = p.get("sym_id")
                if sid in processed:
                    continue
                processed.add(sid)
                live_syms.add(sid)
                if sid in our_syms:            # ours (even if a row says closed) → flatten
                    try:
                        res = cl.close_position(sid, product=settings.straddle_product)
                        closed.append((email, p["symbol"], res.get("closed", res)))
                    except Exception as e:
                        errors.append((email, f"close {p.get('symbol')}: {str(e)[:80]}"))
                else:                          # untracked NIFTY → never auto-close, ALERT
                    untracked.append((email, p.get("symbol")))

            # reconcile rows: any open/intent row whose broker position is gone → closed
            for r in rows_by_user.get(user_id, []):
                if r.status in ("intent", "open") and r.sym_id not in live_syms:
                    r.status, r.closed_at = "closed", _utcnow()
                    r.detail = ((r.detail or "") + ";squareoff-flat")[:480]

    # Stay quiet unless something actually happened (closed / untracked / errors).
    if not (closed or untracked or errors):
        log.info("square-off: scanned %d client(s), all flat — nothing to do", scanned)
        return {"closed": 0, "untracked": 0, "errors": 0, "scanned": scanned}

    summary = (f"\U0001f6d1 Square-off: {len(closed)} leg(s) flattened across "
               f"{scanned} client(s).")
    if untracked:
        summary += ("\n⚠️ UNTRACKED NIFTY at broker (NOT touched — orphan or client's own?):\n"
                    + "\n".join(f"  {e}: {sym}" for (e, sym) in untracked))
    if errors:
        summary += "\n⚠️ ERRORS (CHECK MANUALLY):\n" + "\n".join(f"  {e}: {d}" for (e, d) in errors)
    for (em, sym, _) in closed:
        summary += f"\n  ✓ {em}: closed {sym}"
    log.info("square-off: closed=%d untracked=%d errors=%d scanned=%d",
             len(closed), len(untracked), len(errors), scanned)
    telegram_notify.send_message(summary)
    print(summary)
    return {"closed": len(closed), "untracked": len(untracked),
            "errors": len(errors), "scanned": scanned}


if __name__ == "__main__":
    square_off_all()
