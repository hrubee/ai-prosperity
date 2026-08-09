"""FNO straddle status / logs for the /fno Telegram command.

Called by ai_telegram_bot.py as a subprocess (runs in the aiprosperity venv so it
can reach the DB + broker). `fno_status.py` -> status; `fno_status.py logs` ->
recent activity. Plain text, owner-only (the bot gates auth before calling this).
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta

HERE = "/root/aiprosperity/backend"
for _l in open(os.path.join(HERE, ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)


def _ist():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _svc(unit):
    try:
        return subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                              text=True, timeout=8).stdout.strip()
    except Exception:
        return "?"


def status():
    out = []
    now = _ist()
    live = os.environ.get("STRADDLE_LIVE", "0")
    dry = os.environ.get("STRADDLE_DRY_RUN", "0")
    mode = "🟢 LIVE" if (live == "1" and dry != "1") else ("🟡 DRY-RUN" if dry == "1" else "⚪ SHADOW")
    out.append(f"📊 FNO Straddle — {now:%a %d-%b %H:%M} IST")
    out.append(f"mode: {mode}  | max {os.environ.get('STRADDLE_MAX_LOTS', '?')} lot · cap ₹{os.environ.get('STRADDLE_DAILY_LOSS_CAP_INR', '?')}")
    out.append(f"runner: {_svc('straddle-shadow.service')}  · watchdog: {_svc('straddle-watchdog.timer')}")
    try:
        from sqlalchemy import func, select
        from app.db import session_scope
        from app.models import StraddlePosition, TradejiniConnection, User
        from app.straddle_executor import _ist_today
        from app import tradejini, tradejini_auth
        with session_scope() as db:
            rows = db.execute(select(StraddlePosition).where(
                StraddlePosition.trade_date == _ist_today()).order_by(
                StraddlePosition.opened_at)).scalars().all()
            eng = 0.0
            wins = losses = opn = 0
            for r in rows:
                if r.entry_px is not None and r.exit_px is not None:
                    p = (r.exit_px - r.entry_px) * (r.qty or 0)
                    eng += p
                    wins += p > 0
                    losses += p < 0
                opn += r.status in ("open", "intent")
            out.append(f"today: {len(rows)} trades ({wins}W/{losses}L) · {opn} open · engine ₹{eng:+,.0f}")
            canary = (os.environ.get("STRADDLE_CANARY_EMAIL", "") or "").split(",")[0].strip()
            if canary:
                u = db.execute(select(User).where(func.lower(User.email) == canary.lower())).scalars().first()
                c = db.execute(select(TradejiniConnection).where(
                    TradejiniConnection.user_id == u.id)).scalars().first() if u else None
                if c:
                    cl = tradejini.TradejiniClient(
                        tradejini_auth.ensure_client_token(db, c), api_key=c.api_key)
                    pos = list(cl.open_positions())
                    out.append(f"💰 {canary}: broker ₹{cl.day_realized_pnl_inr():+,.0f} · "
                               f"cash ₹{cl.buyable_cash_inr():,.0f} · {len(pos)} open")
                    for p in pos[:6]:
                        out.append(f"   • {p.get('symbol')} {p.get('side')} {p.get('size')}")
    except Exception as e:
        out.append(f"(read error: {str(e)[:140]})")
    return "\n".join(out)


def logs(n=16):
    out = []
    try:
        with open(os.path.join(HERE, "watchdog.log")) as f:
            tail = f.readlines()[-n:]
        out.append(f"📜 watchdog.log (last {len(tail)}):")
        out += [t.rstrip()[:160] for t in tail]
    except Exception as e:
        out.append(f"(log read error: {str(e)[:80]})")
    return "\n".join(out)[:3800]


if __name__ == "__main__":
    print(logs() if (len(sys.argv) > 1 and sys.argv[1].startswith("log")) else status())
