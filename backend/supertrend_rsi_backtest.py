"""Backtest of the Fyers Supertrend(7,3)+RSI(14) intraday-equity strategy on the
NSE-200 universe, using Kite 5-min data. RESEARCH ONLY — places no orders.

Faithful to fyers_strategy_indicator_ohlc.py:
  - long when Supertrend bullish (close>ST) & RSI>20 & flat
  - short when Supertrend bearish (close<ST) & RSI<70 & flat
  - exit on 0.5% stop (intrabar low/high) OR Supertrend flip
  - ~Rs5000 notional/position; fills at the signal bar's close (the script acts on
    the latest 5-min candle, market order). POSITIONAL: holds until stop/flip
    (the code logic has no EOD square-off, though it declares INTRADAY product).

Charges per round-trip computed two ways (equity intraday):
  - 'pct' : brokerage = min(Rs20, 0.03% of turnover)/order  (Zerodha/discount style)
  - 'flat': brokerage = Rs20/order                          (conservative bound)
  plus STT 0.025% sell-side, exchange ~0.00297%, GST 18%, SEBI, stamp 0.003% buy.

Run on VPS:
  PYTHONPATH=/root/aiprosperity/backend .venv/bin/python supertrend_rsi_backtest.py [LIMIT] [COUNT]
"""
import sys
sys.path.insert(0, "/root/aiprosperity/backend")
import numpy as np
from app import kite_data

PERIOD, MULT, RSI_P = 7, 3, 14
CAPITAL, SL_PCT = 5000.0, 0.005

UNIVERSE = ["ABB","ACC","AUBANK","ABBOTINDIA","ADANIENSOL","ADANIENT","ADANIGREEN",
"ADANIPORTS","ADANIPOWER","ATGL","AWL","ABCAPITAL","ABFRL","ALKEM","AMBUJACEM",
"APOLLOHOSP","APOLLOTYRE","ASHOKLEY","ASIANPAINT","ASTRAL","AUROPHARMA","DMART",
"AXISBANK","BAJAJ-AUTO","BAJFINANCE","BAJAJFINSV","BAJAJHLDNG","BALKRISIND",
"BANDHANBNK","BANKBARODA","BANKINDIA","BATAINDIA","BERGEPAINT","BEL","BHARATFORG",
"BHEL","BPCL","BHARTIARTL","BIOCON","BOSCHLTD","BRITANNIA","CGPOWER","CANBK",
"CHOLAFIN","CIPLA","COALINDIA","COFORGE","COLPAL","CONCOR","COROMANDEL","CROMPTON",
"CUMMINSIND","DLF","DABUR","DALBHARAT","DEEPAKNTR","DELHIVERY","DEVYANI","DIVISLAB",
"DIXON","LALPATHLAB","DRREDDY","EICHERMOT","ESCORTS","NYKAA","FEDERALBNK","FORTIS",
"GAIL","GLAND","GODREJCP","GODREJPROP","GRASIM","FLUOROCHEM","GUJGASLTD","HCLTECH",
"HDFCAMC","HDFCBANK","HDFCLIFE","HAVELLS","HEROMOTOCO","HINDALCO","HAL","HINDPETRO",
"HINDUNILVR","HINDZINC","HONAUT","ICICIBANK","ICICIGI","ICICIPRULI","IDFCFIRSTB",
"ITC","INDIANB","INDHOTEL","IOC","IRCTC","IRFC","IGL","INDUSTOWER","INDUSINDBK",
"NAUKRI","INFY","INDIGO","IPCALAB","JSWENERGY","JSWSTEEL","JINDALSTEL","JIOFIN",
"JUBLFOOD","KOTAKBANK","L&TFH","LTTS","LICHSGFIN","LTIM","LT","LAURUSLABS","LICI",
"LUPIN","MRF","M&MFIN","M&M","MANKIND","MARICO","MARUTI","MFSL","MAXHEALTH","MSUMI",
"MPHASIS","MUTHOOTFIN","NHPC","NMDC","NTPC","NAVINFLUOR","NESTLEIND","OBEROIRLTY",
"ONGC","OIL","PAYTM","OFSS","POLICYBZR","PIIND","PAGEIND","PATANJALI","PERSISTENT",
"PETRONET","PIDILITIND","PEL","POLYCAB","POONAWALLA","PFC","POWERGRID","PRESTIGE",
"PGHH","PNB","RECLTD","RELIANCE","SBICARD","SBILIFE","SRF","MOTHERSON","SHREECEM",
"SHRIRAMFIN","SIEMENS","SONACOMS","SBIN","SAIL","SUNPHARMA","SUNTV","SYNGENE",
"TVSMOTOR","TATACHEM","TATACOMM","TCS","TATACONSUM","TATAELXSI","TATAMOTORS",
"TATAPOWER","TATASTEEL","TTML","TECHM","RAMCOCEM","TITAN","TORNTPHARM","TORNTPOWER",
"TRENT","TRIDENT","TIINDIA","UPL","ULTRACEMCO","UNIONBANK","UBL","MCDOWELL-N","VBL",
"VEDL","IDEA","VOLTAS","WHIRLPOOL","WIPRO","YESBANK","ZEEL","ZOMATO","ZYDUSLIFE"]


def supertrend(h, l, c, period=PERIOD, mult=MULT):
    n = len(c)
    tr = np.empty(n); tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    atr = np.full(n, np.nan)
    if n <= period: return np.full(n, np.nan)
    atr[period-1] = tr[1:period+1].mean()
    a = 1.0/8.0  # ewm(com=7) ~ alpha 0.125 (Wilder-ish)
    for i in range(period, n):
        atr[i] = (1-a)*atr[i-1] + a*tr[i]
    hl2 = (h+l)/2.0
    bu = hl2 + mult*atr; bl = hl2 - mult*atr
    fu = np.full(n, np.nan); fl = np.full(n, np.nan); st = np.full(n, np.nan)
    fu[period] = bu[period]; fl[period] = bl[period]
    for i in range(period+1, n):
        fu[i] = bu[i] if (bu[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = bl[i] if (bl[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
    st[period] = fu[period] if c[period] <= fu[period] else fl[period]
    for i in range(period+1, n):
        if st[i-1] == fu[i-1]:
            st[i] = fl[i] if c[i] > fu[i] else fu[i]
        else:
            st[i] = fu[i] if c[i] < fl[i] else fl[i]
    return st


def rsi_calc(c, period=RSI_P):
    n = len(c)
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    r = np.full(n, np.nan)
    cg = np.cumsum(gain); cl = np.cumsum(loss)
    for i in range(n):
        lo = max(0, i-period+1); cnt = i-lo+1
        ag = (cg[i] - (cg[lo-1] if lo > 0 else 0))/cnt
        al = (cl[i] - (cl[lo-1] if lo > 0 else 0))/cnt
        r[i] = 100.0 if al == 0 else 100 - 100/(1+ag/al)
    return r


def charges(buy_to, sell_to, mode):
    if mode == "flat":
        brk = 20.0 + 20.0
    else:
        brk = min(20.0, 0.0003*buy_to) + min(20.0, 0.0003*sell_to)
    stt = 0.00025*sell_to
    exch = 0.0000297*(buy_to+sell_to)
    sebi = 0.000001*(buy_to+sell_to)
    stamp = 0.00003*buy_to
    gst = 0.18*(brk+exch+sebi)
    return brk+stt+exch+sebi+stamp+gst


def backtest(h, l, c, st, r):
    trades = []; pos = 0; entry = 0.0; sl = 0.0; qty = 0
    for i in range(PERIOD+1, len(c)):
        if np.isnan(st[i]) or np.isnan(r[i]): continue
        px = c[i]
        if pos == 1 and (l[i] < sl or st[i] > px):
            trades.append((entry, px, qty, "long")); pos = 0
        elif pos == -1 and (h[i] > sl or st[i] < px):
            trades.append((entry, px, qty, "short")); pos = 0
        if pos == 0:
            if st[i] < px and r[i] > 20:
                pos = 1; entry = px; qty = max(1, int(CAPITAL/px)); sl = px*(1-SL_PCT)
            elif st[i] > px and r[i] < 70:
                pos = -1; entry = px; qty = max(1, int(CAPITAL/px)); sl = px*(1+SL_PCT)
    if pos == 1: trades.append((entry, c[-1], qty, "long"))
    elif pos == -1: trades.append((entry, c[-1], qty, "short"))
    return trades


def main():
    LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else len(UNIVERSE)
    COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 3800   # ~50 trading days
    nse = kite_data.instruments("NSE")
    sym2tok = {r["tradingsymbol"]: r["instrument_token"] for r in nse
               if r.get("instrument_type") == "EQ" and r.get("segment") == "NSE"}
    uni = UNIVERSE[:LIMIT]
    n_ok = n_fail = n_notok = 0
    tot_g = 0.0; tot_ch = {"pct": 0.0, "flat": 0.0}
    n_trades = 0; n_win = 0; per_stock = []
    rng = [None, None]
    for sym in uni:
        tok = sym2tok.get(sym)
        if not tok:
            n_notok += 1; continue
        try:
            bars = kite_data.bars_for_token(tok, 5, COUNT)
        except Exception:
            n_fail += 1; continue
        if len(bars) < PERIOD+40: continue
        arr = np.array(bars, dtype=float)
        rng[0] = arr[0, 0] if rng[0] is None else min(rng[0], arr[0, 0])
        rng[1] = arr[-1, 0] if rng[1] is None else max(rng[1], arr[-1, 0])
        h, l, c = arr[:, 2], arr[:, 3], arr[:, 4]
        st = supertrend(h, l, c); r = rsi_calc(c)
        trades = backtest(h, l, c, st, r)
        g = 0.0; chp = 0.0; chf = 0.0
        for (e, x, q, side) in trades:
            pnl = (x-e)*q if side == "long" else (e-x)*q
            bto = e*q if side == "long" else x*q
            sto = x*q if side == "long" else e*q
            g += pnl; chp += charges(bto, sto, "pct"); chf += charges(bto, sto, "flat")
            n_trades += 1; n_win += 1 if pnl > 0 else 0
        tot_g += g; tot_ch["pct"] += chp; tot_ch["flat"] += chf
        per_stock.append((sym, len(trades), g, g-chp, g-chf))
        n_ok += 1
    import datetime
    d = lambda ms: datetime.datetime.utcfromtimestamp(ms/1000).strftime("%Y-%m-%d")
    print("="*72)
    print("SUPERTREND(7,3)+RSI(14) — Fyers strategy backtest on NSE-200 (Kite 5-min)")
    print(f"stocks: {n_ok} tested, {n_notok} not-resolvable (renamed/delisted), {n_fail} fetch-fail")
    print(f"window: {d(rng[0])} -> {d(rng[1])}  (~{COUNT//75} trading days/stock)")
    print(f"total trades: {n_trades}  | win-rate(per trade): {100*n_win/max(1,n_trades):.0f}%"
          f"  | avg trades/stock: {n_trades/max(1,n_ok):.0f}")
    print("-"*72)
    print(f"GROSS P&L (all stocks, Rs5000/pos): Rs {tot_g:+,.0f}   ({tot_g/max(1,n_trades):+.1f}/trade)")
    for m in ("pct", "flat"):
        net = tot_g - tot_ch[m]
        lbl = "min(20,0.03%)" if m == "pct" else "flat Rs20/order"
        print(f"NET after charges [{lbl:14}]: Rs {net:+,.0f}"
              f"   (charges Rs {tot_ch[m]:,.0f} = {100*tot_ch[m]/max(1,abs(tot_g)) if tot_g else 0:.0f}% of gross,"
              f"  Rs{tot_ch[m]/max(1,n_trades):.1f}/trade)")
    pos_p = sum(1 for s in per_stock if s[3] > 0)
    print("-"*72)
    print(f"stocks net-positive (pct charges): {pos_p}/{n_ok} ({100*pos_p/max(1,n_ok):.0f}%)")
    per_stock.sort(key=lambda x: x[3], reverse=True)
    print("TOP 6 (net, pct):    " + " | ".join(f"{s[0]} {s[3]:+,.0f}" for s in per_stock[:6]))
    print("BOTTOM 6 (net, pct): " + " | ".join(f"{s[0]} {s[3]:+,.0f}" for s in per_stock[-6:]))
    print("="*72)
    print("NOTE: in-sample-free but single ~2.5-month window; fills at signal-bar close")
    print("      (no entry slippage modeled); positional (no intraday EOD square-off).")


if __name__ == "__main__":
    main()
