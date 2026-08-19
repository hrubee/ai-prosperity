#!/usr/bin/env python3
"""tools/backtest/backtest_btc_1m_render.py — Backtest and Render 1m Heikin-Ashi Trades on BTC (24h Dataset).

Side-by-side visualization:
- Left: Normal Candlestick Chart (with Entry, SL, TP, and Trajectory)
- Right: Heikin-Ashi Candlestick Chart (with Flat-Wick Trigger highlight)
"""
import os
import sys
import json
import time
import math
import ssl
import urllib.request
import datetime
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Output directory for rendered trade images
WORKSPACE_DIR = "/Users/hrushi/Desktop/ai-prosperity"
OUT_DIR = os.path.join(WORKSPACE_DIR, "artifacts/rendered_btc_1m_trades")
ARTIFACT_DIR = "/Users/hrushi/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f"
os.makedirs(OUT_DIR, exist_ok=True)

def fetch_btc_1m_24h():
    """Fetch 1,440 1m candles (24 hours) for BTCUSDT from Binance Futures API."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1440"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        raw = json.loads(r.read().decode())
        
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = df["open_time"].astype(int)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df

def calculate_heikin_ashi(df):
    """Compute Heikin-Ashi OHLC series."""
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    ha_open = np.zeros(len(df))
    ha_open[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2.0
    
    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0
        
    ha_high = np.maximum.reduce([df["high"].values, ha_open, ha_close.values])
    ha_low = np.minimum.reduce([df["low"].values, ha_open, ha_close.values])
    
    df["ha_open"] = ha_open
    df["ha_high"] = ha_high
    df["ha_low"] = ha_low
    df["ha_close"] = ha_close.values
    return df

def run_backtest(df):
    """Run Heikin-Ashi Flat Wick 1:4 RR backtest across the 1m dataset."""
    trades = []
    in_trade = False
    current_trade = None
    
    for i in range(1, len(df)):
        # Check active trade exit
        if in_trade:
            t = current_trade
            high = df["high"].iloc[i]
            low = df["low"].iloc[i]
            
            # Calculate MFE/MAE
            if t["side"] == "LONG":
                fav = (high - t["entry_px"]) / t["risk_dist"]
                adv = (t["entry_px"] - low) / t["risk_dist"]
                t["mfe"] = max(t["mfe"], fav)
                t["mae"] = max(t["mae"], adv)
                
                # Check SL hit first (conservative)
                if low <= t["sl_px"]:
                    t["exit_px"] = t["sl_px"]
                    t["exit_idx"] = i
                    t["exit_dt"] = df["dt"].iloc[i]
                    t["outcome"] = "LOSS"
                    t["net_r"] = -1.0
                    trades.append(t)
                    in_trade = False
                    current_trade = None
                    continue
                elif high >= t["tp_px"]:
                    t["exit_px"] = t["tp_px"]
                    t["exit_idx"] = i
                    t["exit_dt"] = df["dt"].iloc[i]
                    t["outcome"] = "WIN"
                    t["net_r"] = 4.0
                    trades.append(t)
                    in_trade = False
                    current_trade = None
                    continue
            else: # SHORT
                fav = (t["entry_px"] - low) / t["risk_dist"]
                adv = (high - t["entry_px"]) / t["risk_dist"]
                t["mfe"] = max(t["mfe"], fav)
                t["mae"] = max(t["mae"], adv)
                
                if high >= t["sl_px"]:
                    t["exit_px"] = t["sl_px"]
                    t["exit_idx"] = i
                    t["exit_dt"] = df["dt"].iloc[i]
                    t["outcome"] = "LOSS"
                    t["net_r"] = -1.0
                    trades.append(t)
                    in_trade = False
                    current_trade = None
                    continue
                elif low <= t["tp_px"]:
                    t["exit_px"] = t["tp_px"]
                    t["exit_idx"] = i
                    t["exit_dt"] = df["dt"].iloc[i]
                    t["outcome"] = "WIN"
                    t["net_r"] = 4.0
                    trades.append(t)
                    in_trade = False
                    current_trade = None
                    continue
                    
        # Check new signal entry if not in trade
        if not in_trade and i < len(df) - 5:
            ha_o = df["ha_open"].iloc[i]
            ha_h = df["ha_high"].iloc[i]
            ha_l = df["ha_low"].iloc[i]
            ha_c = df["ha_close"].iloc[i]
            
            prev_ha_o = df["ha_open"].iloc[i - 1]
            prev_ha_h = df["ha_high"].iloc[i - 1]
            prev_ha_l = df["ha_low"].iloc[i - 1]
            prev_ha_c = df["ha_close"].iloc[i - 1]
            
            # LONG Condition: Green HA candle with flat bottom (ha_low == ha_open)
            is_flat_bottom = abs(ha_l - ha_o) / ha_o < 1e-5
            is_green = ha_c > ha_o
            prev_was_not_flat_green = not (prev_ha_c > prev_ha_o and abs(prev_ha_l - prev_ha_o) / prev_ha_o < 1e-5)
            
            # SHORT Condition: Red HA candle with flat top (ha_high == ha_open)
            is_flat_top = abs(ha_h - ha_o) / ha_o < 1e-5
            is_red = ha_c < ha_o
            prev_was_not_flat_red = not (prev_ha_c < prev_ha_o and abs(prev_ha_h - prev_ha_o) / prev_ha_o < 1e-5)
            
            if is_green and is_flat_bottom and prev_was_not_flat_green:
                entry_px = df["close"].iloc[i]
                sl_px = df["low"].iloc[i]
                risk_dist = entry_px - sl_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003: # min 0.03% distance
                    tp_px = entry_px + (4.0 * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "LONG",
                        "entry_idx": i,
                        "entry_dt": df["dt"].iloc[i],
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0
                    }
                    in_trade = True
                    
            elif is_red and is_flat_top and prev_was_not_flat_red:
                entry_px = df["close"].iloc[i]
                sl_px = df["high"].iloc[i]
                risk_dist = sl_px - entry_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px - (4.0 * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "SHORT",
                        "entry_idx": i,
                        "entry_dt": df["dt"].iloc[i],
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0
                    }
                    in_trade = True
                    
    # Handle still open trade
    if in_trade and current_trade:
        t = current_trade
        t["exit_px"] = df["close"].iloc[-1]
        t["exit_idx"] = len(df) - 1
        t["exit_dt"] = df["dt"].iloc[-1]
        if t["side"] == "LONG":
            t["net_r"] = (t["exit_px"] - t["entry_px"]) / t["risk_dist"]
        else:
            t["net_r"] = (t["entry_px"] - t["exit_px"]) / t["risk_dist"]
        t["outcome"] = "OPEN" if abs(t["net_r"]) < 0.5 else ("WIN" if t["net_r"] > 0 else "LOSS")
        trades.append(t)
        
    return trades

def draw_candles(ax, df_slice, is_ha=False, trigger_idx=None):
    """Draw high-fidelity candlesticks on matplotlib axis."""
    o_col = "ha_open" if is_ha else "open"
    h_col = "ha_high" if is_ha else "high"
    l_col = "ha_low" if is_ha else "low"
    c_col = "ha_close" if is_ha else "close"
    
    x_coords = np.arange(len(df_slice))
    
    for idx, (x, (_, row)) in enumerate(zip(x_coords, df_slice.iterrows())):
        o = row[o_col]
        h = row[h_col]
        l = row[l_col]
        c = row[c_col]
        
        is_bull = c >= o
        color = "#22c55e" if is_bull else "#ef4444"
        edge_color = "#4ade80" if is_bull else "#f87171"
        
        # Trigger candle special highlight
        if trigger_idx is not None and row.name == trigger_idx:
            # Highlight with gold glow box
            rect_glow = patches.Rectangle(
                (x - 0.45, min(o, c)), 0.9, max(abs(c - o), (h - l) * 0.05),
                linewidth=2.0, edgecolor="#fbbf24", facecolor=color, alpha=0.95, zorder=5
            )
            ax.add_patch(rect_glow)
        else:
            # Body rectangle
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.02)
            rect = patches.Rectangle(
                (x - 0.38, body_bottom), 0.76, body_height,
                linewidth=1.0, edgecolor=edge_color, facecolor=color, alpha=0.85, zorder=4
            )
            ax.add_patch(rect)
            
        # Wicks
        ax.vlines(x, l, h, color=edge_color, linewidth=1.2, zorder=3, alpha=0.9)

def render_trade_chart(df, trade, out_path):
    """Render high-resolution side-by-side Dark Mode chart for a single trade."""
    e_idx = trade["entry_idx"]
    x_idx = trade["exit_idx"]
    
    # 15 bars before entry to 10 bars after exit
    start_idx = max(0, e_idx - 15)
    end_idx = min(len(df), x_idx + 10)
    df_slice = df.iloc[start_idx:end_idx].copy()
    
    e_slice_x = e_idx - start_idx
    x_slice_x = x_idx - start_idx
    
    plt.style.use("dark_background")
    fig, (ax_norm, ax_ha) = plt.subplots(1, 2, figsize=(20, 8), dpi=140, gridspec_kw={"wspace": 0.12})
    fig.patch.set_facecolor("#0b0f19")
    
    side = trade["side"]
    outcome = trade["outcome"]
    net_r = trade["net_r"]
    banner_color = "#22c55e" if "WIN" in outcome else "#ef4444"
    
    # Title Super Header
    dt_str = trade["entry_dt"].strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(
        f"TRADE #{trade['trade_id']:02d} | BTC/USDT 1M | Side: {side} | Outcome: {outcome} ({net_r:+.1f}R) | Entry: {dt_str}",
        fontsize=16, fontweight="bold", color="#ffffff", y=0.98
    )
    
    # Left Subplot: Normal Candlestick Chart
    ax_norm.set_facecolor("#0f172a")
    draw_candles(ax_norm, df_slice, is_ha=False, trigger_idx=df.index[e_idx])
    
    # Right Subplot: Heikin-Ashi Candlestick Chart
    ax_ha.set_facecolor("#0f172a")
    draw_candles(ax_ha, df_slice, is_ha=True, trigger_idx=df.index[e_idx])
    
    # Annotations on both subplots
    for ax, title, chart_type in [
        (ax_norm, "NORMAL CANDLESTICKS (Real Market Orders & Fills)", "Normal"),
        (ax_ha, "HEIKIN-ASHI CANDLESTICKS (Synthetic Smoothed Trend)", "Heikin-Ashi")
    ]:
        ax.set_title(title, fontsize=12, fontweight="bold", color="#38bdf8", pad=10)
        ax.set_xlim(-0.8, len(df_slice) - 0.2)
        
        # Price levels
        entry_px = trade["entry_px"]
        sl_px = trade["sl_px"]
        tp_px = trade["tp_px"]
        
        ax.axhline(entry_px, color="#fbbf24", linestyle="--", linewidth=1.5, alpha=0.95, label=f"Entry: ${entry_px:,.2f}")
        ax.axhline(sl_px, color="#ef4444", linestyle="-", linewidth=1.5, alpha=0.95, label=f"SL (1.0R): ${sl_px:,.2f} ({trade['risk_pct']:.2f}%)")
        ax.axhline(tp_px, color="#22c55e", linestyle="-", linewidth=1.5, alpha=0.95, label=f"TP (4.0R): ${tp_px:,.2f}")
        
        # Shade risk & reward zones
        if side == "LONG":
            ax.axhspan(entry_px, tp_px, color="#22c55e", alpha=0.07)
            ax.axhspan(sl_px, entry_px, color="#ef4444", alpha=0.07)
        else:
            ax.axhspan(tp_px, entry_px, color="#22c55e", alpha=0.07)
            ax.axhspan(entry_px, sl_px, color="#ef4444", alpha=0.07)
            
        # Entry & Exit Markers
        ax.scatter([e_slice_x], [entry_px], color="#fbbf24", s=140, zorder=10, marker="^" if side == "LONG" else "v", edgecolors="#ffffff", label="Entry Fill")
        ax.scatter([x_slice_x], [trade["exit_px"]], color=banner_color, s=160, zorder=10, marker="X", edgecolors="#ffffff", label=f"Exit: {outcome}")
        
        # Annotation text box
        trade_info = (
            f"Side: {side}\n"
            f"Entry: ${entry_px:,.1f}\n"
            f"SL: ${sl_px:,.1f}\n"
            f"TP: ${tp_px:,.1f}\n"
            f"MFE: +{trade['mfe']:.2f}R\n"
            f"MAE: -{trade['mae']:.2f}R\n"
            f"Bars Held: {x_idx - e_idx}m"
        )
        ax.text(
            0.02, 0.96, trade_info, transform=ax.transAxes, fontsize=10,
            verticalalignment="top", fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#1e293b", edgecolor="#334155", alpha=0.9)
        )
        
        if chart_type == "Heikin-Ashi":
            # Add callout pointing to the flat-wick candle
            trigger_text = "Flat Bottom\n(No Lower Wick)" if side == "LONG" else "Flat Top\n(No Upper Wick)"
            ax.annotate(
                f"⚡ Trigger: {trigger_text}",
                xy=(e_slice_x, entry_px), xytext=(e_slice_x - 4, entry_px + (trade['risk_dist'] * 2.5 if side == "LONG" else -trade['risk_dist'] * 2.5)),
                arrowprops=dict(facecolor="#fbbf24", shrink=0.08, width=1.5, headwidth=7),
                fontsize=9, color="#fbbf24", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#1e293b", edgecolor="#fbbf24", alpha=0.9)
            )
            
        ax.legend(loc="lower right", fontsize=8.5, framealpha=0.4, facecolor="#1e293b", edgecolor="#334155")
        ax.grid(True, linestyle=":", alpha=0.2, color="#94a3b8")
        
        # Formatted x-axis timestamps
        step = max(1, len(df_slice) // 8)
        ticks = np.arange(0, len(df_slice), step)
        ax.set_xticks(ticks)
        ax.set_xticklabels([df_slice["dt"].iloc[i].strftime("%H:%M") for i in ticks], rotation=0, fontsize=8, color="#94a3b8")
        ax.tick_params(axis="y", colors="#94a3b8", labelsize=8)

    plt.subplots_adjust(top=0.90, bottom=0.08, left=0.05, right=0.97)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

def main():
    print("=========================================================================")
    print("🚀 FETCHING 24-HOUR 1-MINUTE BTC DATASET...")
    print("=========================================================================")
    df = fetch_btc_1m_24h()
    print(f"Loaded {len(df)} 1m candles for BTC/USDT.")
    print(f"Time Range: {df['dt'].iloc[0]} -> {df['dt'].iloc[-1]} UTC")
    print(f"Price Range: ${df['low'].min():,.2f} - ${df['high'].max():,.2f}\n")
    
    print("Calculating Heikin-Ashi series...")
    df = calculate_heikin_ashi(df)
    
    print("Simulating Heikin-Ashi Flat Wick (1:4 RR) Backtest...")
    trades = run_backtest(df)
    print(f"Total Trades Identified: {len(trades)}")
    
    if not trades:
        print("No trades triggered.")
        return
        
    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total_r = sum(t["net_r"] for t in trades)
    win_rate = (len(wins) / len(trades)) * 100.0
    
    print("\n=========================================================================")
    print(f"📊 BACKTEST RESULTS: BTC 1M (24 HOURS)")
    print("=========================================================================")
    print(f"• Total Trades : {len(trades)}")
    print(f"• Wins (4.0R)  : {len(wins)} ({win_rate:.2f}%)")
    print(f"• Losses (-1.0R): {len(losses)} ({len(losses)/len(trades)*100:.2f}%)")
    print(f"• Net PnL (R)  : {total_r:+.1f} R")
    print(f"• Expectancy   : {total_r / len(trades):+.2f} R per trade")
    print("=========================================================================\n")
    
    print(f"Rendering side-by-side charts for all {len(trades)} trades...")
    rendered_files = []
    
    for t in trades:
        fname = f"trade_{t['trade_id']:02d}_{t['side']}_{t['outcome']}_{t['net_r']:+.1f}R.png"
        out_path = os.path.join(OUT_DIR, fname)
        render_trade_chart(df, t, out_path)
        
        # Also copy to artifacts directory for web viewing
        art_path = os.path.join(ARTIFACT_DIR, fname)
        import shutil
        shutil.copyfile(out_path, art_path)
        rendered_files.append((fname, out_path, t))
        print(f"   ✓ Rendered Trade #{t['trade_id']:02d} ({t['side']} {t['outcome']}) -> {fname}")
        
    print(f"\n✅ All {len(trades)} trade charts successfully rendered in:")
    print(f"   Directory: {OUT_DIR}")
    
    # Save JSON summary of trades
    with open(os.path.join(OUT_DIR, "trades_summary.json"), "w") as f:
        json_trades = []
        for t in trades:
            tc = dict(t)
            tc["entry_dt"] = str(tc["entry_dt"])
            tc["exit_dt"] = str(tc["exit_dt"])
            json_trades.append(tc)
        json.dump(json_trades, f, indent=2)

if __name__ == "__main__":
    main()
