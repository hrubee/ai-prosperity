"""Watchdog + intraday GUARDIAN for the live straddle.

Two jobs, every ~2 min during market hours:

OBSERVE (always, read-only):
  - 🔔 alerts on every NEW fill, ⚠️ flags SLIPPAGE (actual vs engine-intended),
  - ⚠️ flags order REJECTIONS, 💓 alerts if the runner died during trading hours,
  - 📒 appends a full snapshot to watchdog.log. First run seeds a baseline (no spam).

GUARD (teeth — only force-closes an ORPHAN, never a managed position):
  The guardian reads the runner HEARTBEAT (``.straddle_heartbeat.json``: ts, pid,
  and the ``managed`` set of "<user_id>|<sym_id>" the runner is actively managing).
  It force-closes a NIFTY position WE opened (a row exists today) ONLY when no live
  runner is managing it:
    * heartbeat STALE (runner dead) ⇒ close immediately, OR
    * heartbeat FRESH but (user,sym_id) NOT in managed ⇒ close only after the
      orphan PERSISTS across 2 consecutive polls (an in-flight close settles
      between polls, so this never double-closes a position the runner is closing).
  Gates that make it impossible to race the runner or hit the wrong trade:
    * managed-set keyed by (user_id, sym_id) — a client SKIPPED in sizing is never
      "managed", so its leftover is correctly an orphan (the 2026-06-03 incident).
    * "ours" = sym_id in ANY of today's rows (even mis-marked-closed) — never
      touches a position we didn't open (untracked NIFTY ⇒ alert only).
    * time-partitioned 09:35–15:15 so it can't race the 15:25 square-off backstop.
    * its OWN flock so two overlapping watchdog invocations can't double-close.
  Deliberately NOT gated on straddle_live: an orphan must be flattened even after
  the master switch is turned off with a position still open.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

STATE = "/root/aiprosperity/backend/.watchdog_state.json"
LOG = "/root/aiprosperity/backend/watchdog.log"
HEARTBEAT = "/root/aiprosperity/backend/.straddle_heartbeat.json"
GUARD_LOCK = "/root/aiprosperity/backend/.straddle_guardian.lock"
SLIP_ALERT_PCT = 2.0   # flag a fill >2% worse than the engine intended

_guard_lock_fp = None   # kept alive for the process so the flock holds until exit


def _ist() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _load() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(s: dict) -> None:
    try:
        with open(STATE, "w") as f:
            json.dump(s, f)
    except Exception:
        pass


def _log(msg: str) -> None:
    try:
        with open(LOG, "a") as f:
            f.write(f"{_ist():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def _runner_status() -> str:
    try:
        return subprocess.run(["systemctl", "is-active", "straddle-shadow.service"],
                              capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:
        return "unknown"


def _acquire_guardian_lock() -> bool:
    """Single-instance lock for the guardian: a slow poll must not overrun the
    2-min cadence and let two guardians force-close the same position."""
    global _guard_lock_fp
    fp = open(GUARD_LOCK, "w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return False
    _guard_lock_fp = fp
    return True


def _read_heartbeat(stale_secs: int) -> tuple[bool, set, str]:
    """Returns (stale, managed, info). Missing OR old-ts ⇒ stale (runner dead);
    `managed` is the runner's live set of '<user_id>|<sym_id>' it owns — trusted
    ONLY when fresh."""
    try:
        with open(HEARTBEAT) as f:
            hb = json.load(f)
    except Exception:
        return True, set(), "no heartbeat (runner not running / crashed)"
    age = time.time() - float(hb.get("ts", 0) or 0)
    stale = age > stale_secs
    managed = set(hb.get("managed") or [])
    info = f"pid={hb.get('pid')} session={hb.get('session')} age={age:.0f}s managed={len(managed)}"
    return stale, managed, info + (" STALE" if stale else "")


def _guardian_conns(db) -> list:
    """Every client with a StraddlePosition row TODAY (any status), regardless of
    the master switch — a position we opened stays watchable even after
    STRADDLE_LIVE is flipped off with it open."""
    from sqlalchemy import select
    from app.models import StraddlePosition, TradejiniConnection
    from app import tradejini_auth
    from app.straddle_executor import _ist_today
    uids = db.execute(select(StraddlePosition.user_id).where(
        StraddlePosition.trade_date == _ist_today()).distinct()).scalars().all()
    out = []
    for uid in uids:
        c = db.execute(select(TradejiniConnection).where(
            TradejiniConnection.user_id == uid)).scalars().first()
        if c and tradejini_auth.has_auto_creds(c) and c.status != "disconnected":
            out.append(c)
    return out


def _guardian_action(is_ours: bool, key: str, is_managed: bool, hb_stale: bool,
                     prev_orphans: set, can_act: bool) -> str:
    """PURE decision for one broker NIFTY position — the guardian's safety contract.
    Returns:
      'untracked' — not a position we opened → alert only, NEVER close.
      'skip'      — a LIVE runner is managing it (fresh heartbeat + in managed) → leave it.
      'defer'     — it's our orphan but we're outside the act window (time-partition).
      'watch'     — our orphan, first sight under a FRESH runner → close next poll if it
                    persists (an in-flight runner close settles between polls).
      'close'     — flatten NOW: runner DEAD (stale heartbeat) OR a 2nd consecutive sight.
    Keep this covered by tests — every change here is a naked-short risk."""
    if not is_ours:
        return "untracked"
    if (not hb_stale) and is_managed:
        return "skip"
    if not can_act:
        return "defer"
    if hb_stale or key in prev_orphans:
        return "close"
    return "watch"


def run() -> None:
    ist = _ist()
    if ist.weekday() >= 5 or not ("09:15" <= ist.strftime("%H:%M") <= "15:35"):
        return  # market closed — no-op

    if not _acquire_guardian_lock():
        _log("guardian lock held — another watchdog is running, skipping")
        return

    from sqlalchemy import select
    from app.config import settings
    from app.db import session_scope
    from app.models import StraddlePosition, User
    from app import tradejini, tradejini_auth, telegram_notify
    from app.straddle_executor import _live_conns, _ist_today
    from app.straddle_squareoff import _is_nifty_option

    st = _load()
    first_run = "seen" not in st
    seen = set(st.get("seen", []))
    prev_orphans = set(st.get("guardian_seen", []))   # orphan keys from the LAST poll
    cur_orphans: set[str] = set()                     # orphan keys THIS poll
    today = _ist_today()
    alerts: list[str] = []
    hm = ist.strftime("%H:%M")

    # ownership signal for the guardian
    hb_stale, managed, hb_info = _read_heartbeat(settings.straddle_heartbeat_stale_secs)
    guard_on = settings.straddle_guardian_enabled
    can_act = guard_on and ("09:35" <= hm <= "15:15")   # time-partition vs square-off

    # RUNNER-DOWN matters only when we INTEND to trade. With the master switch off
    # the runner is deliberately stopped (e.g. post-incident) ⇒ don't cry wolf
    # every 2 min, or the alarm gets ignored when it's real. Re-arms on live/dry.
    status = _runner_status()
    expect_runner = settings.straddle_live or settings.straddle_dry_run
    if expect_runner and "09:35" <= hm <= "15:20" and status not in ("active", "activating"):
        alerts.append(f"💓 RUNNER DOWN (status={status}) during trading hours — CHECK NOW")

    with session_scope() as db:
        conns = _live_conns(db)
        gconns = _guardian_conns(db) if guard_on else []
        have = {c.user_id for c in conns}
        conns = conns + [c for c in gconns if c.user_id not in have]   # union, dedup
        if not conns:
            _log("no live clients")
        for conn in conns:
            u = db.get(User, conn.user_id)
            email = (u.email if u else conn.user_id) or conn.user_id
            try:
                cl = tradejini.TradejiniClient(
                    tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
                pos = cl.open_positions()
                fills = (cl._get("/api/oms/trades", {"symDetails": "true"}) or {}).get("d", []) or []
                orders = (cl._get("/api/oms/orders", {"symDetails": "true"}) or {}).get("d", []) or []
                lim = (cl._get("/api/oms/limits") or {}).get("d", {}) or {}
            except Exception as e:
                alerts.append(f"⚠️ {email}: broker read failed — {str(e)[:80]}")
                continue

            # today's rows for this client: engine-intended prices (slippage) AND
            # the set of sym_ids that are OURS (any status — even mis-marked-closed)
            rows = db.execute(select(StraddlePosition).where(
                StraddlePosition.user_id == conn.user_id,
                StraddlePosition.trade_date == today)).scalars().all()
            our_syms = {r.sym_id for r in rows if r.sym_id}
            intended: dict[str, float] = {}
            for r in rows:
                if r.entry_order_id and r.entry_px:
                    intended[str(r.entry_order_id)] = r.entry_px
                if r.exit_order_id and r.exit_px:
                    intended[str(r.exit_order_id)] = r.exit_px

            # ── GUARDIAN: flatten our orphans (no live runner managing them) ──
            done_syms: set = set()   # dedup: one decision/close per sym_id per poll
            for p in pos:
                sym_id = p.get("sym_id")
                sym = str(p.get("symbol") or sym_id or "")
                if not _is_nifty_option(sym):
                    continue
                if sym_id in done_syms:   # broker returned a duplicate row for this symId
                    continue
                done_syms.add(sym_id)
                key = f"{conn.user_id}|{sym_id}"
                act = _guardian_action(sym_id in our_syms, key, key in managed,
                                       hb_stale, prev_orphans, can_act)
                if act == "untracked":
                    # a NIFTY position we did NOT open — never touch someone else's
                    ukey = "untracked_" + str(conn.user_id) + "_" + str(sym_id)
                    if ukey not in seen and not first_run:
                        alerts.append(f"❓ {email}: UNTRACKED NIFTY {sym} {p.get('side')} "
                                      f"{p.get('size')} — not ours, NOT closing")
                    seen.add(ukey)
                    continue
                if act == "skip":
                    continue   # a LIVE runner owns it — never race the runner
                cur_orphans.add(key)               # ours + (stale OR unmanaged)
                if act == "defer":
                    continue   # outside the 09:35–15:15 window — square-off owns EOD
                if act == "close":
                    # dead runner ⇒ now; fresh-but-unmanaged ⇒ 2nd consecutive sight
                    try:
                        res = cl.close_position(sym_id, product=settings.straddle_product)
                    except Exception as e:
                        alerts.append(f"🛡️ {email}: GUARDIAN close FAILED for {sym} — {str(e)[:80]}")
                        _log(f"GUARDIAN-FAIL {email} {sym} {str(e)[:120]}")
                        continue
                    if res.get("closed"):
                        why = "runner DEAD" if hb_stale else "runner not managing it"
                        alerts.append(f"🛡️ {email}: GUARDIAN force-closed orphan {sym} "
                                      f"(oid={res.get('order_id')}) — {why}")
                        _log(f"GUARDIAN-CLOSE {email} {sym} sym_id={sym_id} {why} hb=[{hb_info}]")
                        for r in rows:           # mark the row(s) closed for our books
                            if r.sym_id == sym_id and r.status in ("open", "intent"):
                                r.status = "closed"
                                r.closed_at = datetime.now(timezone.utc)
                                r.detail = ((r.detail or "") + ";guardian_force_close")[:480]
                    cur_orphans.discard(key)       # closed, or already flat (settled)
                else:   # "watch"
                    alerts.append(f"🛡️ {email}: orphan {sym} (runner not managing) — "
                                  f"closing next poll if it persists [{hb_info}]")
                    _log(f"GUARDIAN-WATCH {email} {sym} sym_id={sym_id} hb=[{hb_info}]")

            for fl in fills:
                sym = str(fl.get("symId") or "")
                if "NIFTY" not in sym.upper():
                    continue
                fid = str(fl.get("fillId"))
                if fid in seen:
                    continue
                seen.add(fid)
                side = (fl.get("side") or "").upper()
                px = float(fl.get("fillPrice") or 0)
                qty = fl.get("fillQty")
                parts = sym.split("_")
                strike = parts[-2] if len(parts) >= 2 else "?"
                leg = parts[-1] if parts else "?"
                line = f"🔔 {email}: {side} {leg} {strike} {qty}@₹{px}"
                oid = str(fl.get("orderId"))
                ipx = intended.get(oid)
                if ipx:
                    worse = (ipx - px) if side == "SELL" else (px - ipx)
                    pct = 100 * worse / ipx if ipx else 0
                    if pct > SLIP_ALERT_PCT:
                        line += f"  ⚠️SLIP {pct:.0f}% worse (intended ₹{ipx})"
                if not first_run:
                    alerts.append(line)
                _log(f"FILL {email} {side} {leg} {strike} {qty}@{px} fid={fid} oid={oid} intended={ipx}")

            for o in orders:
                if str(o.get("status", "")).lower() != "rejected":
                    continue
                key = "rej_" + str(o.get("orderId"))
                if key in seen:
                    continue
                seen.add(key)
                rsn = str(o.get("reason", ""))[:70]
                _log(f"REJECT {email} {o.get('orderId')} {rsn}")
                if not first_run and "not enabled" not in rsn.lower():
                    alerts.append(f"⚠️ {email}: order REJECTED — {rsn}")

            prem = lim.get("premium")
            brok = lim.get("brokerage")
            _log(f"SNAP {email} pos={len(pos)} premiumField={prem} charges={brok} runner={status}")
            cap = settings.straddle_daily_loss_cap_inr
            try:
                loss = abs(float(prem or 0)) + abs(float(brok or 0))
                if cap > 0 and loss > 0.8 * cap and not first_run:
                    alerts.append(f"⚠️ {email}: day cost ≈₹{loss:.0f} near daily cap ₹{cap:.0f}")
            except (TypeError, ValueError):
                pass

    st["seen"] = list(seen)[-2000:]
    st["guardian_seen"] = sorted(cur_orphans)
    _save(st)

    _log(f"GUARD hb=[{hb_info}] can_act={can_act} orphans={sorted(cur_orphans)}")
    if first_run:
        _log(f"watchdog initialized — baseline {len(seen)} item(s), no alerts sent")
        return
    if alerts:
        telegram_notify.send_message(f"🐶 Straddle watchdog {hm} IST:\n" + "\n".join(alerts))
        _log("ALERTS " + " | ".join(a.replace(chr(10), " ") for a in alerts))


if __name__ == "__main__":
    run()
