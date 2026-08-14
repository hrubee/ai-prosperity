#!/usr/bin/env python3
"""fibvol.py — 30X Volume Spike Fibonacci Strategy Core Indicator/Signal Module.

Core Logic:
1. Identifies 15m candles with >= 30x Volume Spike over 40-candle SMA volume.
2. Ensures the spike candle closes GREEN (close >= open).
3. Computes Fibonacci levels:
   - Entry: Golden 0.618 Fib level (High - 0.618 * (High - Low))
   - Stop Loss: 0.786 Fib level (High - 0.786 * (High - Low))
   - Take Profit: 1:4 Risk-Reward Target
"""
import numpy as np
import pandas as pd

def fibvol_core(df: pd.DataFrame, spike_vol: float = 30.0, entry_fib: float = 0.618, sl_fib: float = 0.786, rr_ratio: float = 4.0) -> pd.DataFrame:
    """Calculates FIBVOL signals and Fibonacci levels on OHLCV dataframe.
    
    Expects df with columns: ['open', 'high', 'low', 'close', 'volume']
    """
    df = df.copy()
    
    # Calculate 40-candle baseline volume SMA
    df["vol_sma40"] = df["volume"].rolling(window=40).mean()
    df["vol_mult"] = df["volume"] / df["vol_sma40"]
    df["is_green"] = df["close"] >= df["open"]
    
    # Spike signal
    df["spike_signal"] = df["is_green"] & (df["vol_mult"] >= spike_vol)
    
    # Calculate Fibonacci levels on green spike candles
    highs = df["high"].values
    lows = df["low"].values
    rng = highs - lows
    
    df["fib_entry"] = highs - (entry_fib * rng)
    df["fib_sl"] = highs - (sl_fib * rng)
    risk = df["fib_entry"] - df["fib_sl"]
    df["fib_tp"] = df["fib_entry"] + (rr_ratio * risk)
    
    return df
