#!/usr/bin/env python3
"""tools/backtest/backtest_btc_15m_1m_render.py — Backtest 15M Heikin-Ashi Strategy on BTC with 1M Execution Granularity (24 Hours).

- Signal Timeframe: 15 Minutes (15M) Heikin-Ashi Flat-Wick Triggers
- Backtest / Execution Granularity: 1 Minute (1M) Intra-Bar Resolution
- Side-by-side rendering: Left = Normal Candlesticks (15M), Right = Heikin-Ashi (15M)
"""
import os
import sys
import json
import time
import ssl
import urllib.request
import datetime
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches

WORKSPACE_DIR = "/Users/hrushi/Desktop/ai-prosperity"
OUT_DIR = os.path.join(WORKSPACE_DIR, "artifacts/rendered_btc_15m_trades")
ARTIFACT_DIR = "/Users/hrushi/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f"
os.makedirs(OUT_DIR, exist_ok=True)

def fetch_btc_1m_dataset():
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
    df.set_index("dt", inplace=True)
    return df

def build_15m_and_ha(df_1m):
    """Resample 1m candles into 15m candles and compute 15m Heikin-Ashi series."""
    df_15m = df_1m.resample("15min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "open_time": "first"
    }).dropna()
    
    # Heikin-Ashi computation on 15M
    ha_close = (df_15m["open"] + df_15m["high"] + df_15m["low"] + df_15m["close"]) / 4.0
    ha_open = np.zeros(len(df_15m))
    ha_open[0] = (df_15m["open"].iloc[0] + df_15m["close"].iloc[0]) / 2.0
    
    for i in range(1, len(df_15m)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0
        
    ha_high = np.maximum.reduce([df_15m["high"].values, ha_open, ha_close.values])
    ha_low = np.minimum.reduce([df_15m["low"].values, ha_open, ha_close.values])
    
    df_15m["ha_open"] = ha_open
    df_15m["ha_high"] = ha_high
    df_15m["ha_low"] = ha_low
    df_15m["ha_close"] = ha_close.values
    return df_15m

def run_15m_backtest_with_1m_resolution(df_15m, df_1m):
    """Run 15M Heikin-Ashi signals backtest resolved at 1M candle granularity."""
    trades = []
    in_trade = False
    current_trade = None
    
    for i in range(1, len(df_15m)):
        t_15m_start = df_15m.index[i]
        t_15m_end = t_15m_start + pd.Timedelta(minutes=15)
        
        # 1. If currently in trade, check intra-bar 1m price action during this 15m candle
        if in_trade:
            # Slice 1m bars for this 15m period
            bars_1m = df_1m.loc[t_15m_start:t_15m_end - pd.Timedelta(milliseconds=1)]
            for t_1m, row in bars_1m.iterrows():
                high = row["high"]
                low = row["low"]
                t = current_trade
                
                if t["side"] == "LONG":
                    fav = (high - t["entry_px"]) / t["risk_dist"]
                    adv = (t["entry_px"] - low) / t["risk_dist"]
                    t["mfe"] = max(t["mfe"], fav)
                    t["mae"] = max(t["mae"], adv)
                    
                    if low <= t["sl_px"]:
                        t["exit_px"] = t["sl_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "LOSS"
                        t["net_r"] = -1.0
                        t["exit_15m_idx"] = i
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                    elif high >= t["tp_px"]:
                        t["exit_px"] = t["tp_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "WIN"
                        t["net_r"] = 4.0
                        t["exit_15m_idx"] = i
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                else: # SHORT
                    fav = (t["entry_px"] - low) / t["risk_dist"]
                    adv = (high - t["entry_px"]) / t["risk_dist"]
                    t["mfe"] = max(t["mfe"], fav)
                    t["mae"] = max(t["mae"], adv)
                    
                    if high >= t["sl_px"]:
                        t["exit_px"] = t["sl_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "LOSS"
                        t["net_r"] = -1.0
                        t["exit_15m_idx"] = i
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                    elif low <= t["tp_px"]:
                        t["exit_px"] = t["tp_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "WIN"
                        t["net_r"] = 4.0
                        t["exit_15m_idx"] = i
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                        
        # 2. If not in trade, evaluate 15m Heikin-Ashi trigger at candle close of bar (i)
        if not in_trade and i < len(df_15m) - 2:
            ha_o = df_15m["ha_open"].iloc[i]
            ha_h = df_15m["ha_high"].iloc[i]
            ha_l = df_15m["ha_low"].iloc[i]
            ha_c = df_15m["ha_close"].iloc[i]
            
            prev_ha_o = df_15m["ha_open"].iloc[i - 1]
            prev_ha_c = df_15m["ha_close"].iloc[i - 1]
            prev_ha_l = df_15m["ha_low"].iloc[i - 1]
            prev_ha_h = df_15m["ha_high"].iloc[i - 1]
            
            # LONG Condition: Green 15M HA candle with flat bottom (no lower wick)
            is_flat_bottom = abs(ha_l - ha_o) / ha_o < 1e-5
            is_green = ha_c > ha_o
            prev_not_flat_green = not (prev_ha_c > prev_ha_o and abs(prev_ha_l - prev_ha_o) / prev_ha_o < 1e-5)
            
            # SHORT Condition: Red 15M HA candle with flat top (no upper wick)
            is_flat_top = abs(ha_h - ha_o) / ha_o < 1e-5
            is_red = ha_c < ha_o
            prev_not_flat_red = not (prev_ha_c < prev_ha_o and abs(prev_ha_h - prev_ha_o) / prev_ha_o < 1e-5)
            
            if is_green and is_flat_bottom and prev_not_flat_green:
                entry_px = df_15m["close"].iloc[i]
                sl_px = df_15m["ha_low"].iloc[i] # Exact Heikin-Ashi Flat Bottom (ha_open / ha_low)
                risk_dist = entry_px - sl_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px + (4.0 * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "LONG",
                        "entry_15m_idx": i,
                        "entry_dt": df_15m.index[i] + pd.Timedelta(minutes=15), # Entry at candle close
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0
                    }
                    in_trade = True
                    
            elif is_red and is_flat_top and prev_not_flat_red:
                entry_px = df_15m["close"].iloc[i]
                sl_px = df_15m["ha_high"].iloc[i] # Exact Heikin-Ashi Flat Top (ha_open / ha_high)
                risk_dist = sl_px - entry_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px - (4.0 * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "SHORT",
                        "entry_15m_idx": i,
                        "entry_dt": df_15m.index[i] + pd.Timedelta(minutes=15),
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0
                    }
                    in_trade = True

    # Check still open trade
    if in_trade and current_trade:
        t = current_trade
        t["exit_px"] = df_1m["close"].iloc[-1]
        t["exit_dt"] = df_1m.index[-1]
        t["exit_15m_idx"] = len(df_15m) - 1
        if t["side"] == "LONG":
            t["net_r"] = (t["exit_px"] - t["entry_px"]) / t["risk_dist"]
        else:
            t["net_r"] = (t["entry_px"] - t["exit_px"]) / t["risk_dist"]
        t["outcome"] = "OPEN" if abs(t["net_r"]) < 0.5 else ("WIN" if t["net_r"] > 0 else "LOSS")
        trades.append(t)
        
    return trades

def draw_15m_candles(ax, df_slice, is_ha=False, trigger_dt=None):
    """Draw high-resolution 15M candlesticks on matplotlib axis."""
    o_col = "ha_open" if is_ha else "open"
    h_col = "ha_high" if is_ha else "high"
    l_col = "ha_low" if is_ha else "low"
    c_col = "ha_close" if is_ha else "close"
    
    x_coords = np.arange(len(df_slice))
    
    for idx, (x, (t, row)) in enumerate(zip(x_coords, df_slice.iterrows())):
        o = row[o_col]
        h = row[h_col]
        l = row[l_col]
        c = row[c_col]
        
        is_bull = c >= o
        color = "#22c55e" if is_bull else "#ef4444"
        edge_color = "#4ade80" if is_bull else "#f87171"
        
        # Trigger candle highlight
        if trigger_dt is not None and t == trigger_dt:
            rect_glow = patches.Rectangle(
                (x - 0.45, min(o, c)), 0.9, max(abs(c - o), (h - l) * 0.05),
                linewidth=2.2, edgecolor="#fbbf24", facecolor=color, alpha=0.95, zorder=5
            )
            ax.add_patch(rect_glow)
        else:
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.02)
            rect = patches.Rectangle(
                (x - 0.38, body_bottom), 0.76, body_height,
                linewidth=1.0, edgecolor=edge_color, facecolor=color, alpha=0.85, zorder=4
            )
            ax.add_patch(rect)
            
        ax.vlines(x, l, h, color=edge_color, linewidth=1.2, zorder=3, alpha=0.9)

def render_15m_trade_chart(df_15m, df_1m, trade, out_path):
    """Render high-resolution side-by-side Dark Mode chart for 15M trade with 1M resolution path."""
    e_15m_idx = trade["entry_15m_idx"]
    x_15m_idx = trade.get("exit_15m_idx", e_15m_idx + 1)
    
    start_idx = max(0, e_15m_idx - 10)
    end_idx = min(len(df_15m), x_15m_idx + 8)
    df_slice = df_15m.iloc[start_idx:end_idx].copy()
    
    e_slice_x = e_15m_idx - start_idx
    x_slice_x = x_15m_idx - start_idx
    trigger_dt = df_15m.index[e_15m_idx]
    
    plt.style.use("dark_background")
    fig, (ax_norm, ax_ha) = plt.subplots(1, 2, figsize=(20, 8), dpi=140, gridspec_kw={"wspace": 0.12})
    fig.patch.set_facecolor("#0b0f19")
    
    side = trade["side"]
    outcome = trade["outcome"]
    net_r = trade["net_r"]
    banner_color = "#22c55e" if "WIN" in outcome else "#ef4444"
    
    duration_mins = int((trade["exit_dt"] - trade["entry_dt"]).total_seconds() / 60)
    
    dt_str = trade["entry_dt"].strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(
        f"TRADE #{trade['trade_id']:02d} | BTC/USDT 15M Timeframe (1M Intra-Bar Execution) | Side: {side} | Outcome: {outcome} ({net_r:+.1f}R) | Entry: {dt_str}",
        fontsize=15, fontweight="bold", color="#ffffff", y=0.98
    )
    
    # Left Subplot: Normal 15M Candlesticks
    ax_norm.set_facecolor("#0f172a")
    draw_15m_candles(ax_norm, df_slice, is_ha=False, trigger_dt=trigger_dt)
    
    # Right Subplot: Heikin-Ashi 15M Candlesticks
    ax_ha.set_facecolor("#0f172a")
    draw_15m_candles(ax_ha, df_slice, is_ha=True, trigger_dt=trigger_dt)
    
    for ax, title, chart_type in [
        (ax_norm, "NORMAL 15M CANDLESTICKS (Real Market Prices & 1M Fills)", "Normal"),
        (ax_ha, "HEIKIN-ASHI 15M CANDLESTICKS (15M Trend & Flat-Wick Setup)", "Heikin-Ashi")
    ]:
        ax.set_title(title, fontsize=12, fontweight="bold", color="#38bdf8", pad=10)
        ax.set_xlim(-0.8, len(df_slice) - 0.2)
        
        entry_px = trade["entry_px"]
        sl_px = trade["sl_px"]
        tp_px = trade["tp_px"]
        
        ax.axhline(entry_px, color="#fbbf24", linestyle="--", linewidth=1.5, alpha=0.95, label=f"Entry: ${entry_px:,.2f}")
        ax.axhline(sl_px, color="#ef4444", linestyle="-", linewidth=1.5, alpha=0.95, label=f"SL (1.0R): ${sl_px:,.2f} ({trade['risk_pct']:.2f}%)")
        ax.axhline(tp_px, color="#22c55e", linestyle="-", linewidth=1.5, alpha=0.95, label=f"TP (4.0R): ${tp_px:,.2f}")
        
        if side == "LONG":
            ax.axhspan(entry_px, tp_px, color="#22c55e", alpha=0.07)
            ax.axhspan(sl_px, entry_px, color="#ef4444", alpha=0.07)
        else:
            ax.axhspan(tp_px, entry_px, color="#22c55e", alpha=0.07)
            ax.axhspan(entry_px, sl_px, color="#ef4444", alpha=0.07)
            
        ax.scatter([e_slice_x], [entry_px], color="#fbbf24", s=140, zorder=10, marker="^" if side == "LONG" else "v", edgecolors="#ffffff", label="15M Close Entry Fill")
        ax.scatter([x_slice_x], [trade["exit_px"]], color=banner_color, s=160, zorder=10, marker="X", edgecolors="#ffffff", label=f"1M Fill Exit: {outcome}")
        
        trade_info = (
            f"Side: {side}\n"
            f"15M Entry: ${entry_px:,.1f}\n"
            f"SL: ${sl_px:,.1f}\n"
            f"TP (1:4 RR): ${tp_px:,.1f}\n"
            f"MFE: +{trade['mfe']:.2f}R\n"
            f"MAE: -{trade['mae']:.2f}R\n"
            f"Duration: {duration_mins} mins (1M res)"
        )
        ax.text(
            0.02, 0.96, trade_info, transform=ax.transAxes, fontsize=10,
            verticalalignment="top", fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#1e293b", edgecolor="#334155", alpha=0.9)
        )
        
        if chart_type == "Heikin-Ashi":
            trigger_text = "15M Flat Bottom\n(No Lower Wick)" if side == "LONG" else "15M Flat Top\n(No Upper Wick)"
            ax.annotate(
                f"⚡ 15M Trigger: {trigger_text}",
                xy=(e_slice_x, entry_px), xytext=(e_slice_x - 3, entry_px + (trade['risk_dist'] * 2.2 if side == "LONG" else -trade['risk_dist'] * 2.2)),
                arrowprops=dict(facecolor="#fbbf24", shrink=0.08, width=1.5, headwidth=7),
                fontsize=9, color="#fbbf24", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#1e293b", edgecolor="#fbbf24", alpha=0.9)
            )
            
        ax.legend(loc="lower right", fontsize=8.5, framealpha=0.4, facecolor="#1e293b", edgecolor="#334155")
        ax.grid(True, linestyle=":", alpha=0.2, color="#94a3b8")
        
        step = max(1, len(df_slice) // 8)
        ticks = np.arange(0, len(df_slice), step)
        ax.set_xticks(ticks)
        ax.set_xticklabels([df_slice.index[i].strftime("%H:%M") for i in ticks], rotation=0, fontsize=8, color="#94a3b8")
        ax.tick_params(axis="y", colors="#94a3b8", labelsize=8)

    plt.subplots_adjust(top=0.90, bottom=0.08, left=0.05, right=0.97)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

def main():
    print("=========================================================================")
    print("🚀 FETCHING 24-HOUR 1-MINUTE BTC DATASET FOR 15M HEIKIN-ASHI BACKTEST...")
    print("=========================================================================")
    df_1m = fetch_btc_1m_dataset()
    print(f"Loaded {len(df_1m)} 1m candles.")
    
    print("Constructing 15-Minute Resampled & Heikin-Ashi series...")
    df_15m = build_15m_and_ha(df_1m)
    print(f"Built {len(df_15m)} 15m candles over 24 hours.")
    print(f"Time Range: {df_15m.index[0]} -> {df_15m.index[-1]} UTC\n")
    
    print("Running 15M Heikin-Ashi strategy backtest with 1M execution granularity...")
    trades = run_15m_backtest_with_1m_resolution(df_15m, df_1m)
    print(f"Total 15M Trades Triggered: {len(trades)}")
    
    if not trades:
        print("No trades triggered.")
        return
        
    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total_r = sum(t["net_r"] for t in trades)
    win_rate = (len(wins) / len(trades)) * 100.0 if trades else 0.0
    
    print("\n=========================================================================")
    print(f"📊 BACKTEST RESULTS: BTC 15M TIMEFRAME (1M RESOLUTION, 24 HOURS)")
    print("=========================================================================")
    print(f"• Total Trades : {len(trades)}")
    print(f"• Wins (4.0R)  : {len(wins)} ({win_rate:.2f}%)")
    print(f"• Losses (-1.0R): {len(losses)} ({len(losses)/len(trades)*100:.2f}%)")
    print(f"• Net PnL (R)  : {total_r:+.1f} R")
    print(f"• Expectancy   : {total_r / len(trades):+.2f} R per trade")
    print("=========================================================================\n")
    
    print(f"Rendering side-by-side 15M charts for all {len(trades)} trades...")
    import shutil
    for t in trades:
        fname = f"trade_15m_{t['trade_id']:02d}_{t['side']}_{t['outcome']}_{t['net_r']:+.1f}R.png"
        out_path = os.path.join(OUT_DIR, fname)
        render_15m_trade_chart(df_15m, df_1m, t, out_path)
        
        art_path = os.path.join(ARTIFACT_DIR, fname)
        shutil.copyfile(out_path, art_path)
        print(f"   ✓ Rendered Trade #{t['trade_id']:02d} ({t['side']} {t['outcome']}) -> {fname}")
        
    print(f"\n✅ All {len(trades)} trade charts successfully saved in:")
    print(f"   Directory: {OUT_DIR}")
    
    with open(os.path.join(OUT_DIR, "trades_15m_summary.json"), "w") as f:
        json_trades = []
        for t in trades:
            tc = dict(t)
            tc["entry_dt"] = str(tc["entry_dt"])
            tc["exit_dt"] = str(tc["exit_dt"])
            json_trades.append(tc)
        json.dump(json_trades, f, indent=2)

if __name__ == "__main__":
    main()
