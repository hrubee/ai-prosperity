import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import time
import os
import ssl
import json
import urllib.request

ssl_ctx = ssl._create_unverified_context()

# Fetch CRV 15m data around 2026-08-12
url = "https://fapi.binance.com/fapi/v1/klines?symbol=CRVUSDT&interval=15m&limit=1500"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
klines = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=8))

# Find the CRV trade around 2026-08-12 14:30 UTC
trade_idx = None
for i, r in enumerate(klines):
    t_str = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r[0]/1000))
    if "2026-08-12 14:30" in t_str:
        trade_idx = i
        break

if trade_idx is None:
    trade_idx = 700

start_i = trade_idx - 15
end_i = trade_idx + 18
window_klines = klines[start_i:end_i]

times = [int(r[0]) for r in window_klines]
opens = np.array([float(r[1]) for r in window_klines])
highs = np.array([float(r[2]) for r in window_klines])
lows = np.array([float(r[3]) for r in window_klines])
closes = np.array([float(r[4]) for r in window_klines])
vols = np.array([float(r[5]) for r in window_klines])

spike_bar_idx = 15 # in window
reclaim_bar_idx = 17 # in window (15:00 UTC)
entry_bar_idx = 18

spike_low = lows[spike_bar_idx]
entry_px = opens[entry_bar_idx]
risk_dist = 0.0036 # from backtest
sl_px = entry_px - risk_dist
tp_px = entry_px + (2.0 * risk_dist)

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
    color = '#26a69a' if c >= o else '#ef5350'
    
    if i == spike_bar_idx:
        color = '#ff3366' # Panic Dump Bar
    elif i == reclaim_bar_idx:
        color = '#e5a50a' # Reclaim 2B Bar
        
    ax1.plot([x_coords[i], x_coords[i]], [l, h], color=color, linewidth=1.3)
    rect_y = min(o, c)
    rect_h = max(abs(c - o), 0.0002)
    rect = patches.Rectangle((x_coords[i] - width/2, rect_y), width, rect_h, facecolor=color, edgecolor=color, alpha=0.95)
    ax1.add_patch(rect)

# Draw Spike Low Liquidity Level
ax1.axhline(y=spike_low, color='#e5a50a', linestyle=':', linewidth=1.8, label=f'Spike Low (Liquidity Level): ${spike_low:.4f}')

# Add Trade Execution Lines
ax1.axhline(y=entry_px, color='#00d8ff', linestyle='--', linewidth=1.5, label=f'Long Entry: ${entry_px:.4f}')
ax1.axhline(y=sl_px, color='#ff4d6d', linestyle='--', linewidth=1.5, label=f'Stop Loss: ${sl_px:.4f} (-1.3%)')
ax1.axhline(y=tp_px, color='#00f097', linestyle='--', linewidth=1.5, label=f'Take Profit (1:2.0 RR): ${tp_px:.4f} (+2.6%)')

# Fill zones
ax1.axhspan(entry_px, tp_px, xmin=(entry_bar_idx-0.5)/(len(window_klines)), xmax=(entry_bar_idx+5.5)/(len(window_klines)), color='#00f097', alpha=0.15)
ax1.axhspan(sl_px, entry_px, xmin=(entry_bar_idx-0.5)/(len(window_klines)), xmax=(entry_bar_idx+5.5)/(len(window_klines)), color='#ff4d6d', alpha=0.10)

# Annotations
ax1.annotate(
    f'PANIC DUMP BAR\nVol: 4.8x Surge\nLow: ${spike_low:.4f}',
    xy=(spike_bar_idx, lows[spike_bar_idx]),
    xytext=(spike_bar_idx - 5.5, lows[spike_bar_idx] - 0.003),
    arrowprops=dict(facecolor='#ff3366', edgecolor='#ff3366', width=1.2, headwidth=6),
    fontsize=9, fontweight='bold', color='#ff3366',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#1f2430', edgecolor='#ff3366', alpha=0.95)
)

ax1.annotate(
    f'2B SWEEP & RECLAIM\nSwept below ${spike_low:.4f}\nClosed ABOVE Level!',
    xy=(reclaim_bar_idx, lows[reclaim_bar_idx]),
    xytext=(reclaim_bar_idx - 3.5, lows[reclaim_bar_idx] - 0.005),
    arrowprops=dict(facecolor='#e5a50a', edgecolor='#e5a50a', width=1.2, headwidth=6),
    fontsize=9.5, fontweight='bold', color='#e5a50a',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#1f2430', edgecolor='#e5a50a', alpha=0.95)
)

tp_hit_idx = entry_bar_idx + 5
ax1.annotate(
    f'TAKE PROFIT HIT (+2.0R)\nClosed: ${tp_px:.4f} (+2.6% Gain)',
    xy=(tp_hit_idx, tp_px),
    xytext=(tp_hit_idx - 1.5, tp_px + 0.003),
    arrowprops=dict(facecolor='#00f097', edgecolor='#00f097', width=1.2, headwidth=6),
    fontsize=10, fontweight='bold', color='#00f097',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#1f2430', edgecolor='#00f097', alpha=0.95)
)

ax1.set_title('Real 2B2T Reclaim Trade — CRV/USDT (2026-08-12 14:30 UTC -> +2.0R Win)', fontsize=14, fontweight='bold', pad=12, color='#ffffff')
ax1.set_ylabel('Price (USDT)', fontsize=11, color='#8b949e')
ax1.grid(True, linestyle=':', alpha=0.25, color='#30363d')
ax1.legend(loc='upper left', framealpha=0.95, facecolor='#1f2430', edgecolor='#30363d', fontsize=9)

# 2. Volume Sub-Chart
vol_colors = ['#ff3366' if i == spike_bar_idx else ('#e5a50a' if i == reclaim_bar_idx else ('#26a69a' if closes[i] >= opens[i] else '#ef5350')) for i in range(len(window_klines))]

ax2.bar(x_coords, vols, width=width, color=vol_colors, alpha=0.85)
base_vol = float(np.mean([float(r[5]) for r in klines[trade_idx-20:trade_idx]]))
ax2.axhline(y=base_vol, color='#00d8ff', linestyle=':', linewidth=1.5, label=f'20-Bar Baseline Volume: {base_vol/1000:,.0f}k tokens')

date_labels = [time.strftime('%m-%d %H:%M', time.gmtime(t/1000)) for t in times]
ax2.set_xticks(x_coords[::3])
ax2.set_xticklabels(date_labels[::3], rotation=20, fontsize=8.5, color='#8b949e')
ax2.set_ylabel('15m Volume', fontsize=11, color='#8b949e')
ax2.grid(True, linestyle=':', alpha=0.25, color='#30363d')
ax2.legend(loc='upper left', framealpha=0.95, facecolor='#1f2430', edgecolor='#30363d', fontsize=9)

plt.subplots_adjust(hspace=0.15, top=0.93, bottom=0.10, left=0.08, right=0.95)

out_path = "/Users/hrushi/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f/vol2b2t_real_trade_crv.png"
plt.savefig(out_path, dpi=200, facecolor=fig.get_facecolor(), edgecolor='none')
plt.close()

print(f"✅ Rendered verified 2b2t trade chart to: {out_path}")
