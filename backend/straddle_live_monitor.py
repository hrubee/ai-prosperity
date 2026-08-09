"""LIVE health watchdog — observe-only, alerts on Telegram, never trades.

Complements the guardian (straddle_watchdog, which force-CLOSES orphans): this one
WATCHES and SCREAMS. Runs every few minutes during a live session and checks the
handful of things that mean "the system is NOT working fine":

  CRITICAL (page immediately):
    * NAKED SHORT — any NIFTY option held SHORT at a client (net sell). The whole
      safety net exists to prevent this; if one ever appears, something defeated it.
    * NOT FLAT AT EOD — after 15:25 IST any NIFTY leg still open = square-off failed.
  WARN:
    * ORPHAN — a live NIFTY leg whose sym_id is in no open row AND not in the
      runner heartbeat (the guardian should be closing it; alert in case it can't).
    * RUNNER DARK — inside 09:35–15:20 with open positions but a stale/missing
      heartbeat (runner may have died holding a position).
    * LOSS CAP — client day-realized P&L at/below the daily loss cap.

Quiet when healthy (one OK line to the journal). Telegrams only WARN/CRITICAL, and
de-dups repeats within a run via a small state file so it doesn't spam every 3 min.

Run on the VPS (timer):  .venv/bin/python straddle_live_monitor.py
"""
from __future__ import annotations

import json
import os
import sys
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


_load_env(os.path.join(_HERE, ".env"))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import StraddlePosition, TradejiniConnection, User  # noqa: E402
from app import tradejini, tradejini_auth  # noqa: E402
from app.straddle_executor import _ist_today, _live_conns  # noqa: E402
from app.straddle_squareoff import _is_nifty_option  # noqa: E402

_ALERT_STATE = os.path.join(_HERE, ".straddle_monitor_alerts.json")


def _ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _tg(msg):
    try:
        from app import telegram_notify
        telegram_notify.send_message(msg)
    except Exception as e:
        print("telegram failed:", str(e)[:80])


def _dedup_send(key, msg):
    """Telegram a problem only if this key wasn't already alerted today (avoid
    every-3-min spam). CRITICAL keys re-send hourly; WARN once/day."""
    today = _ist_today()
    state = {}
    try:
        state = json.load(open(_ALERT_STATE))
    except Exception:
        pass
    if state.get("date") != today:
        state = {"date": today, "sent": {}}
    last = state["sent"].get(key)
    now = _ist_now().timestamp()
    crit = key.startswith("CRIT")
    if last is not None and (now - last) < (3600 if crit else 86400):
        return  # already alerted recently
    _tg(msg)
    state["sent"][key] = now
    try:
        json.dump(state, open(_ALERT_STATE, "w"))
    except Exception:
        pass


def main():
    ist = _ist_now()
    hm = ist.strftime("%H:%M")
    live = settings.straddle_live and not settings.straddle_dry_run
    if not live:
        print(f"[monitor {hm}] STRADDLE not live (live={settings.straddle_live} dry={settings.straddle_dry_run}) — idle.")
        return 0
    today = _ist_today()
    problems = []

    with session_scope() as db:
        conns = _live_conns(db)
        rows = db.execute(select(StraddlePosition).where(
            StraddlePosition.trade_date == today)).scalars().all()
        open_syms = {r.sym_id for r in rows if r.status in ("intent", "open") and r.sym_id}
        all_syms = {r.sym_id for r in rows if r.sym_id}

        # heartbeat (runner liveness/ownership)
        managed, hb_stale = set(), True
        try:
            from app.straddle_watchdog import _read_heartbeat
            hb_stale, managed, _info = _read_heartbeat(
                stale_secs=int(os.environ.get("STRADDLE_HEARTBEAT_STALE_SECS", "180")))
        except Exception as e:
            print("  heartbeat read err:", str(e)[:80])

        for conn in conns:
            u = db.get(User, conn.user_id)
            email = (u.email if u else conn.user_id)
            try:
                cl = tradejini.TradejiniClient(
                    tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
                positions = cl.open_positions()
            except Exception as e:
                problems.append(("WARN", f"CONN {email}: positions read failed ({str(e)[:60]})"))
                continue

            for p in positions:
                if not _is_nifty_option(p.get("symbol", "")):
                    continue
                sid = p.get("sym_id")
                side = p.get("side")
                # CRITICAL: a NIFTY option held SHORT — the failure the net prevents
                if side == "sell":
                    problems.append(("CRIT", f"NAKED SHORT {email}: {p.get('symbol')} size={p.get('size')} "
                                             f"— the whole safety net is to stop this. INVESTIGATE NOW."))
                # CRITICAL: not flat after the 15:25 square-off
                if hm > "15:25":
                    problems.append(("CRIT", f"NOT FLAT @ {hm} {email}: {p.get('symbol')} still open "
                                             f"after square-off window — square-off may have failed."))
                # WARN: orphan — live leg in no open row AND not managed by a live runner
                key = f"{conn.user_id}|{sid}"
                if sid not in all_syms and key not in managed:
                    problems.append(("WARN", f"ORPHAN {email}: {p.get('symbol')} at broker, in no row "
                                             f"+ not in heartbeat — guardian should close; verify."))

            # WARN: runner dark with open positions during session
            if "09:35" <= hm <= "15:20" and open_syms and hb_stale:
                problems.append(("WARN", f"RUNNER DARK: heartbeat stale/missing at {hm} with "
                                         f"{len(open_syms)} open leg(s) — runner may have died holding."))

            # WARN: daily loss cap
            cap = settings.straddle_daily_loss_cap_inr
            if cap > 0:
                try:
                    pnl = cl.day_realized_pnl_inr()
                    if pnl is not None and pnl <= -abs(cap):
                        problems.append(("WARN", f"LOSS CAP {email}: day P&L Rs.{pnl:,.0f} <= -Rs.{cap:,.0f} "
                                                 f"— circuit breaker should be blocking new entries."))
                except Exception:
                    pass

    if not problems:
        print(f"[monitor {hm}] OK — live, {len(open_syms)} open leg(s), heartbeat {'stale' if hb_stale else 'fresh'}, no naked short, no orphan.")
        return 0
    for sev, msg in problems:
        print(f"[monitor {hm}] {sev}: {msg}")
        icon = "🛑" if sev == "CRIT" else "⚠️"
        _dedup_send(f"{sev}:{msg[:40]}", f"{icon} STRADDLE {sev}: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
