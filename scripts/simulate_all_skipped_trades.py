import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, "/root/trading-bot/crypto")
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

# All green >= 10.0x volume spike signals from the past 30 hours
candidate_events = [
    {"base": "XPL",     "ts_str": "16-Aug 01:30 PM IST", "mult": 17.56, "pump": 1.21},
    {"base": "PORTAL",  "ts_str": "16-Aug 05:30 PM IST", "mult": 15.86, "pump": 8.79},
    {"base": "STABLE",  "ts_str": "16-Aug 05:30 PM IST", "mult": 19.38, "pump": 2.47},
    {"base": "GIGGLE",  "ts_str": "16-Aug 05:30 PM IST", "mult": 12.81, "pump": 5.15},
    {"base": "MELANIA", "ts_str": "16-Aug 05:30 PM IST", "mult": 22.51, "pump": 2.48},
    {"base": "BEL",     "ts_str": "16-Aug 09:30 PM IST", "mult": 28.18, "pump": 11.85},
    {"base": "ARPA",    "ts_str": "16-Aug 09:30 PM IST", "mult": 13.44, "pump": 1.39},
    {"base": "SOPH",    "ts_str": "17-Aug 01:30 AM IST", "mult": 23.92, "pump": 1.57},
    {"base": "NEWT",    "ts_str": "17-Aug 05:30 AM IST", "mult": 13.36, "pump": 2.15},
    {"base": "GPS",     "ts_str": "17-Aug 01:30 PM IST", "mult": 13.11, "pump": 9.81},
    {"base": "MERL",    "ts_str": "17-Aug 05:30 PM IST", "mult": 14.40, "pump": 1.13},
]

print("="*110)
print("COMPREHENSIVE OUTCOME SIMULATION FOR ALL DETECTED 10X VOLUME SPIKES:")
print("="*110)

results = []

for ev in candidate_events:
    base = ev["base"]
    try:
        candles_4h = A.get_ohlcv(base, "4h", limit=40, include_forming=True)
        if not candles_4h or len(candles_4h) < 22:
            continue
            
        # Find the trigger candle matching the timestamp
        # Match by date/time
        trigger_idx = None
        for i, c in enumerate(candles_4h):
            t_ist = datetime.fromtimestamp(c[0]/1000 + 19800, tz=timezone.utc).strftime("%d-%b %I:%M %p IST")
            if ev["ts_str"] in t_ist:
                trigger_idx = i
                break
                
        if trigger_idx is None:
            # Fallback based on relative index from end
            if "05:30 PM" in ev["ts_str"] and "17-Aug" in ev["ts_str"]:
                trigger_idx = len(candles_4h) - 2
            elif "01:30 PM" in ev["ts_str"] and "17-Aug" in ev["ts_str"]:
                trigger_idx = len(candles_4h) - 3
            elif "05:30 AM" in ev["ts_str"] and "17-Aug" in ev["ts_str"]:
                trigger_idx = len(candles_4h) - 5
            else:
                trigger_idx = len(candles_4h) - 6
                
        c_trig = candles_4h[trigger_idx]
        entry_px = c_trig[4] # 4H close
        
        # Calculate ATR(14)
        trs = []
        for k in range(max(1, trigger_idx - 13), trigger_idx + 1):
            cur = candles_4h[k]
            prev = candles_4h[k-1]
            tr = max(cur[2] - cur[3], abs(cur[2] - prev[4]), abs(cur[3] - prev[4]))
            trs.append(tr)
        atr14 = float(sum(trs) / len(trs)) if trs else entry_px * 0.03
        
        sl_px = entry_px + (1.0 * atr14)
        tp_px = entry_px - (2.0 * atr14)
        
        sl_pct = ((sl_px - entry_px) / entry_px) * 100.0
        tp_pct = ((entry_px - tp_px) / entry_px) * 100.0
        
        # Check all subsequent candles up to current
        subsequent = candles_4h[trigger_idx + 1 :]
        outcome = "FLOATING / ACTIVE"
        exit_px = entry_px
        exit_time = "Ongoing"
        r_mult = 0.0
        pnl_pct = 0.0
        
        current_mark = subsequent[-1][4] if subsequent else entry_px
        lowest_price = min([c[3] for c in subsequent]) if subsequent else entry_px
        highest_price = max([c[2] for c in subsequent]) if subsequent else entry_px
        
        max_dump_pct = ((entry_px - lowest_price) / entry_px) * 100.0
        max_adverse_pct = ((highest_price - entry_px) / entry_px) * 100.0
        
        for c in subsequent:
            c_high = c[2]
            c_low = c[3]
            c_close = c[4]
            c_t_ist = datetime.fromtimestamp(c[0]/1000 + 19800, tz=timezone.utc).strftime("%d-%b %I:%M %p IST")
            
            if c_low <= tp_px:
                outcome = "🎯 TAKE PROFIT HIT"
                exit_px = tp_px
                exit_time = c_t_ist
                r_mult = +2.0
                pnl_pct = tp_pct
                break
            elif c_high >= sl_px:
                outcome = "🛑 STOP LOSS HIT"
                exit_px = sl_px
                exit_time = c_t_ist
                r_mult = -1.0
                pnl_pct = -sl_pct
                break
                
        if outcome == "FLOATING / ACTIVE":
            exit_px = current_mark
            pnl_pct = ((entry_px - current_mark) / entry_px) * 100.0
            r_mult = (entry_px - current_mark) / (sl_px - entry_px)
            
        results.append({
            "base": base,
            "signal_time": ev["ts_str"],
            "mult": ev["mult"],
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "current_px": current_mark,
            "outcome": outcome,
            "exit_px": exit_px,
            "exit_time": exit_time,
            "r_mult": r_mult,
            "pnl_pct": pnl_pct,
            "max_dump_pct": max_dump_pct
        })
    except Exception as e:
        print(f"Error {base}: {e}")

print(f"{'COIN':<8} | {'SIGNAL TIME':<18} | {'SPIKE':<6} | {'ENTRY':<10} | {'SL':<10} | {'TP (1:2)':<10} | {'OUTCOME':<18} | {'R-MULT':<8} | {'PNL %':<8} | {'MAX DUMP'}")
print("-" * 110)
total_r = 0.0
for r in results:
    total_r += r["r_mult"]
    print(f"#{r['base']:<7} | {r['signal_time']:<18} | {r['mult']:>4.1f}x | {r['entry_px']:>10.4f} | {r['sl_px']:>10.4f} | {r['tp_px']:>10.4f} | {r['outcome']:<18} | {r['r_mult']:>+6.2f}R | {r['pnl_pct']:>+6.2f}% | {r['max_dump_pct']:>+6.2f}%")

print("="*110)
tp_hits = len([r for r in results if r["outcome"] == "🎯 TAKE PROFIT HIT"])
sl_hits = len([r for r in results if r["outcome"] == "🛑 STOP LOSS HIT"])
floating_green = len([r for r in results if r["outcome"] == "FLOATING / ACTIVE" and r["r_mult"] > 0])
floating_red = len([r for r in results if r["outcome"] == "FLOATING / ACTIVE" and r["r_mult"] < 0])

print(f"TOTAL SETUPS: {len(results)}")
print(f"• Take Profit Hit (Closed at +2.0R): {tp_hits}")
print(f"• Floating in Profit:                {floating_green}")
print(f"• Floating in Small Drawdown:        {floating_red}")
print(f"• Stop Loss Hit (Stopped Out):       {sl_hits}")
print(f"• OVERALL NET RETURN:                {total_r:+.2f}R ({total_r:+.2f}% Account Growth at 1% risk per trade)")
print("="*110)
