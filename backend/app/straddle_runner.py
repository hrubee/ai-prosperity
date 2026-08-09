"""Intraday runner for the options straddle strategy (Phases 2-3).

Orchestrates the live session: at 09:35 it picks the closest-~Rs.50 CE & PE on the
near weekly expiry, then each minute feeds their premiums into the StraddleEngine
and collects buy/sell ACTIONS. Two modes:
  - SHADOW (default): logs what it WOULD trade, places nothing — safe to run live.
  - replay(date): re-runs a historical day from Kite minute candles (validation).

Phase 4 plugs in here: pass an `executor(action, leg_meta)` callback to turn each
engine action into a real per-client Tradejini order. Until then, executor=None.
"""
from __future__ import annotations

import csv
import fcntl
import io
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timedelta

from . import kite_data
from .kite_data import KITE_API, _auto_login, _ctx
from .options_straddle import StraddleConfig, StraddleEngine

log = logging.getLogger("straddle")
_CTX = _ctx()

# ── single-instance lock + heartbeat (restart-safety primitives) ────
# Deterministic paths next to the backend package (== /root/aiprosperity/backend/
# on the VPS). The lock makes a second runner impossible; the heartbeat is the
# OWNERSHIP signal the guardian reads to know which positions are being actively
# managed (so it can flatten orphans without ever racing a live runner).
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LOCK_PATH = os.path.join(_BACKEND_DIR, ".straddle_runner.lock")
_HB_PATH = os.path.join(_BACKEND_DIR, ".straddle_heartbeat.json")
_lock_fp = None   # held for the life of the process once acquired


def _acquire_lock() -> bool:
    """Take the exclusive single-instance lock. Returns True if we hold it (or
    already do — idempotent within a process, so run_live_session→run_shadow_session
    is fine), False if ANOTHER live runner holds it. The fd is intentionally never
    closed: the flock releases only on process exit, which is exactly the lifetime
    we want."""
    global _lock_fp
    if _lock_fp is not None:
        return True
    fp = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return False
    try:
        fp.write(f"{os.getpid()}\n")
        fp.flush()
    except Exception:
        pass
    _lock_fp = fp
    return True


def _write_heartbeat(managed: list, open_legs: set, session: str) -> None:
    """Atomically write the runner heartbeat the guardian reads. `managed` is the
    list of "<user_id>|<sym_id>" the runner is actively managing this instant."""
    try:
        payload = {
            "ts": time.time(),
            "iso": _ist_now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": os.getpid(),
            "session": session,
            "open_legs": sorted(open_legs),
            "managed": list(managed),
        }
        tmp = f"{_HB_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, _HB_PATH)   # atomic — the guardian never reads a half file
    except Exception as e:
        log.warning("straddle heartbeat write failed: %s", e)


def _clear_heartbeat() -> None:
    """Remove the heartbeat on a CLEAN session end (square-off flat). A crash
    instead leaves the last (now-stale-ts) heartbeat, which the guardian correctly
    reads as 'runner dead' and flattens any orphan."""
    try:
        os.remove(_HB_PATH)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("straddle heartbeat clear failed: %s", e)


def _launched_via() -> str:
    # systemd sets INVOCATION_ID / JOURNAL_STREAM for service units.
    if os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM"):
        return "systemd"
    return "manual"

# Kite caps historical at ~3 req/s; keep a safe floor between every call and
# retry once on a 429 so a burst never crashes the session.
_MIN_GAP = 0.4
_last_call = [0.0]


def _kget(path: str) -> bytes:
    gap = _MIN_GAP - (time.monotonic() - _last_call[0])
    if gap > 0:
        time.sleep(gap)
    tok = _auto_login()
    req = urllib.request.Request(
        f"{KITE_API}{path}",
        headers={"X-Kite-Version": "3",
                 "Authorization": f"token {kite_data.settings.kite_api_key}:{tok}"})
    for attempt in range(2):
        try:
            r = urllib.request.urlopen(req, timeout=30, context=_CTX).read()
            _last_call[0] = time.monotonic()
            return r
        except urllib.error.HTTPError as e:
            _last_call[0] = time.monotonic()
            if e.code == 429 and attempt == 0:
                time.sleep(1.5)
                continue
            raise


def _discover_legs_kite() -> dict:
    """Return {'expiry', 'lot', 'CE': {strike: token}, 'PE': {strike: token}} for
    the nearest live NIFTY weekly expiry. LEGACY Kite path (login currently broken)."""
    rows = list(csv.DictReader(io.StringIO(_kget("/instruments/NFO").decode("utf-8", "ignore"))))
    nopt = [r for r in rows if r.get("name") == "NIFTY" and r.get("instrument_type") in ("CE", "PE")]
    expiry = sorted({r["expiry"] for r in nopt})[0]
    lot = int(nopt[0]["lot_size"])
    exp = [r for r in nopt if r["expiry"] == expiry]
    CE = {float(r["strike"]): r["instrument_token"] for r in exp if r["instrument_type"] == "CE"}
    PE = {float(r["strike"]): r["instrument_token"] for r in exp if r["instrument_type"] == "PE"}
    return {"expiry": expiry, "lot": lot, "CE": CE, "PE": PE}


def _minute_candles(token: str, day_from: str, day_to: str):
    j = json.loads(_kget(f"/instruments/historical/{token}/minute?from={day_from}&to={day_to}"))
    return [(datetime.strptime(c[0][:19], "%Y-%m-%dT%H:%M:%S"), float(c[4]))
            for c in (j.get("data") or {}).get("candles", [])]


def _ltp_kite(tokens: list) -> dict:
    """Live last-traded premium for instrument tokens (LEGACY Kite path).
    CHUNKED (200/req) so the 09:35 full-chain query (hundreds of strikes) can't hit
    Kite's per-request quote cap / URL-length limit and silently truncate — a
    truncated response would drop strikes and could pick an off-band leg."""
    if not tokens:
        return {}
    out = {}
    for i in range(0, len(tokens), 200):
        batch = tokens[i:i + 200]
        q = "&".join(f"i={t}" for t in batch)
        j = json.loads(_kget(f"/quote/ltp?{q}"))
        data = j.get("data") or {}
        for t in batch:
            d = data.get(str(t)) or data.get(t)
            if d:
                out[str(t)] = float(d.get("last_price", 0.0))
    return out


# ── data source: Tradejini (live WS) is the default — Kite login is broken.
#    Set STRADDLE_DATA_SRC=kite to fall back to the legacy Kite path. ────────────
_TJ_FEED = None


def _use_tradejini() -> bool:
    return os.environ.get("STRADDLE_DATA_SRC", "tradejini").strip().lower() != "kite"


def discover_legs() -> dict:
    """Chain for the nearest weekly {'expiry','lot','CE':{strike:token},'PE':{strike:token}}.
    Tradejini WS by default; also starts a background L1 feed over the strike band so the
    synchronous ltp() below can read live premiums (token = Tradejini excToken)."""
    if not _use_tradejini():
        return _discover_legs_kite()
    from . import tradejini_data as td
    global _TJ_FEED
    chain = td.discover_chain("NIFTY")
    all_ex = list(chain["CE"].values()) + list(chain["PE"].values())
    _TJ_FEED = td.ChainFeed().start(all_ex)
    log.info("straddle data: Tradejini WS feed started over %d strikes (expiry %s, lot %d)",
             len(all_ex), chain["expiry"], chain["lot"])
    return chain


def ltp(tokens: list) -> dict:
    """Live premiums {str(token): ltp}. Reads the Tradejini WS feed (default) or Kite."""
    if not _use_tradejini():
        return _ltp_kite(tokens)
    if _TJ_FEED is None or not tokens:
        return {}
    _TJ_FEED.wait_ready(tokens, 8)
    return _TJ_FEED.ltp(tokens)


def pick_closest_premium(prem_by_strike: dict, target: float) -> float | None:
    best, bd = None, 1e18
    for s, p in prem_by_strike.items():
        if p is not None and abs(p - target) < bd:
            best, bd = s, abs(p - target)
    return best


# ── Runner ─────────────────────────────────────────────────────
class StraddleRunner:
    """One trading day. Pick legs at 09:35, then tick() each minute with premiums."""

    def __init__(self, cfg: StraddleConfig, lot: int, ce_leg: tuple, pe_leg: tuple,
                 executor=None):
        # ce_leg / pe_leg = (strike, token)
        self.cfg, self.lot = cfg, lot
        self.ce_strike, self.ce_token = ce_leg
        self.pe_strike, self.pe_token = pe_leg
        self.engine = StraddleEngine(cfg)
        self.executor = executor          # Phase 4: callback(action, meta) -> places orders
        self.actions = []                 # full action log

    def tick(self, now: datetime, ce_prem: float, pe_prem: float) -> list:
        acts = self.engine.on_minute(now, {"CE": ce_prem, "PE": pe_prem})
        for a in acts:
            meta = {"strike": self.ce_strike if a["leg"] == "CE" else self.pe_strike,
                    "token": self.ce_token if a["leg"] == "CE" else self.pe_token,
                    "lot": self.lot, "time": now}
            a["meta"] = meta
            self.actions.append((now, a))
            log.info("straddle %s %s %s @Rs%.1f strike=%.0f (%s)%s",
                     a["side"].upper(), a["leg"], "", a["premium"], meta["strike"], a["reason"],
                     "" if self.executor is None else " [LIVE]")
            if self.executor is not None:        # Phase 4 — place real orders
                try:
                    self.executor(a, meta)
                except Exception as e:           # never let execution crash the loop
                    log.warning("straddle executor error: %s", e)
        return acts

    def virtual_pnl(self) -> float:
        return self.engine.day_pnl(self.lot)


# ── entrypoints ────────────────────────────────────────────────
def replay(date_str: str) -> dict:
    """Re-run a historical day from Kite minute candles (validation)."""
    chain = discover_legs()
    frm, to = f"{date_str}+09:15:00", f"{date_str}+15:30:00"
    # 09:35 premiums for strike selection (scan a band around spot)
    def at935(tokmap, lo, hi):
        out = {}
        for s, tok in tokmap.items():
            if not (lo <= s <= hi):
                continue
            for dt, p in _minute_candles(tok, frm, to):
                if (dt.hour, dt.minute) == (9, 35):
                    out[s] = p
                    break
        return out
    ce935 = at935(chain["CE"], 23400, 24400)
    pe935 = at935(chain["PE"], 22600, 23500)
    ce_s = pick_closest_premium(ce935, 50.0)
    pe_s = pick_closest_premium(pe935, 50.0)
    if ce_s is None or pe_s is None:
        return {"error": "no ~Rs.50 strike found in band"}
    cfg = StraddleConfig()
    r = StraddleRunner(cfg, chain["lot"], (ce_s, chain["CE"][ce_s]), (pe_s, chain["PE"][pe_s]))
    ce_ser = dict(_minute_candles(chain["CE"][ce_s], frm, to))
    pe_ser = dict(_minute_candles(chain["PE"][pe_s], frm, to))
    last_ce = last_pe = None
    for t in sorted(set(ce_ser) | set(pe_ser)):
        last_ce = ce_ser.get(t, last_ce)
        last_pe = pe_ser.get(t, last_pe)
        if last_ce is None or last_pe is None:
            continue
        r.tick(t, last_ce, last_pe)
    return {"date": date_str, "ce_strike": ce_s, "pe_strike": pe_s,
            "lot": chain["lot"], "pnl": r.virtual_pnl(),
            "ce_entries": r.engine.legs["CE"].entries, "pe_entries": r.engine.legs["PE"].entries,
            "n_actions": len(r.actions)}


def _ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _in_late_start_refuse_window(start_hm: str) -> bool:
    """True if an IST 'HH:MM' start is a restart/late start that must NOT open a
    fresh session — inside the trading window, after the 09:35 arming it cannot
    replay (Fix #3). 09:33 → False (normal start); 09:37–15:20 → True (refuse);
    after 15:20 / pre-market → False. Pure + boundary-tested."""
    return "09:37" <= start_hm <= "15:20"


def _carry(cur, last):
    """Carry the last-known premium across a momentary missing quote (Fix #4) so a
    stop / the 15:20 square-off is never skipped on a one-minute data gap."""
    return cur if cur is not None else last


def run_shadow_session(executor=None, make_executor=None, cfg=None):
    """Run ONE live session (IST market hours): wait to 09:35, pick the ~Rs.50 legs
    from live quotes, then each minute feed live premiums to the engine and LOG the
    actions. executor=None and make_executor=None → SHADOW (places nothing).

    ``make_executor(chain, cfg) -> executor`` lets the caller build an executor
    AFTER the weekly chain is known (the canary executor needs the expiry to
    resolve the exact Tradejini contract). Safe to launch at ~09:33 IST daily."""
    cfg = cfg or StraddleConfig()
    if not _acquire_lock():
        log.error("straddle: another runner holds the lock (%s) — refusing to start", _LOCK_PATH)
        return None
    mode0 = "SHADOW" if (executor is None and make_executor is None) else "LIVE"
    log.info("straddle %s session starting (IST %s) — pid=%s launched=%s lock=%s",
             mode0, _ist_now().strftime("%H:%M"), os.getpid(), _launched_via(), _LOCK_PATH)
    # Restart / late-start guard (Fix #3): the strategy ARMS at 09:35 and that
    # reference can't be replayed. A process reaching startup INSIDE the trading
    # window (09:37–15:20) is a restart or a late start — it must NOT open a fresh
    # straddle (the 2026-06-03 incident: a 2nd runner re-armed DIFFERENT strikes).
    # Refuse the session; the heartbeat goes absent ⇒ the guardian flattens any leg
    # left open. A normal start (timer ~09:33) is well before 09:37 and proceeds.
    # Override with STRADDLE_ALLOW_LATE_START=1 (testing only).
    start_hm = _ist_now().strftime("%H:%M")
    if _in_late_start_refuse_window(start_hm) and os.environ.get("STRADDLE_ALLOW_LATE_START") != "1":
        log.error("straddle: started %s IST inside the trading window (restart/late) — "
                  "REFUSING to open a session; guardian will flatten any open leg.", start_hm)
        _clear_heartbeat()
        try:
            from . import telegram_notify
            telegram_notify.send_message(
                f"⛔ Straddle runner started {start_hm} IST (restart/late) — refusing a fresh "
                "session (can't replay the 09:35 arming); guardian will flatten any open leg.")
        except Exception:
            pass
        return None
    chain = discover_legs()
    if executor is None and make_executor is not None:
        executor = make_executor(chain, cfg)
    # heartbeat session label — "live"/"dry" when an executor exists, else "shadow"
    session_label = ("dry" if getattr(executor, "dry_run", False) else "live") \
        if executor is not None else "shadow"
    ce_tokens = list(chain["CE"].values())
    pe_tokens = list(chain["PE"].values())

    # wait until 09:35 IST — beat the heart so the guardian sees a live runner from
    # process start (no positions yet ⇒ managed is empty)
    while _ist_now().time().strftime("%H:%M") < "09:35":
        _write_heartbeat([], set(), session_label)
        time.sleep(5)

    # 09:35 leg selection from one batched LTP call per side. Build strike->premium
    # by looking up each token's LTP — ltp() keys are str(token). (A prior version
    # did `int(token) in <string-keyed map>`, always empty → "could not pick legs"
    # abort every day. Do NOT reintroduce an int/str key mismatch here.)
    ce_ltp = ltp(ce_tokens)
    pe_ltp = ltp(pe_tokens)
    ce_prem = {s: ce_ltp.get(str(t)) for s, t in chain["CE"].items() if ce_ltp.get(str(t)) is not None}
    pe_prem = {s: pe_ltp.get(str(t)) for s, t in chain["PE"].items() if pe_ltp.get(str(t)) is not None}
    ce_s = pick_closest_premium(ce_prem, cfg.target_premium)
    pe_s = pick_closest_premium(pe_prem, cfg.target_premium)
    if ce_s is None or pe_s is None:
        log.warning("straddle: could not pick ~Rs.%.0f legs; aborting session", cfg.target_premium)
        return
    log.info("straddle legs: CE %s@Rs%.1f  PE %s@Rs%.1f  (expiry %s lot %d)",
             ce_s, ce_prem[ce_s], pe_s, pe_prem[pe_s], chain["expiry"], chain["lot"])
    runner = StraddleRunner(cfg, chain["lot"], (ce_s, chain["CE"][ce_s]),
                            (pe_s, chain["PE"][pe_s]), executor=executor)

    # minute loop until 15:20 IST
    last_min = None
    last_cep = last_pep = None
    try:
        while _ist_now().time().strftime("%H:%M") <= "15:20":
            now = _ist_now()
            # Heartbeat at the TOP of every iteration (~10s), so liveness is
            # decoupled from a slow/long tick. `managed` = the (user|sym_id) pairs
            # the engine is holding open RIGHT NOW — the guardian's ownership gate.
            open_legs = {n for n, lg in runner.engine.legs.items() if lg.in_pos}
            managed = executor.managed_keys(open_legs) if executor is not None else []
            _write_heartbeat(managed, open_legs, session_label)
            cur = now.strftime("%H:%M")
            if cur != last_min:
                last_min = cur
                try:
                    q = ltp([runner.ce_token, runner.pe_token])
                    cep = q.get(str(runner.ce_token)); pep = q.get(str(runner.pe_token))
                    # Carry last-known premium across a momentary missing quote so a
                    # stop / the 15:20 square-off is NEVER skipped on a data gap
                    # (Fix #4). The engine logic is unchanged — it just always gets
                    # a premium for each open leg.
                    last_cep = _carry(cep, last_cep)
                    last_pep = _carry(pep, last_pep)
                    if last_cep is not None and last_pep is not None:
                        runner.tick(now.replace(second=0, microsecond=0), last_cep, last_pep)
                except Exception as e:
                    log.warning("straddle tick error: %s", e)
                # Refresh the heartbeat with POST-tick state so a slow fan-out tick
                # can't make a LIVE runner look stale to the guardian (Fix #6).
                open_legs = {n for n, lg in runner.engine.legs.items() if lg.in_pos}
                managed = executor.managed_keys(open_legs) if executor is not None else []
                _write_heartbeat(managed, open_legs, session_label)
            time.sleep(10)
    finally:
        # Clean exit (square-off flat) ⇒ remove the heartbeat. A crash/SIGKILL
        # instead leaves a stale-ts heartbeat ⇒ the guardian flattens any orphan.
        _clear_heartbeat()

    mode = "SHADOW" if executor is None else "LIVE"
    log.info("straddle session done [%s]: virtual P&L Rs.%.0f over %d actions (CE %d / PE %d entries)",
             mode, runner.virtual_pnl(), len(runner.actions),
             runner.engine.legs["CE"].entries, runner.engine.legs["PE"].entries)
    return runner


def run_live_session():
    """Run ONE live session, fanning the engine's actions to EVERY connected
    client, each sized to their wallet. Reconciles prior rows first (restart-safe
    DB state-sync, no orders). Gated by ``STRADDLE_LIVE``: when off, this is a
    pure shadow run (no orders). When on (optionally restricted to one account by
    ``STRADDLE_CANARY_EMAIL``), it places real orders. This + the crash-safe
    square-off are the only places real orders originate. At session end it
    logs+alerts the engine virtual P&L vs each client's broker realized P&L."""
    from .config import settings
    from .options_straddle import StraddleConfig as _Cfg
    from .straddle_executor import StraddleClientExecutor, reconcile_on_restart
    if not _acquire_lock():
        log.error("straddle: another runner holds the lock (%s) — refusing to start", _LOCK_PATH)
        return None
    dry = settings.straddle_dry_run
    if not (settings.straddle_live or dry):
        log.info("straddle: STRADDLE_LIVE=0 — SHADOW (no orders placed)")
        return run_shadow_session()
    if dry:
        log.info("straddle: DRY-RUN — full order path (resolution + sizing), places NOTHING")
    log.info("straddle: reconcile %s", reconcile_on_restart())
    cfg = _Cfg()  # engine virtual P&L stays per-1-lot; per-client sizing in executor
    holder: dict = {}

    def _mk(chain, c):
        ex = StraddleClientExecutor(chain["expiry"], dry_run=dry)
        holder["ex"] = ex
        return ex

    runner = run_shadow_session(cfg=cfg, make_executor=_mk)
    _report_eod(runner, holder.get("ex"))
    return runner


def _report_eod(runner, executor) -> None:
    """EOD readout: engine virtual P&L per lot, and per client their virtual
    (×lots) vs the broker's actual realized P&L — the live-vs-backtest gap. Best
    effort: if the broker doesn't expose realized P&L, say so (read contract note)."""
    from .db import session_scope
    from . import tradejini, tradejini_auth, telegram_notify
    from .straddle_executor import _email_of
    from .models import TradejiniConnection
    from sqlalchemy import select
    vpl = runner.virtual_pnl() if runner is not None else 0.0   # per 1 lot
    lots_by_user = {s["user_id"]: s["lots"] for s in (executor.sized if executor else [])}
    lines = [f"\U0001f4ca Straddle EOD: engine virtual Rs.{vpl:,.0f}/lot."]
    if executor is None or not lots_by_user:
        lines.append("  (no live clients traded)")
    else:
        try:
            with session_scope() as db:
                for user_id, lots in lots_by_user.items():
                    conn = db.execute(select(TradejiniConnection).where(
                        TradejiniConnection.user_id == user_id)).scalars().first()
                    if conn is None:
                        continue
                    email = _email_of(db, user_id)
                    try:
                        cl = tradejini.TradejiniClient(
                            tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
                        actual = cl.day_realized_pnl_inr()
                    except Exception as e:
                        lines.append(f"  {email} ({lots} lot): read failed — {str(e)[:60]}")
                        continue
                    exp = vpl * lots
                    if actual is None:
                        lines.append(f"  {email} ({lots} lot): expected ~Rs.{exp:,.0f}; "
                                     f"actual n/a — read contract note")
                    else:
                        slip = actual - exp
                        lines.append(f"  {email} ({lots} lot): expected ~Rs.{exp:,.0f} vs "
                                     f"actual Rs.{actual:,.0f} -> slippage Rs.{slip:,.0f}")
        except Exception as e:
            lines.append(f"  (client P&L read failed: {str(e)[:100]})")
    msg = "\n".join(lines)
    log.info(msg)
    try:
        telegram_notify.send_message(msg)
    except Exception:
        pass


def deploy_safe() -> tuple[bool, str]:
    """Deploy-guard (Fix 5). Returns (safe_to_deploy, reason). UNSAFE when the
    market is open on a live straddle day AND any position is open — restarting the
    service then orphans the open position (the 2026-06-03 root cause). Stage the
    change; apply after 15:20 square-off confirms flat. The scp deploy step should
    gate on the `deploy-check` CLI (exit 0 = safe, 1 = refuse)."""
    from .config import settings
    ist = _ist_now()
    market_open = ist.weekday() < 5 and "09:15" <= ist.strftime("%H:%M") <= "15:30"
    if not market_open:
        return True, "market closed"
    if not (settings.straddle_live or settings.straddle_dry_run):
        return True, "straddle not live (STRADDLE_LIVE=0)"
    try:
        from sqlalchemy import select
        from .db import session_scope
        from .models import StraddlePosition
        from .straddle_executor import _ist_today
        with session_scope() as db:
            n = len(db.execute(select(StraddlePosition.id).where(
                StraddlePosition.trade_date == _ist_today(),
                StraddlePosition.status.in_(["intent", "open"]))).all())
    except Exception as e:
        # fail SAFE for a deploy guard: if we can't prove flat, refuse
        return False, f"could not verify flat ({str(e)[:80]}) — refusing during market hours"
    if n > 0:
        return False, f"{n} open/intent position(s) during market hours — refuse (apply after 15:20)"
    return True, "live but flat"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "shadow":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        run_shadow_session()
    elif len(sys.argv) > 1 and sys.argv[1] in ("live", "canary"):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        run_live_session()
    elif len(sys.argv) > 1 and sys.argv[1] in ("deploy-check", "deploy_check"):
        ok, why = deploy_safe()
        print(("SAFE: " if ok else "REFUSE: ") + why)
        sys.exit(0 if ok else 1)
    else:
        d = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
        print(json.dumps(replay(d), default=str, indent=2))
