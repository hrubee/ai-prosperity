#!/usr/bin/env python3
"""2b2t Indian-equity 4H LONG-ONLY scan (SHADOW — detects + reports, places NO orders).

DATA: Tradejini CubePlus REST (Securities group + /api/mkt-data/chart/interval-data 30m), aggregated
30m -> 4h (Tradejini has no daily/4h interval; only 1/5/15/30m). UNIVERSE: NIFTY 200 (nifty200.py).
SIGNAL: 2b2t long — last LOOKBACK 4h candles red + the just-closed 4h candle sweeps the prior 4h low
-> entry = prior low (buy-stop), SL = signal-candle low, TP = 1:RR. Alerts to @tradeejini_bot.

CAVEAT: Indian equity trades 6.25h/day, so 4h bars are built by grouping 8 consecutive 30m bars
CONTINUOUSLY (they can span the overnight gap) — an approximation of a true 4h clock bar.
No trading token used; would-execute venue = Tradejini (cash equity, CNC).
"""
import os
import sys
import time
import json
import urllib.request
import urllib.parse
import datetime as dt

sys.path.insert(0, "/root/aiprosperity/backend")
from app import tradejini as tj
from app import nifty200

LOOKBACK = int(os.environ.get("B2TJ_LOOKBACK", "4"))      # 4-candle trend (operator's config)
RR = float(os.environ.get("B2TJ_RR", "4.0"))
MINR = float(os.environ.get("B2TJ_MIN_RISK", "0.005"))    # 0.5% min stop (fee/slip cliff guard)
MAXR = float(os.environ.get("B2TJ_MAX_RISK", "0.10"))     # 10% max stop
CAPITAL = float(os.environ.get("B2TJ_CAPITAL", "100000"))
RISK_FRAC = float(os.environ.get("B2TJ_RISK_FRAC", "0.01"))
CAP = int(os.environ.get("B2TJ_MAX_SYMBOLS", "200"))
AGG = int(os.environ.get("B2TJ_AGG_30M", "8"))            # 8 x 30m = 4h
DAYS = int(os.environ.get("B2TJ_DAYS", "30"))
TG_TOKEN = os.environ.get("B2TJ_TG_TOKEN", "")
TG_CHAT = os.environ.get("B2TJ_TG_CHAT", "")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def agg_4h(bars):
    """30m bars [[ts_ms,o,h,l,c,v],...] -> 4h [[ts,o,h,l,c],...] (drop the trailing partial group)."""
    out = []
    n = len(bars) - (len(bars) % AGG)
    for i in range(0, n, AGG):
        g = bars[i:i + AGG]
        out.append([g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4]])
    return out


def long_signal(o, h, l, c, j):
    """2b2t long on bar j (the just-closed signal candle)."""
    if j < LOOKBACK + 1:
        return None
    if not all(c[k] < o[k] for k in range(j - LOOKBACK, j)):   # LOOKBACK red candles before signal
        return None
    if not (l[j] < l[j - 1]):                                  # signal candle sweeps the prior low
        return None
    entry, sl = l[j - 1], l[j]
    if entry <= sl:
        return None
    stop = (entry - sl) / entry
    if stop < MINR or stop > MAXR:
        return None
    return entry, sl, entry + RR * (entry - sl), stop


def main():
    key, tok = tj.individual_data_token()
    c = tj.TradejiniClient(tok, api_key=key)
    syms = nifty200.fetch_symbols()[:CAP]
    print("universe: %d NIFTY200 stocks (Tradejini 30m -> 4h)" % len(syms))
    now = int(time.time()); frm = now - DAYS * 86400
    setups = []; scanned = errs = 0
    for sym in syms:
        try:
            sid = "EQT_%s_EQ_NSE" % sym
            j = c._get("/api/mkt-data/chart/interval-data?id=%s&interval=30&from=%d&to=%d" % (sid, frm, now))
            bars = (j.get("d") or {}).get("bars") or []
            if len(bars) < (LOOKBACK + 2) * AGG:
                continue
            f = agg_4h(bars)
            if len(f) < LOOKBACK + 2:
                continue
            o = [x[1] for x in f]; h = [x[2] for x in f]; l = [x[3] for x in f]; cl = [x[4] for x in f]
            sig = long_signal(o, h, l, cl, len(cl) - 1)
            scanned += 1
            if sig:
                entry, sl, tp, stop = sig
                qty = int((RISK_FRAC * CAPITAL) / (entry - sl)) if entry > sl else 0
                setups.append((sym, entry, sl, tp, stop, qty))
            time.sleep(0.08)
        except Exception:
            errs += 1
    setups.sort(key=lambda s: -s[4])
    print("scanned=%d errors=%d setups=%d" % (scanned, errs, len(setups)))
    today = dt.datetime.now(IST).strftime("%d %b %Y %H:%M")
    lines = ["🟢 [2b2t · INDIA · 4H] LONG setups — %s IST" % today,
             "(SHADOW — no orders; data=Tradejini 30m→4h, would-trade=Tradejini cash)", ""]
    if not setups:
        lines.append("No 2b2t 4h long setups right now (NIFTY200).")
    for sym, e, s, tp, stop, qty in setups[:20]:
        lines.append("• %-12s entry %.2f · SL %.2f · TP %.2f · risk %.1f%% · ~%d sh" % (sym, e, s, tp, stop * 100, qty))
    if len(setups) > 20:
        lines.append("… +%d more" % (len(setups) - 20))
    msg = "\n".join(lines)
    print("----\n" + msg)
    if TG_TOKEN and TG_CHAT:
        body = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg}).encode()
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN, data=body), timeout=20))
            print("TG sent ok=", r.get("ok"))
        except Exception as e:
            print("TG err", e)


if __name__ == "__main__":
    main()
