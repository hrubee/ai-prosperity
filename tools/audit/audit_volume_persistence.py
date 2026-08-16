#!/usr/bin/env python3
"""audit_volume_persistence.py — Audit volume persistence post-entry for VOL2B2T trades.
"""
import os
import sys
sys.path.append(".")
import numpy as np
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

TRADES = [
    {"sym": "JTO", "entry_ts": 1786586444000, "entry_str": "13-Aug 07:30 IST"},
    {"sym": "BARD", "entry_ts": 1786437726000, "entry_str": "11-Aug 14:12 IST"},
    {"sym": "RARE", "entry_ts": 1786537187000, "entry_str": "12-Aug 17:49 IST"},
    {"sym": "MELANIA", "entry_ts": 1786424245000, "entry_str": "11-Aug 10:27 IST"},
    {"sym": "NEO", "entry_ts": 1786518215000, "entry_str": "12-Aug 12:33 IST"}
]

def main():
    print("==========================================================================================================")
    print("📊 VOLUME PERSISTENCE AUDIT POST-ENTRY (15m CANDLES)")
    print("==========================================================================================================")

    for t_info in TRADES:
        sym = t_info["sym"]
        entry_ts = t_info["entry_ts"]
        
        try:
            rows = A.get_ohlcv(sym, "15m", limit=300)
            if not rows or len(rows) < 50:
                print(f"No klines found for {sym}")
                continue

            t_list = [r[0] for r in rows]
            o_list = [r[1] for r in rows]
            h_list = [r[2] for r in rows]
            l_list = [r[3] for r in rows]
            c_list = [r[4] for r in rows]
            v_list = [r[5] for r in rows]
            
            # Find entry index
            entry_idx = -1
            for i in range(len(t_list)):
                if t_list[i] <= entry_ts < t_list[i] + 15 * 60 * 1000:
                    entry_idx = i
                    break
            if entry_idx == -1:
                entry_idx = np.argmin([abs(ts - entry_ts) for ts in t_list])
                
            if entry_idx < 40:
                print(f"Not enough history for {sym}")
                continue

            base_vol = float(np.mean(v_list[entry_idx - 40 : entry_idx]))
            spike_vol = float(v_list[entry_idx - 1]) if entry_idx > 0 else float(v_list[entry_idx])
            entry_vol = float(v_list[entry_idx])

            print(f"\n🔹 Symbol: {sym} (Entry: {t_info['entry_str']})")
            print(f"   Baseline 40-bar Avg Volume: {base_vol:,.1f}")
            print(f"   Spike Candle Volume:        {spike_vol:,.1f} ({spike_vol / max(base_vol, 1e-9):.1f}x baseline)")
            print(f"   Entry Candle Volume:        {entry_vol:,.1f} ({entry_vol / max(base_vol, 1e-9):.1f}x baseline)")
            print("   Post-Entry Volume Persistence (Next 6 Candles / 1.5 Hours):")

            for step in range(1, 7):
                idx = entry_idx + step
                if idx < len(v_list):
                    vol_step = float(v_list[idx])
                    mult = vol_step / max(base_vol, 1e-9)
                    c_color = "🟢 GREEN" if c_list[idx] >= o_list[idx] else "🔴 RED"
                    px_change = ((c_list[idx] - o_list[idx]) / o_list[idx]) * 100
                    print(f"      +{(step*15)}m (Candle {step}): Vol = {vol_step:,.1f} ({mult:.1f}x base) | {c_color} ({px_change:+.2f}%)")

        except Exception as e:
            print(f"Error auditing {sym}: {e}")

if __name__ == "__main__":
    main()
