import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../platforms/coindcx")))
from adapter import CoinDCXExchangeAdapter
import pandas as pd
import numpy as np

adapter = CoinDCXExchangeAdapter()

def analyze_crypto_heikin_ashi(coin="BTC", interval="4h", limit=15):
    klines = adapter.get_ohlcv(coin, interval=interval, limit=limit, include_forming=True)
    if not klines:
        print(f"No data for {coin}")
        return

    df = pd.DataFrame(klines, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    
    # Heikin-Ashi calculation
    df["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0

    ha_open = np.zeros(len(df))
    ha_open[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2.0
    ha_close_vals = df["ha_close"].values
    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i-1] + ha_close_vals[i-1]) / 2.0
    df["ha_open"] = ha_open
    df["ha_high"] = df[["high", "ha_open", "ha_close"]].max(axis=1)
    df["ha_low"] = df[["low", "ha_open", "ha_close"]].min(axis=1)
    df["ha_color"] = np.where(df["ha_close"] >= df["ha_open"], "🟢 Green (Bullish)", "🔴 Red (Bearish)")
    
    # Flat Bottom (Uptrend) / Flat Top (Downtrend)
    df["shaved_bottom"] = (df["ha_close"] >= df["ha_open"]) & np.isclose(df["ha_low"], df["ha_open"], rtol=0.001)
    df["shaved_top"] = (df["ha_close"] < df["ha_open"]) & np.isclose(df["ha_high"], df["ha_open"], rtol=0.001)

    print(f"================================================================")
    print(f"📊 LIVE COINDCX {coin}/USDT — {interval.upper()} HEIKIN-ASHI ANALYSIS")
    print(f"================================================================")
    for _, r in df.tail(8).iterrows():
        tag = ""
        if r["shaved_bottom"]:
            tag = " 🚀 [Flat Bottom — Strong Momentum]"
        elif r["shaved_top"]:
            tag = " 🔻 [Flat Top — Strong Momentum]"
        
        t_str = r["time"].strftime("%d %b %H:%M UTC")
        print(f"{t_str} | Normal: O=${r['open']:,.2f} C=${r['close']:,.2f} | HA: O=${r['ha_open']:,.2f} C=${r['ha_close']:,.2f} | {r['ha_color']}{tag}")
    print()

if __name__ == "__main__":
    analyze_crypto_heikin_ashi("BTC", "4h", 12)
    analyze_crypto_heikin_ashi("ETH", "4h", 12)
    analyze_crypto_heikin_ashi("SOL", "1h", 12)
