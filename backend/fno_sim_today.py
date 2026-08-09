"""Replay the live NIFTY brain on TODAY's 5m candles for all configured F&O
instruments, open -> now. Reuses the brain's OWN cross/ATR/SL functions + Kite
data, so it reproduces exactly what the brain saw and would publish today, then
tracks each fresh cross forward (SL hit / opposite-cross TP / still open) for a
points P&L. Read-only: no orders, no DB writes."""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = "/root/aiprosperity/backend"
for _l in open(os.path.join(HERE, ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)

from app import fno_instruments, kite_data
from app.nifty_brain import (_ema_series, _atr, EMA_FAST, EMA_SLOW, ATR_PERIOD,
                             SL_ATR_MULT, TIMEFRAME_MIN, _fetch_count)

IST = timezone(timedelta(hours=5, minutes=30))


def ist(ms):
    return datetime.fromtimestamp(ms / 1000, IST)


def main():
    today = datetime.now(IST).date()
    print("FNO BRAIN SIMULATION  -  {:%a %d-%b-%Y}  (open -> now)".format(today))
    print("logic: {}m candles | EMA{}/{} cross | SL = {}x ATR{} | exit = opposite cross".format(
        TIMEFRAME_MIN, EMA_FAST, EMA_SLOW, SL_ATR_MULT, ATR_PERIOD))
    print("=" * 80)

    fired = 0
    for inst in fno_instruments.active_instruments():
        try:
            nm = fno_instruments.resolve_near_month(inst)
            bars = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())
        except Exception as e:
            print("\n{:<11} skip ({})".format(inst.key, str(e)[:70]))
            continue
        if not bars or len(bars) < EMA_SLOW + 2:
            print("\n{:<11} not enough bars ({})".format(inst.key, len(bars) if bars else 0))
            continue

        closes = [b[4] for b in bars]
        fast = _ema_series(closes, EMA_FAST)
        slow = _ema_series(closes, EMA_SLOW)
        n = min(len(fast), len(slow))
        fast, slow = fast[-n:], slow[-n:]
        base = len(bars) - n  # bars index for aligned index 0

        # every cross in the window (aligned idx -> side)
        crosses = []
        for i in range(1, n):
            fp, fc, sp, sc = fast[i - 1], fast[i], slow[i - 1], slow[i]
            if fp <= sp and fc > sc:
                crosses.append((i, "buy"))
            elif fp >= sp and fc < sc:
                crosses.append((i, "sell"))
        cross_side = {i: s for i, s in crosses}

        cur = closes[-1]
        cur_t = ist(int(bars[-1][0]))
        bias = "EMA9 > EMA150 (bullish)" if fast[-1] > slow[-1] else "EMA9 < EMA150 (bearish)"
        print("\n{:<11} {:<16} now {:>9.1f} @ {:%H:%M}   EMA9={:.1f} EMA150={:.1f}  -> {}".format(
            inst.key, nm.tj_symbol, cur, cur_t, fast[-1], slow[-1], bias))

        today_cr = [(i, s) for (i, s) in crosses if ist(int(bars[base + i][0])).date() == today]
        if not today_cr:
            if crosses:
                li, ls = crosses[-1]
                lb = bars[base + li]
                print("   no fresh cross today.  last cross: {} {:%d-%b %H:%M} @ {:.1f}".format(
                    ls.upper(), ist(int(lb[0])), lb[4]))
            else:
                print("   no cross in window.")
            continue

        for (ci, side) in today_cr:
            fired += 1
            cb = bars[base + ci]
            ref = cb[4]
            atr = _atr(bars[:base + ci + 1], ATR_PERIOD)
            sl = round(ref + SL_ATR_MULT * atr, 1) if side == "sell" else round(ref - SL_ATR_MULT * atr, 1)
            ent_t = ist(int(cb[0]))

            exit_px = exit_t = reason = None
            for j in range(base + ci + 1, len(bars)):
                hi, lo, cl = bars[j][2], bars[j][3], bars[j][4]
                if side == "sell" and hi >= sl:
                    exit_px, exit_t, reason = sl, ist(int(bars[j][0])), "SL hit"
                    break
                if side == "buy" and lo <= sl:
                    exit_px, exit_t, reason = sl, ist(int(bars[j][0])), "SL hit"
                    break
                aj = j - base
                if aj in cross_side and cross_side[aj] != side:
                    exit_px, exit_t, reason = cl, ist(int(bars[j][0])), "opposite cross (TP)"
                    break
            if exit_px is None:
                exit_px, exit_t, reason = cur, cur_t, "STILL OPEN (mark)"

            pts = (ref - exit_px) if side == "sell" else (exit_px - ref)
            pct = pts / ref * 100 if ref else 0.0
            print("   {} @ {:%H:%M}  entry {:.1f}  SL {:.1f} (1.5xATR={:.1f})".format(
                side.upper(), ent_t, ref, sl, atr))
            print("      -> {} @ {:%H:%M} {:.1f}   P&L {:+.1f} pts ({:+.2f}%)".format(
                reason, exit_t, exit_px, pts, pct))

    print("\n" + "=" * 80)
    print("fresh crosses today: {}".format(fired))
    if not fired:
        print("(brain would have published nothing new today — all instruments held)")


if __name__ == "__main__":
    main()
