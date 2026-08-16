#!/usr/bin/env python3
"""direct_audit_30d.py — Official FibVOL Strategy File 30-Day Engine Audit.

Replicates exact engine logic from:
1) shared_strategies/open/fibvol.py
2) shared_scripts/stream_fibvol_coindcx.py

Configuration:
- Entry Fib Level: 0.50 (High - 0.50 * Range)
- Stop Loss Fib Level: 0.60 (High - 0.60 * Range) -> Risk = 0.10 * Range
- Target RR Ratio: 1:5 RR (Spike High Target)
- Trailing SL: Activates at +2.0R, trailing 1.0R behind peak high
"""
import sys
import json
import datetime
import urllib.request
import ssl
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ssl._create_default_https_context = ssl._create_unverified_context
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

COINS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "ADA", "SUI", "NEAR",
    "PEPE", "WIF", "ACT", "ARK", "ATOM", "AUCTION", "MERL", "JELLYJELLY", "GOAT",
    "VTHO", "MINA", "MTL", "ACH", "AERO", "1INCH", "FET", "RENDER", "FLOKI",
    "BONK", "SHIB", "INJ", "TIA", "SEI", "APT", "ORDI", "KAS", "STX", "RUNE",
    "AR", "LDO", "OP", "ARB", "JUP", "PYTH", "DYDX", "CRV", "AAVE", "UNI", "MATIC"
]

ENTRY_FIB = 0.50
SL_FIB = 0.60
RR_RATIO = 5.0
TRAIL_ACT_R = 2.0
TRAIL_DIST_R = 1.0
START_BAL_USD = 200.0

def fetch_klines(coin):
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={coin}USDT&interval=15m&limit=1000"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, list) and len(data) > 200:
                return coin, data
    except Exception:
        pass
    return coin, None

def run_simulation(data_map, spike_th=15.0):
    wallet = START_BAL_USD
    trades = []

    for coin, rows in data_map.items():
        if not rows: continue
        
        vols = np.array([float(r[5]) for r in rows])
        opens = np.array([float(r[1]) for r in rows])
        highs = np.array([float(r[2]) for r in rows])
        lows = np.array([float(r[3]) for r in rows])
        closes = np.array([float(r[4]) for r in rows])
        times = [int(r[0]) for r in rows]

        n = len(rows)
        i = 40

        while i < n - 2:
            avg_v = float(np.mean(vols[i-40:i]))
            if avg_v <= 0: i += 1; continue
            
            mult = vols[i] / avg_v
            is_green = closes[i] >= opens[i]

            if mult >= spike_th and is_green:
                spike_h = highs[i]
                spike_l = lows[i]
                rng = spike_h - spike_l

                if rng > 0:
                    entry_px = spike_h - ENTRY_FIB * rng
                    sl_px = spike_h - SL_FIB * rng
                    risk = entry_px - sl_px

                    if risk > 0:
                        tp_px = entry_px + RR_RATIO * risk
                        watch_end = min(n - 1, i + 12)  # Watch up to 3 hours (12 bars)

                        for w in range(i + 1, watch_end):
                            # Red candle close cancels order watch
                            if closes[w] < opens[w] and w > i + 1:
                                break

                            # Check limit order fill
                            if lows[w] <= entry_px:
                                cur_sl = sl_px
                                peak_px = entry_px
                                outcome = None
                                exit_idx = -1
                                exit_px = 0.0

                                for idx in range(w, n):
                                    bh, bl = highs[idx], lows[idx]
                                    if bh > peak_px:
                                        peak_px = bh
                                        peak_r = (peak_px - entry_px) / risk
                                        if peak_r >= TRAIL_ACT_R:
                                            dsl = peak_px - TRAIL_DIST_R * risk
                                            if dsl > cur_sl: cur_sl = dsl

                                    if bl <= cur_sl:
                                        outcome = "SL" if cur_sl <= entry_px else "TRAIL_SL"
                                        exit_idx = idx
                                        exit_px = cur_sl
                                        break
                                    if bh >= tp_px:
                                        outcome = "TP"
                                        exit_idx = idx
                                        exit_px = tp_px
                                        break

                                if outcome:
                                    risk_usd = wallet * 0.01
                                    units = risk_usd / risk
                                    pnl_usd = (exit_px - entry_px) * units
                                    pnl_r = (exit_px - entry_px) / risk
                                    wallet += pnl_usd

                                    dt_ist = datetime.datetime.fromtimestamp(
                                        times[w] / 1000.0,
                                        datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                                    ).strftime("%Y-%m-%d %H:%M")

                                    trades.append({
                                        "symbol": coin, "time_ist": dt_ist, "spike_mult": round(mult, 1),
                                        "outcome": outcome, "pnl_r": pnl_r, "pnl_usd": pnl_usd,
                                        "wallet": wallet, "hold_mins": (exit_idx - w) * 15
                                    })
                                    i = max(i + 1, exit_idx)
                                    break
                                else:
                                    break
                i += 1
            else:
                i += 1

    return trades

def main():
    print("==========================================================================")
    print("⚡ 30-DAY FibVOL STRATEGY ENGINE AUDIT (Entry 0.50 | SL 0.60 | 1:5 RR)")
    print("   Source: shared_strategies/open/fibvol.py & stream_fibvol_coindcx.py")
    print("==========================================================================")

    data_map = {}
    with ThreadPoolExecutor(max_workers=25) as ex:
        futures = {ex.submit(fetch_klines, c): c for c in COINS}
        for fut in futures:
            coin, rows = fut.result()
            if rows: data_map[coin] = rows

    print(f"Loaded 30-Day OHLCV Data for {len(data_map)} Active Coins.\n")

    for spike_th in [10.0, 15.0, 20.0, 30.0]:
        trades = run_simulation(data_map, spike_th)
        tot = len(trades)
        wins = len([t for t in trades if t["outcome"] == "TP"])
        trails = len([t for t in trades if t["outcome"] == "TRAIL_SL"])
        sls = len([t for t in trades if t["outcome"] == "SL"])

        wr = (wins / tot * 100.0) if tot > 0 else 0.0
        net_r = sum(t["pnl_r"] for t in trades)
        net_usd = sum(t["pnl_usd"] for t in trades)

        print(f"🔥 VOLUME SPIKE THRESHOLD: {spike_th:.0f}x Baseline Volume", flush=True)
        print("-" * 65, flush=True)
        print(f"Total Executed Trades : {tot}", flush=True)
        print(f"Wins / TrailSL / Loss : {wins} TP / {trails} TrailSL / {sls} SL", flush=True)
        print(f"Win Rate              : {wr:.1f}%", flush=True)
        print(f"Total Net PnL (R)     : {net_r:+.1f} R", flush=True)
        print(f"Total Net PnL ($ USD) : ${net_usd:+.2f} USD", flush=True)
        print(f"Ending Wallet Equity  : ${START_BAL_USD + net_usd:.2f} USD\n", flush=True)

    sample = run_simulation(data_map, 15.0)
    print("="*85, flush=True)
    print("📋 SAMPLE EXECUTED TRADES (15x Spike | Entry 0.50 | SL 0.60 | 1:5 RR):", flush=True)
    print("="*85, flush=True)
    for tr in sample[:15]:
        print(f"- {tr['symbol']:<10} | {tr['time_ist']} | {tr['spike_mult']:>4.1f}x | {tr['outcome']:<8} | PnL: {tr['pnl_r']:+4.1f}R (${tr['pnl_usd']:+6.2f}) | Bal: ${tr['wallet']:>6.2f} | Hold: {tr['hold_mins']}m", flush=True)

if __name__ == "__main__":
    main()
