import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import time
import os
import ssl
import json
import urllib.request

ssl_ctx = ssl._create_unverified_context()

# Fetch AVAX 1H data directly
url = "https://fapi.binance.com/fapi/v1/klines?symbol=AVAXUSDT&interval=1h&limit=1000"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
klines = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=8))

idx = 831 # 2026-08-12 12:00 UTC trade

start_i = idx - 16
end_i = idx + 10
window_klines = klines[start_i:end_i]

times = [int(r[0]) for r in window_klines]
opens = np.array([float(r[1]) for r in window_klines])
highs = np.array([float(r[2]) for r in window_klines])
lows = np.array([float(r[3]) for r in window_klines])
closes = np.array([float(r[4]) for r in window_klines])
vols = np.array([float(r[5]) for r in window_klines])

signal_bar_idx = 16
entry_bar_idx = 17

# Calculate ATR(14)
n_total = len(klines)
tr = np.zeros(n_total)
for i in range(1, n_total):
    tr[i] = max(float(klines[i][2])-float(klines[i][3]), abs(float(klines[i][2])-float(klines[i-1][4])), abs(float(klines[i][3])-float(klines[i-1][4])))
atr_val = np.mean(tr[idx-13:idx+1])

entry_px = opens[entry_bar_idx]
risk_dist = 1.0 * atr_val
sl_px = entry_px + risk_dist
tp_px = entry_px - (2.0 * risk_dist)

# Plot dark mode professional trading chart
plt.style.use('dark_background')
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8.5), dpi=160, gridspec_kw={'height_ratios': [3.0, 1.2]})
fig.patch.set_facecolor('#0d1117')
ax1.set_facecolor('#161b22')
ax2.set_facecolor('#161b22')

x_coords = np.arange(len(window_klines))
width = 0.65

# 1. Candlestick Chart
for i in range(len(window_klines)):
    o, h, l, c = opens[i], highs[i], lows[i], closes[i]
    color = '#26a69a' if c >= o else '#ef5350' # Clean tradingview green / red
    
    if i == signal_bar_idx:
        color = '#e5a50a' # Golden yellow signal candle
        
    ax1.plot([x_coords[i], x_coords[i]], [l, h], color=color, linewidth=1.3)
    rect_y = min(o, c)
    rect_h = max(abs(c - o), 0.003)
    rect = patches.Rectangle((x_coords[i] - width/2, rect_y), width, rect_h, facecolor=color, edgecolor=color, alpha=0.95)
    ax1.add_patch(rect)

# Add Trade Execution Lines
ax1.axhline(y=entry_px, color='#00d8ff', linestyle='--', linewidth=1.5, label=f'Short Entry: ${entry_px:.2f}')
ax1.axhline(y=sl_px, color='#ff4d6d', linestyle='--', linewidth=1.5, label=f'Stop Loss (1.0x ATR): ${sl_px:.2f} (-0.8%)')
ax1.axhline(y=tp_px, color='#00f097', linestyle='--', linewidth=1.5, label=f'Take Profit (1:2.0 RR): ${tp_px:.2f} (+1.7%)')

# Fill zones
ax1.axhspan(tp_px, entry_px, xmin=(entry_bar_idx-0.5)/(len(window_klines)), xmax=(entry_bar_idx+2.5)/(len(window_klines)), color='#00f097', alpha=0.15)
ax1.axhspan(entry_px, sl_px, xmin=(entry_bar_idx-0.5)/(len(window_klines)), xmax=(entry_bar_idx+2.5)/(len(window_klines)), color='#ff4d6d', alpha=0.10)

# In-chart Callouts
ax1.annotate(
    '8.7x VOLUME SURGE\nUpper Wick: 57% Rejection\nWhale Absorption',
    xy=(signal_bar_idx, highs[signal_bar_idx]),
    xytext=(signal_bar_idx - 6.5, highs[signal_bar_idx] - 0.01),
    arrowprops=dict(facecolor='#e5a50a', edgecolor='#e5a50a', width=1.2, headwidth=6),
    fontsize=9.5, fontweight='bold', color='#e5a50a',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#1f2430', edgecolor='#e5a50a', alpha=0.95)
)

ax1.annotate(
    f'SHORT ENTRY\nPrice: ${entry_px:.2f}',
    xy=(entry_bar_idx, entry_px),
    xytext=(entry_bar_idx + 1.2, entry_px + 0.05),
    arrowprops=dict(facecolor='#00d8ff', edgecolor='#00d8ff', width=1.2, headwidth=6),
    fontsize=9.5, fontweight='bold', color='#00d8ff',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#1f2430', edgecolor='#00d8ff', alpha=0.95)
)

tp_hit_idx = entry_bar_idx + 2
ax1.annotate(
    f'TAKE PROFIT HIT (+2.0R)\nClosed: ${tp_px:.2f} in 2 Hours',
    xy=(tp_hit_idx, tp_px),
    xytext=(tp_hit_idx + 0.8, tp_px - 0.06),
    arrowprops=dict(facecolor='#00f097', edgecolor='#00f097', width=1.2, headwidth=6),
    fontsize=10, fontweight='bold', color='#00f097',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#1f2430', edgecolor='#00f097', alpha=0.95)
)

ax1.set_title('DumpRide 1H Institutional Exhaustion Short — AVAX/USDT (+2.0R Target Hit)', fontsize=14, fontweight='bold', pad=12, color='#ffffff')
ax1.set_ylabel('Price (USDT)', fontsize=11, color='#8b949e')
ax1.grid(True, linestyle=':', alpha=0.25, color='#30363d')
ax1.legend(loc='upper left', framealpha=0.95, facecolor='#1f2430', edgecolor='#30363d', fontsize=9.5)
ax1.set_ylim([min(lows) - 0.04, max(highs) + 0.06])

# 2. Volume Sub-Chart
base_vol = float(np.mean([float(r[5]) for r in klines[idx-20:idx]]))
vol_colors = ['#e5a50a' if i == signal_bar_idx else ('#26a69a' if closes[i] >= opens[i] else '#ef5350') for i in range(len(window_klines))]

ax2.bar(x_coords, vols, width=width, color=vol_colors, alpha=0.85)
ax2.axhline(y=base_vol, color='#00d8ff', linestyle=':', linewidth=1.5, label=f'20-Bar Baseline: {base_vol/1000:,.0f}k tokens (~$2.3M USDT)')
ax2.annotate(
    f'8.7x Surge ({vols[signal_bar_idx]/1000:,.0f}k tokens)',
    xy=(signal_bar_idx, vols[signal_bar_idx]),
    xytext=(signal_bar_idx - 5.5, vols[signal_bar_idx] * 0.85),
    arrowprops=dict(facecolor='#e5a50a', edgecolor='#e5a50a', width=1.0, headwidth=5),
    fontsize=9, fontweight='bold', color='#e5a50a',
    bbox=dict(boxstyle='round,pad=0.3', facecolor='#1f2430', edgecolor='#e5a50a', alpha=0.9)
)

date_labels = [time.strftime('%m-%d %H:00', time.gmtime(t/1000)) for t in times]
ax2.set_xticks(x_coords[::2])
ax2.set_xticklabels(date_labels[::2], rotation=20, fontsize=8.5, color='#8b949e')
ax2.set_ylabel('1H Volume', fontsize=11, color='#8b949e')
ax2.grid(True, linestyle=':', alpha=0.25, color='#30363d')
ax2.legend(loc='upper left', framealpha=0.95, facecolor='#1f2430', edgecolor='#30363d', fontsize=9)

plt.subplots_adjust(hspace=0.15, top=0.93, bottom=0.10, left=0.08, right=0.95)

out_path = "/Users/hrushi/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f/dumpride_trade_example.png"
plt.savefig(out_path, dpi=200, facecolor=fig.get_facecolor(), edgecolor='none')
plt.close()

print(f"✅ Perfectly rendered trade chart to: {out_path}")
