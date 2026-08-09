"""F&O brain — publishes Indian index-future signals to the client bus (venue=tradejini).

The F&O counterpart of the crypto orchestrator's Phase-B publish. Each cycle, for
every configured instrument it:
  1. resolves the near-month FUTURE (Kite data token + Tradejini exec symbol);
  2. fetches that future's 5m candles from Kite and computes EMA-9/150 + ATR;
  3. on a FRESH cross, runs an AI Vision congestion veto (Llama-3.2-vision) — a
     choppy/whipsaw cross is blocked;
  4. otherwise publishes buy/sell tagged venue=tradejini with ref_price + an
     ATR stop, the worker sizing each client in whole lots at 2% risk.

WHY THE FUTURE, NOT THE INDEX: a future trades at a basis to its spot index
(NIFTY ~135 pts, BANKNIFTY ~400 pts). The executor sizes AND places the stop off
the published ref/sl. If those are index-domain while the client trades the
future, the real stop sits hundreds of points away from the fill and the client
eats multiples of the intended 2%. Driving every calculation off the future's own
price keeps ref_price, sl_price and the placed stop in one domain — the 2%
guarantee holds. See fno_instruments.resolve_near_month().
"""
from __future__ import annotations

import logging
import os
import ssl
import time
from datetime import datetime, timedelta, timezone

from . import brain_log, fno_instruments, kite_data, signal_bus, vision
from .config import settings
from .db import session_scope
from . import telegram_notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nifty-brain")

# ── config (env-overridable) ───────────────────────────────────
TIMEFRAME_MIN = int(os.environ.get("NIFTY_BRAIN_TF_MIN", "5"))
EMA_FAST = int(os.environ.get("NIFTY_BRAIN_EMA_FAST", "9"))
EMA_SLOW = int(os.environ.get("NIFTY_BRAIN_EMA_SLOW", "150"))
ATR_PERIOD = int(os.environ.get("NIFTY_BRAIN_ATR", "14"))
SL_ATR_MULT = float(os.environ.get("NIFTY_BRAIN_SL_ATR_MULT", "1.5"))
POLL_SEC = int(os.environ.get("NIFTY_BRAIN_POLL_SEC", "60"))
CHART_BARS = int(settings.fno_chart_bars)
IST = timezone(timedelta(hours=5, minutes=30))


def _ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


_CTX = _ctx()
# Per-instrument fresh-cross dedup: instrument key -> last cross bar ts (ms). A
# 5m cross shows on the last closed bar for up to ~5 min of 60s polls; this fires
# the (paid, ~few-second) Vision call + publish at most once per cross bar.
_last_published: dict[str, int] = {}


# ── indicators (pure python) ───────────────────────────────────
def _ema_series(closes: list[float], period: int) -> list[float]:
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA seed
    out = [ema]
    for px in closes[period:]:
        ema = px * k + ema * (1 - k)
        out.append(ema)
    return out  # aligned to closes[period-1:]


def _atr(bars: list[list], period: int) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        _, _, h, l, c = bars[i]
        pc = bars[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def compute_cross(bars: list[list]) -> dict:
    """{'signal': 'buy'|'sell'|'hold', 'close': px, 'atr': v} on the latest closed
    bar. Fresh cross only (this bar crossed, the prior bar had not)."""
    need = EMA_SLOW + 2
    if len(bars) < need:
        return {"signal": "hold", "close": bars[-1][4] if bars else 0.0, "atr": 0.0,
                "_note": f"need {need} bars, have {len(bars)}"}
    closes = [b[4] for b in bars]
    fast = _ema_series(closes, EMA_FAST)
    slow = _ema_series(closes, EMA_SLOW)
    n = min(len(fast), len(slow))
    fast, slow = fast[-n:], slow[-n:]
    f_prev, f_cur = fast[-2], fast[-1]
    s_prev, s_cur = slow[-2], slow[-1]
    signal = "hold"
    if f_prev <= s_prev and f_cur > s_cur:
        signal = "buy"
    elif f_prev >= s_prev and f_cur < s_cur:
        signal = "sell"
    return {"signal": signal, "close": closes[-1], "atr": _atr(bars, ATR_PERIOD)}


def build_signal(bars: list[list]) -> dict:
    """Pure: future bars → {signal, ref_price, sl_price, atr, cross_ts}. ref_price
    and sl_price are BOTH in the bars' price domain, so |ref-sl| is exactly the
    stop distance the executor sizes against AND places on the exchange. This is
    the 2%-risk invariant — proven by test_nifty_brain (no network)."""
    res = compute_cross(bars)
    sig, close, atr = res["signal"], res["close"], res["atr"]
    sl = 0.0
    if atr > 0 and sig == "buy":
        sl = round(close - SL_ATR_MULT * atr, 1)
    elif atr > 0 and sig == "sell":
        sl = round(close + SL_ATR_MULT * atr, 1)
    return {"signal": sig, "ref_price": close, "sl_price": sl, "atr": atr,
            "cross_ts": int(bars[-1][0]) if bars else 0}


# ── market hours ───────────────────────────────────────────────
def _market_open(now_ist: datetime) -> bool:
    if now_ist.weekday() >= 5:  # Sat/Sun
        return False
    mins = now_ist.hour * 60 + now_ist.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30  # 09:15–15:30 IST


# ── per-instrument evaluation ──────────────────────────────────
def _fetch_count() -> int:
    # enough for EMA-slow warmup + the chart window + cross detection
    return EMA_SLOW + CHART_BARS + 5


def evaluate_instrument(inst: fno_instruments.FnoInstrument, vision_on: bool) -> str:
    """Resolve → fetch future bars → cross → (vision veto) → publish. Returns a
    short status string. Isolated: a raise here is caught by the caller so one
    instrument can't take down the others."""
    nm = fno_instruments.resolve_near_month(inst)  # raises → caller skips
    bars = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())

    res = build_signal(bars)
    if res["signal"] == "hold":
        return f"hold({inst.key} {nm.tj_symbol} close={res['ref_price']})"

    cross_ts = res["cross_ts"]
    if _last_published.get(inst.key) == cross_ts:
        return f"dup({inst.key} bar={cross_ts})"  # already handled this cross bar

    # Staleness guard: `_last_published` is empty on restart, so a mid-session
    # restart would otherwise read the last CLOSED bar — which right after the
    # 09:15 open is still yesterday's 15:25 bar — and could act on a stale cross.
    # A genuine just-closed bar is < ~1 timeframe old; anything older is stale.
    age_min = (time.time() * 1000 - cross_ts) / 60000.0
    if age_min > 3 * TIMEFRAME_MIN:
        _last_published[inst.key] = cross_ts  # don't re-check every poll
        return f"stale({inst.key} bar_age={age_min:.0f}m)"

    side, ref, sl, atr = res["signal"], res["ref_price"], res["sl_price"], res["atr"]

    # Render the chart on every fresh cross (for the admin AI-Vision log), even
    # when the model is off. matplotlib-only; a render failure is non-fatal.
    chart = f"/tmp/fno_chart_{inst.key}.png"
    red_dots = None
    try:
        red_dots = vision.render_crossover_chart(
            bars, EMA_FAST, EMA_SLOW, chart, f"{inst.key} ({nm.tj_symbol})",
            CHART_BARS, TIMEFRAME_MIN)
    except Exception as e:
        log.warning("chart render failed for %s: %s", inst.key, e)
        chart = None

    # AI Vision congestion veto (veto-only — see vision.py). A failure falls back
    # to publishing (mirrors the crypto brain: a vision outage must not block trading).
    vision_evaluated, congested, reason, vsl = False, None, None, None
    if vision_on and chart:
        try:
            congested, reason, vsl = vision.vision_veto(
                nm.tj_symbol, chart, side, ref, CHART_BARS, TIMEFRAME_MIN)
            vision_evaluated = True
            log.info("[VISION] %s %s congestion=%s dots=%s vsl=%.1f :: %s",
                     inst.key, side, congested, red_dots, vsl or 0.0, (reason or "")[:160])
        except Exception as e:
            log.warning("vision failed for %s (%s) — publishing with ATR stop: %s",
                        inst.key, side, e)

    # Telegram: chart image + AI Vision verdict on every fresh cross (fire-and-forget).
    try:
        if chart:
            _arrow = "\U0001f7e2 BULLISH cross \u2192 BUY" if side == "buy" else "\U0001f534 BEARISH cross \u2192 SELL"
            if vision_evaluated:
                _verdict = "\U0001f6ab Congested \u2192 veto (take-profit only)" if congested else "\u2705 Clean \u2192 entry allowed"
            else:
                _verdict = "(AI vision not evaluated)"
            telegram_notify.send_photo(
                chart,
                f"<b>{inst.key} {nm.tj_symbol}</b>  F&O\\n{_arrow}\\nAI Vision: {_verdict}\\n{(reason or '')[:600]}")
    except Exception:
        pass

    def _log_event(action: str, signal_id: str | None):
        brain_log.record(
            instrument=inst.key, tj_symbol=nm.tj_symbol, side=side, ref_price=ref,
            sl_price=sl, atr=atr, red_dots=red_dots, vision_evaluated=vision_evaluated,
            congested=congested, vision_reason=reason, visual_sl=vsl, action=action,
            signal_id=signal_id, chart_path=chart)

    # A vision-congested cross is too choppy to OPEN a fresh whole-lot F&O
    # position — but the opposite EMA cross is still the take-profit. So instead
    # of suppressing the signal entirely (which would strand a winning client
    # holding the opposite side), publish it exit_only: the executor closes any
    # opposite holder and skips clients who are flat. The EMA opposite cross is
    # ALWAYS the take-profit; the veto only gates new entries.
    exit_only = bool(vision_evaluated and congested)

    with session_scope() as db:
        sig = signal_bus.publish_signal(
            db, nm.tj_symbol, side,
            sl_price=(None if exit_only else (sl or None)), ref_price=ref,
            source="nifty-brain", venue="tradejini", exit_only=exit_only)
        sid = sig.id
    _last_published[inst.key] = cross_ts  # one publish per cross bar

    # Telegram: 'trade placed' alert \u2014 one per published brain signal (fire-and-forget).
    try:
        _act = "take-profit / exit-only" if exit_only else "entry"
        _msg = f"\U0001f514 <b>NIFTY brain placed {side.upper()} {nm.tj_symbol}</b> \u2192 Tradejini ({_act}) ref={ref:.1f}"
        if not exit_only:
            _msg += f" sl={sl:.1f}"
        telegram_notify.send_message(_msg)
    except Exception:
        pass

    if exit_only:
        _log_event("exit-only", sid)
        log.info("exit-only %s %s ref=%.1f (congested → take-profit only, kite_token=%s) → signal %s",
                 side, nm.tj_symbol, ref, nm.kite_token, sid)
        return f"exit-only({inst.key} {side} congestion → take-profit)"

    _log_event("published" if vision_evaluated else ("fallback" if vision_on else "published"), sid)
    log.info("published %s %s ref=%.1f sl=%.1f atr=%.1f (kite_token=%s) → signal %s",
             side, nm.tj_symbol, ref, sl, atr, nm.kite_token, sid)
    return f"published({inst.key} {side})"


def evaluate_all(vision_on: bool) -> list[str]:
    out = []
    for inst in fno_instruments.active_instruments():
        try:
            out.append(evaluate_instrument(inst, vision_on))
        except Exception as e:
            out.append(f"skip({inst.key}:{str(e)[:80]})")
    return out


def run() -> None:
    insts = fno_instruments.active_instruments()
    vision_on = settings.fno_vision_enabled and vision.vision_available()
    log.info("F&O brain started: %s %dm EMA%d/%d poll=%ds vision=%s (publishes venue=tradejini)",
             ",".join(i.key for i in insts), TIMEFRAME_MIN, EMA_FAST, EMA_SLOW, POLL_SEC,
             "on" if vision_on else "OFF")
    if settings.fno_vision_enabled and not vision_on:
        log.warning("vision requested but unavailable (need NVIDIA_API_KEY + matplotlib) — running without veto")
    while True:
        try:
            now_ist = datetime.now(timezone.utc).astimezone(IST)
            if _market_open(now_ist):
                log.info("eval: %s", " | ".join(evaluate_all(vision_on)))
            # else: market closed — idle quietly
        except Exception as e:
            log.exception("brain loop error: %s", e)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    run()
