#!/usr/bin/env python3
"""check_acu_ttm_status.py — Calculate TTM Squeeze state for ACU on CoinDCX.
"""
import sys
import numpy as np
import datetime
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

def main():
    sym = "ACU"
    print(f"Checking TTM Squeeze status for {sym}...")
    
    rows = A.get_ohlcv(sym, "15m", limit=100)
    if not rows or len(rows) < 30:
        print("Not enough candles:", len(rows) if rows else 0)
        return

    c_list = np.array([float(r[4]) for r in rows])
    h_list = np.array([float(r[2]) for r in rows])
    l_list = np.array([float(r[3]) for r in rows])
    v_list = np.array([float(r[5]) for r in rows])
    t_list = [r[0] for r in rows]

    print("\nRecent 15m Candles for ACU:")
    for idx in range(-10, 0):
        c_val = c_list[idx]
        v_val = v_list[idx]
        ts_ms = t_list[idx]
        dt_ist = datetime.datetime.fromtimestamp(ts_ms/1000.0, datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        
        # Calculate Bollinger Bands (20, 2.0)
        sub_c = c_list[idx-20:idx]
        b_mean = np.mean(sub_c)
        b_std = np.std(sub_c)
        upper_bb = b_mean + 2.0 * b_std
        lower_bb = b_mean - 2.0 * b_std

        # Calculate Keltner Channels (20, 1.5)
        sub_h = h_list[idx-20:idx]
        sub_l = l_list[idx-20:idx]
        sub_atr = np.mean(sub_h - sub_l)
        upper_kc = b_mean + 1.5 * sub_atr
        lower_kc = b_mean - 1.5 * sub_atr

        in_squeeze = (lower_bb > lower_kc) and (upper_bb < upper_kc)
        base_vol = np.mean(v_list[idx-40:idx]) if abs(idx)+40 <= len(v_list) else np.mean(v_list[:40])
        v_mult = v_val / max(base_vol, 1e-9)

        print(f"  {dt_ist.strftime('%d-%b %H:%M IST')} | Close: {c_val:.6g} | Vol: {v_val:,.0f} ({v_mult:.1f}x) | Squeeze: {'ON 🔒' if in_squeeze else 'OFF (Fired/No Squeeze) ⚪'}")

if __name__ == "__main__":
    main()
