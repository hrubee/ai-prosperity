import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, "/root/trading-bot/crypto")
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

# 9 spikes >= 10.0x from last 24h
spikes = [
    {"base": "BEL", "ts": 1755370800000, "time_ist": "16-Aug 09:30 PM", "mult": 28.18, "pump": 11.85},
    {"base": "SOPH", "ts": 1755385200000, "time_ist": "17-Aug 01:30 AM", "mult": 23.92, "pump": 1.57},
    {"base": "MELANIA", "ts": 1755356400000, "time_ist": "16-Aug 05:30 PM", "mult": 22.51, "pump": 2.48},
    {"base": "STABLE", "ts": 1755356400000, "time_ist": "16-Aug 05:30 PM", "mult": 19.38, "pump": 2.47},
    {"base": "XPL", "ts": 1755342000000, "time_ist": "16-Aug 01:30 PM", "mult": 17.56, "pump": 1.21},
    {"base": "PORTAL", "ts": 1755356400000, "time_ist": "16-Aug 05:30 PM", "mult": 15.86, "pump": 8.79},
    {"base": "ARPA", "ts": 1755370800000, "time_ist": "16-Aug 09:30 PM", "mult": 13.44, "pump": 1.39},
    {"base": "NEWT", "ts": 1755399600000, "time_ist": "17-Aug 05:30 AM", "mult": 13.36, "pump": 2.15},
    {"base": "GIGGLE", "ts": 1755356400000, "time_ist": "16-Aug 05:30 PM", "mult": 12.81, "pump": 5.15},
]

print("="*95)
print("SIMULATION OF 10X VOLUME SPIKE SHORT ENTRIES (PAST 24 HOURS):")
print("="*95)

results = []

for s in spikes:
    base = s["base"]
    try:
        candles_4h = A.get_ohlcv(base, "4h", limit=50, include_forming=True)
        if not candles_4h or len(candles_4h) < 25:
            continue
            
        # Find index of the trigger candle
        trigger_idx = None
        for i, c in enumerate(candles_4h):
            if abs(c[0] - s["ts"]) < 3600000:
                trigger_idx = i
                break
                
        if trigger_idx is None:
            # Match by latest
            trigger_idx = len(candles_4h) - 2
            
        c_trig = candles_4h[trigger_idx]
        entry_px = c_trig[4] # Close of trigger candle
        
        # Calculate ATR(14)
        trs = []
        for i in range(max(1, trigger_idx - 14), trigger_idx + 1):
            cur = candles_4h[i]
            prev = candles_4h[i-1]
            tr = max(cur[2] - cur[3], abs(cur[2] - prev[4]), abs(cur[3] - prev[4]))
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else entry_px * 0.03
        
        sl_px = entry_px + (1.0 * atr)
        tp_px = entry_px - (2.0 * atr)
        
        sl_pct = ((sl_px - entry_px) / entry_px) * 100.0
        tp_pct = ((entry_px - tp_px) / entry_px) * 100.0
        
        # Check subsequent candles
        subsequent = candles_4h[trigger_idx + 1 :]
        outcome = "ACTIVE / FLOATING"
        exit_px = entry_px
        exit_time = "Ongoing"
        pnl_pct = 0.0
        r_mult = 0.0
        
        current_mark = subsequent[-1][4] if subsequent else entry_px
        
        for c in subsequent:
            c_high = c[2]
            c_low = c[3]
            c_close = c[4]
            c_t_ist = datetime.fromtimestamp(c[0]/1000 + 19800, tz=timezone.utc).strftime("%d-%b %I:%M %p")
            
            # Did high hit SL?
            if c_high >= sl_px:
                outcome = "🛑 STOPPED OUT"
                exit_px = sl_px
                exit_time = c_t_ist
                pnl_pct = -sl_pct
                r_mult = -1.0
                break
            # Did low hit TP?
            elif c_low <= tp_px:
                outcome = "🎯 TAKE PROFIT HIT"
                exit_px = tp_px
                exit_time = c_t_ist
                pnl_pct = tp_pct
                r_mult = 2.0
                break
                
        if outcome == "ACTIVE / FLOATING":
            # Floating PnL
            pnl_pct = ((entry_px - current_mark) / entry_px) * 100.0
            r_mult = (entry_px - current_mark) / (sl_px - entry_px)
            exit_px = current_mark
            
        results.append({
            "base": base,
            "time_ist": s["time_ist"],
            "mult": s["mult"],
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "current_px": current_mark,
            "outcome": outcome,
            "exit_px": exit_px,
            "pnl_pct": pnl_pct,
            "r_mult": r_mult
        })
    except Exception as e:
        print(f"Error evaluating {base}: {e}")

print(f"{'COIN':<9} | {'SPIKE':<8} | {'ENTRY':<10} | {'SL (+ATR)':<10} | {'TP (-2ATR)':<10} | {'OUTCOME':<18} | {'R-MULT':<8} | {'PNL %'}")
print("-" * 95)
total_r = 0.0
for r in results:
    total_r += r["r_mult"]
    print(f"#{r['base']:<8} | {r['mult']:>5.1f}x | {r['entry_px']:>10.4f} | {r['sl_px']:>10.4f} | {r['tp_px']:>10.4f} | {r['outcome']:<18} | {r['r_mult']:>+6.2f}R | {r['pnl_pct']:>+7.2f}%")

print("="*95)
wins = len([r for r in results if r["outcome"] == "🎯 TAKE PROFIT HIT" or (r["outcome"] == "ACTIVE / FLOATING" and r["r_mult"] > 0)])
losses = len([r for r in results if r["outcome"] == "🛑 STOPPED OUT" or (r["outcome"] == "ACTIVE / FLOATING" and r["r_mult"] < 0)])
print(f"TOTAL TRADES: {len(results)} | WIN/POSITIVE: {wins} | LOSS/NEGATIVE: {losses}")
print(f"NET R-MULTIPLE (at 1% risk per trade): {total_r:+.2f}R ({total_r:+.2f}% Account Growth)")
print("="*95)
