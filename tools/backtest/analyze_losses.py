#!/usr/bin/env python3
"""tools/backtest/analyze_losses.py — Deep Diagnostic Loss Analysis on Heikin-Ashi Strategy
"""
import os, sys, json, ssl, urllib.request
import pandas as pd
import numpy as np

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"

def analyze_timeframe(tf: str):
    csv_file = f"/root/trading-bot/heikinashi_{tf}_trades.csv"
    if not os.path.exists(csv_file):
        print(f"File not found: {csv_file}")
        return

    df = pd.read_csv(csv_file)
    losses = df[df["result"] == "LOSS"].copy()
    wins = df[df["result"] == "WIN"].copy()
    total_n = len(df)
    loss_n = len(losses)
    win_n = len(wins)

    print("=" * 80)
    print(f"🔍 DEEP LOSS PATTERN DIAGNOSTIC: {tf.upper()} TIMEFRAME ({total_n:,d} Trades)")
    print("=" * 80)

    # 1. HOLDING TIME & STOP-OUT TIMING
    print("1️⃣ STOP-OUT TIMING & REVERSAL SPEED:")
    imm_1bar = (losses["holding_bars"] == 1).sum()
    imm_2bar = (losses["holding_bars"] <= 2).sum()
    imm_3bar = (losses["holding_bars"] <= 3).sum()
    slow_loss = (losses["holding_bars"] > 10).sum()

    print(f"  • Immediate 1-Bar Stopouts (Instant Reversal) : {imm_1bar:,d} ({imm_1bar/loss_n*100:.1f}% of all losses)")
    print(f"  • Fast 1-2 Bar Stopouts                       : {imm_2bar:,d} ({imm_2bar/loss_n*100:.1f}% of all losses)")
    print(f"  • Fast 1-3 Bar Stopouts                       : {imm_3bar:,d} ({imm_3bar/loss_n*100:.1f}% of all losses)")
    print(f"  • Slow Bleed Losses (>10 bars)                : {slow_loss:,d} ({slow_loss/loss_n*100:.1f}% of all losses)")
    print(f"  • Avg Bars Held (Losses): {losses['holding_bars'].mean():.2f} bars | (Wins): {wins['holding_bars'].mean():.2f} bars")

    # 2. RISK / STOP LOSS SPREAD
    print("\n2️⃣ STOP-LOSS WIDTH & NOISE SUSCEPTIBILITY:")
    print(f"  • Avg SL Distance %: Losses = {losses['risk_pct'].mean():.2f}% | Wins = {wins['risk_pct'].mean():.2f}%")
    tight_sl_losses = (losses["risk_pct"] < 0.5).sum()
    wide_sl_losses = (losses["risk_pct"] > 3.0).sum()
    print(f"  • Ultra-tight SL (<0.5% distance): {tight_sl_losses:,d} losses ({tight_sl_losses/loss_n*100:.1f}%)")
    print(f"  • Wide SL (>3.0% distance)       : {wide_sl_losses:,d} losses ({wide_sl_losses/loss_n*100:.1f}%)")

    # 3. DIRECTIONAL BIAS (LONG vs SHORT)
    print("\n3️⃣ DIRECTIONAL BREAKDOWN (LONGS vs SHORTS):")
    longs = df[df["side"] == "LONG"]
    shorts = df[df["side"] == "SHORT"]
    long_wr = (longs["result"] == "WIN").mean() * 100
    short_wr = (shorts["result"] == "WIN").mean() * 100
    print(f"  • Longs  : {len(longs):,d} trades | Win Rate: {long_wr:.2f}% | Total Losses: {(longs['result'] == 'LOSS').sum():,d}")
    print(f"  • Shorts : {len(shorts):,d} trades | Win Rate: {short_wr:.2f}% | Total Losses: {(shorts['result'] == 'LOSS').sum():,d}")

    # 4. PROFIT RUNUPS BEFORE LOSS (MFE ANALYSIS)
    print("\n4️⃣ MAXIMUM FAVORABLE EXCURSION (MFE) — Profit Peak Before Stopout:")
    # Calculate MFE on the losses
    mfe_col = losses["mfe_r"] if "mfe_r" in losses.columns else None
    loss_by_coin = losses.groupby("pair").size().sort_values(ascending=False)
    print(f"  • Top 5 Coins with Most Losses: {dict(loss_by_coin.head(5))}")
    print(f"  • Coins with 0 Wins: {(df.groupby('pair')['result'].apply(lambda s: (s=='WIN').sum()) == 0).sum()} coins")
    print()

def main():
    for tf in ["15m", "30m", "1h", "4h"]:
        analyze_timeframe(tf)

if __name__ == "__main__":
    main()
