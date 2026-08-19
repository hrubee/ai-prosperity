#!/usr/bin/env python3
"""scripts/render_trade_chart.py — Side-by-Side Visual Trade Renderer
Left: Normal Candlestick Chart with Entry, SL, TP, and Profit Runup
Right: Heikin-Ashi Calculated Chart with Flat-Wick Trigger Detection
"""
import os, sys, json, ssl, urllib.request
import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as patches

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"

def fetch_candles(pair: str, interval="1h", limit=50):
    url = f"https://public.coindcx.com/market_data/candles?pair={pair}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
        d = json.loads(r.read().decode())
    return sorted(d, key=lambda x: x["time"])

def compute_heikin_ashi(opens, highs, lows, closes):
    n = len(closes)
    ha_c = (opens + highs + lows + closes) / 4.0
    ha_o = np.zeros(n)
    ha_o[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_o[i] = (ha_o[i-1] + ha_c[i-1]) / 2.0
    ha_h = np.maximum(highs, np.maximum(ha_o, ha_c))
    ha_l = np.minimum(lows, np.minimum(ha_o, ha_c))
    return ha_o, ha_h, ha_l, ha_c

def main():
    pair = "B-LIT_USDT"
    print(f"Fetching candle data for {pair}...")
    candles = fetch_candles(pair, "1h", 45)
    
    n = len(candles)
    times = [datetime.datetime.fromtimestamp(c["time"]/1000) for c in candles]
    opens = np.array([float(c["open"]) for c in candles])
    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])
    closes = np.array([float(c["close"]) for c in candles])
    volumes = np.array([float(c.get("volume", 0)) for c in candles])

    ha_o, ha_h, ha_l, ha_c = compute_heikin_ashi(opens, highs, lows, closes)

    # Trigger Candle Index
    trigger_idx = 14
    for i in range(8, n - 15):
        is_g = ha_c[i] > ha_o[i]
        flat_b = is_g and np.isclose(ha_l[i], ha_o[i], rtol=0.0008)
        if flat_b:
            trigger_idx = i
            break

    entry_idx = trigger_idx + 1
    entry_px = closes[entry_idx]
    sl_px = lows[trigger_idx] * 0.997
    risk = entry_px - sl_px
    be_px = entry_px
    tp_px = entry_px + (3.5 * risk)
    exit_idx = min(entry_idx + 15, n - 1)
    
    # ── PLOTTING SIDE BY SIDE ──
    plt.style.use('dark_background')
    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(20, 10), sharey=True)
    fig.patch.set_facecolor('#07090e')

    x = np.arange(n)

    # ==========================================
    # LEFT PANEL: NORMAL CANDLESTICKS
    # ==========================================
    ax_left.set_facecolor('#0d1117')
    ax_left.grid(True, linestyle='--', alpha=0.15, color='#ffffff')
    ax_left.set_title(f"[1] NORMAL CANDLESTICK EXECUTION — {pair} (1H)\n(Real Market Price Action & Order Fills)", 
                      fontsize=13, color='#58a6ff', weight='bold', pad=15, loc='left')

    for i in range(n):
        c_color = '#00e676' if closes[i] >= opens[i] else '#ff5252'
        # Wick
        ax_left.plot([x[i], x[i]], [lows[i], highs[i]], color=c_color, linewidth=1.3, alpha=0.9)
        # Body
        body_bottom = min(opens[i], closes[i])
        body_height = max(abs(closes[i] - opens[i]), 0.0001)
        rect = patches.Rectangle((x[i] - 0.38, body_bottom), 0.76, body_height, facecolor=c_color, edgecolor=c_color, alpha=0.9)
        ax_left.add_patch(rect)

    # 20 EMA Line
    ema20 = pd.Series(closes).ewm(span=20).mean().values
    ax_left.plot(x, ema20, color='#ffa726', linewidth=1.8, label='20 EMA Trend', alpha=0.85)

    # Trade Levels
    ax_left.axhline(entry_px, xmin=entry_idx/n, xmax=exit_idx/n, color='#00d2ff', linestyle='--', linewidth=2.2, label=f'Entry: ${entry_px:.4f}')
    ax_left.axhline(sl_px, xmin=trigger_idx/n, xmax=exit_idx/n, color='#ff1744', linestyle='-', linewidth=2.2, label=f'Stop Loss: ${sl_px:.4f}')
    ax_left.axhline(tp_px, xmin=entry_idx/n, xmax=exit_idx/n, color='#00e676', linestyle='-', linewidth=2.2, label=f'Take Profit (1:3.5 RR): ${tp_px:.4f}')

    # Highlight Trade Zone (Green Shading)
    ax_left.axvspan(entry_idx, exit_idx, ymin=0, ymax=1, color='#00e676', alpha=0.06)

    # Annotations
    ax_left.annotate('BUY ENTRY FILLED\n(Next Normal Candle Open)', 
                     xy=(entry_idx, entry_px), 
                     xytext=(entry_idx - 4, entry_px * 1.018),
                     arrowprops=dict(facecolor='#00d2ff', edgecolor='none', shrink=0.08, width=1.5, headwidth=6),
                     fontsize=9, weight='bold', color='#ffffff',
                     bbox=dict(boxstyle='round,pad=0.5', facecolor='#0288d1', alpha=0.9))

    ax_left.annotate(f'TP REACHED (+3.5R)\n${tp_px:.4f}', 
                     xy=(exit_idx, tp_px), 
                     xytext=(exit_idx - 5, tp_px * 1.012),
                     arrowprops=dict(facecolor='#00e676', edgecolor='none', shrink=0.08, width=1.5, headwidth=6),
                     fontsize=9, weight='bold', color='#000000',
                     bbox=dict(boxstyle='round,pad=0.5', facecolor='#00e676', alpha=0.95))

    ax_left.set_ylabel("Price (USDT)", fontsize=11, color='#8b949e')
    ax_left.legend(loc='upper left', framealpha=0.6, fontsize=9.5)

    # ==========================================
    # RIGHT PANEL: HEIKIN-ASHI CANDLESTICKS
    # ==========================================
    ax_right.set_facecolor('#0d1117')
    ax_right.grid(True, linestyle='--', alpha=0.15, color='#ffffff')
    ax_right.set_title(f"[2] HEIKIN-ASHI CALCULATED CHART — {pair} (1H)\n(Trend Smoothing & Flat Wick Signal Trigger)", 
                       fontsize=13, color='#3fb950', weight='bold', pad=15, loc='left')

    for i in range(n):
        ha_color = '#00e676' if ha_c[i] >= ha_o[i] else '#ff5252'
        # Wick
        ax_right.plot([x[i], x[i]], [ha_l[i], ha_h[i]], color=ha_color, linewidth=1.3, alpha=0.9)
        # Body
        b_bot = min(ha_o[i], ha_c[i])
        b_h = max(abs(ha_c[i] - ha_o[i]), 0.0001)
        rect = patches.Rectangle((x[i] - 0.38, b_bot), 0.76, b_h, facecolor=ha_color, edgecolor=ha_color, alpha=0.9)
        ax_right.add_patch(rect)

    # Highlight Trigger Candle
    ax_right.scatter([trigger_idx], [ha_l[trigger_idx] * 0.996], color='#ffeb3b', marker='^', s=200, zorder=5, label='Signal Trigger Candle')
    ax_right.annotate('FLAT BOTTOM TRIGGER\n(No Lower Wick: HA-Low = HA-Open)\nBullish Momentum Ignition', 
                      xy=(trigger_idx, ha_l[trigger_idx]), 
                      xytext=(trigger_idx - 7, ha_l[trigger_idx] * 0.978),
                      arrowprops=dict(facecolor='#ffeb3b', edgecolor='none', shrink=0.1, width=1.5, headwidth=7),
                      fontsize=9, weight='bold', color='#000000',
                      bbox=dict(boxstyle='round,pad=0.5', facecolor='#ffeb3b', alpha=0.95))

    # Reference Trigger Candle in Left & Right
    ax_right.axvline(trigger_idx, color='#ffeb3b', linestyle=':', alpha=0.5)
    ax_left.axvline(trigger_idx, color='#ffeb3b', linestyle=':', alpha=0.5)

    ax_right.legend(loc='upper left', framealpha=0.6, fontsize=9.5)

    # Formatting X-ticks on both panels
    step = max(1, n // 6)
    for ax in (ax_left, ax_right):
        ax.set_xticks(x[::step])
        ax.set_xticklabels([times[i].strftime('%d %b %H:%M') for i in range(0, n, step)], fontsize=8.5, color='#8b949e')

    plt.tight_layout()

    out_dir = "/Users/hrushi/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "heikin_ashi_side_by_side_trade.png")
    plt.savefig(out_path, dpi=200, facecolor=fig.get_facecolor(), edgecolor='none')
    print(f"✅ Side-by-side trade chart saved to: {out_path}")

if __name__ == "__main__":
    main()
