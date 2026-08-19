#!/usr/bin/env python3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.gridspec as gridspec
import numpy as np
import time, os, ssl, json, urllib.request

ssl_ctx = ssl._create_unverified_context()

# 1. Fetch real market candle data for SOLUSDT 15m
url = 'https://fapi.binance.com/fapi/v1/klines?symbol=SOLUSDT&interval=15m&limit=40'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
raw = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=8))

times = [int(r[0]) for r in raw]
opens = np.array([float(r[1]) for r in raw])
highs = np.array([float(r[2]) for r in raw])
lows = np.array([float(r[3]) for r in raw])
closes = np.array([float(r[4]) for r in raw])
vols = np.array([float(r[5]) for r in raw])

# Heikin Ashi Calculation
ha_close = (opens + highs + lows + closes) / 4.0
ha_open = np.zeros(len(closes))
ha_open[0] = (opens[0] + closes[0]) / 2.0
for i in range(1, len(closes)):
    ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))

# Trade Setup on Last Closed Bar
ci = len(closes) - 2
entry_px = closes[ci]
sl_dist = 0.012 * entry_px
sl_px = entry_px - sl_dist
tp_px = entry_px + (4.0 * sl_dist)

# Plot Side-by-Side Dual-Engine Dark-Mode Chart
plt.style.use('dark_background')
fig = plt.figure(figsize=(22, 10), dpi=180)
fig.patch.set_facecolor('#0a0d13')

gs = gridspec.GridSpec(2, 2, height_ratios=[3.5, 1.0], width_ratios=[1.0, 1.0], hspace=0.08, wspace=0.10)

ax_raw_price = fig.add_subplot(gs[0, 0])
ax_raw_vol   = fig.add_subplot(gs[1, 0], sharex=ax_raw_price)

ax_ha_price  = fig.add_subplot(gs[0, 1])
ax_ha_vol    = fig.add_subplot(gs[1, 1], sharex=ax_ha_price)

for ax in [ax_raw_price, ax_raw_vol, ax_ha_price, ax_ha_vol]:
    ax.set_facecolor('#10141d')
    ax.grid(True, linestyle=':', alpha=0.22, color='#2c3848')

x = np.arange(len(raw))
w = 0.62

# ─────────────────────────────────────────────────────────────────────────────
# 1. LEFT PANEL: STANDARD RAW CANDLESTICKS (15-MINUTE)
# ─────────────────────────────────────────────────────────────────────────────
for i in range(len(raw)):
    o, h, l, c = opens[i], highs[i], lows[i], closes[i]
    col = '#00e676' if c >= o else '#ff1744'
    ax_raw_price.plot([x[i], x[i]], [l, h], color=col, linewidth=1.2, alpha=0.85)
    rect_y = min(o, c)
    rect_h = max(abs(c - o), 0.001)
    ax_raw_price.add_patch(patches.Rectangle((x[i]-w/2, rect_y), w, rect_h, facecolor=col, edgecolor=col, alpha=0.85))

# Trade Levels on Raw
ax_raw_price.axhline(entry_px, color='#00e5ff', linestyle='--', linewidth=1.4, label=f'Entry: ${entry_px:,.2f}')
ax_raw_price.axhline(sl_px, color='#ff5252', linestyle='--', linewidth=1.4, label=f'Stop Loss: ${sl_px:,.2f} (-1.2%)')
ax_raw_price.axhline(tp_px, color='#00e676', linestyle='--', linewidth=1.6, label=f'TP (1:4 RR): ${tp_px:,.2f} (+4.8%)')

ax_raw_price.axhspan(entry_px, tp_px, xmin=0.70, xmax=1.0, color='#00e676', alpha=0.12)
ax_raw_price.axhspan(sl_px, entry_px, xmin=0.70, xmax=1.0, color='#ff1744', alpha=0.12)

ax_raw_price.set_title('RAW CANDLESTICKS (15-MINUTE) — PRICE ACTION & S/R', fontsize=12, fontweight='bold', color='#e2e8f0', pad=10, loc='left')
ax_raw_price.set_ylabel('Price (USDT)', fontsize=9.5, color='#90a4ae')
ax_raw_price.legend(loc='upper left', facecolor='#171d27', edgecolor='#2c3848', fontsize=8.5)

# Raw Volume
vol_cols = ['#00e676' if closes[i] >= opens[i] else '#ff1744' for i in range(len(raw))]
ax_raw_vol.bar(x, vols, width=w, color=vol_cols, alpha=0.75)
vol_ma = np.convolve(vols, np.ones(20)/20, mode='same')
ax_raw_vol.plot(x, vol_ma, color='#00e5ff', linewidth=1.2, linestyle=':', label='20 SMA Vol')
ax_raw_vol.set_ylabel('Volume', fontsize=9.5, color='#90a4ae')

# ─────────────────────────────────────────────────────────────────────────────
# 2. RIGHT PANEL: HEIKIN-ASHI (15-MINUTE)
# ─────────────────────────────────────────────────────────────────────────────
for i in range(len(raw)):
    ho, hh, hl, hc = ha_open[i], ha_high[i], ha_low[i], ha_close[i]
    is_ha_green = hc >= ho
    col = '#00e676' if is_ha_green else '#ff1744'
    
    # Wick line
    ax_ha_price.plot([x[i], x[i]], [hl, hh], color=col, linewidth=1.2, alpha=0.85)
    rect_y = min(ho, hc)
    rect_h = max(abs(hc - ho), 0.001)
    ax_ha_price.add_patch(patches.Rectangle((x[i]-w/2, rect_y), w, rect_h, facecolor=col, edgecolor=col, alpha=0.85))
    
    # Highlight Flat Bottom
    if is_ha_green and ((ho - hl) / max(hh - hl, 0.001) <= 0.05):
        ax_ha_price.scatter(x[i], hl - (0.002 * ho), color='#00e5ff', marker='^', s=45, zorder=5)

# Trade Levels on HA
ax_ha_price.axhline(entry_px, color='#00e5ff', linestyle='--', linewidth=1.4, label=f'Entry: ${entry_px:,.2f}')
ax_ha_price.axhline(sl_px, color='#ff5252', linestyle='--', linewidth=1.4, label=f'Stop Loss: ${sl_px:,.2f}')
ax_ha_price.axhline(tp_px, color='#00e676', linestyle='--', linewidth=1.6, label=f'Take Profit (1:4 RR): ${tp_px:,.2f}')

ax_ha_price.axhspan(entry_px, tp_px, xmin=0.70, xmax=1.0, color='#00e676', alpha=0.12)
ax_ha_price.axhspan(sl_px, entry_px, xmin=0.70, xmax=1.0, color='#ff1744', alpha=0.12)

ax_ha_price.set_title('HEIKIN-ASHI (15-MINUTE) — PURE MOMENTUM CONFIRMATION', fontsize=12, fontweight='bold', color='#e2e8f0', pad=10, loc='left')
ax_ha_price.set_ylabel('HA Price (USDT)', fontsize=9.5, color='#90a4ae')
ax_ha_price.legend(loc='upper left', facecolor='#171d27', edgecolor='#2c3848', fontsize=8.5)

# HA Volume
ha_vol_cols = ['#00e676' if ha_close[i] >= ha_open[i] else '#ff1744' for i in range(len(raw))]
ax_ha_vol.bar(x, vols, width=w, color=ha_vol_cols, alpha=0.75)
ax_ha_vol.plot(x, vol_ma, color='#00e5ff', linewidth=1.2, linestyle=':', label='20 SMA Vol')
ax_ha_vol.set_ylabel('Volume', fontsize=9.5, color='#90a4ae')

# Time axis formatting
date_labels = [time.strftime('%d %b %H:%M', time.gmtime(t/1000)) for t in times]
for ax in [ax_raw_vol, ax_ha_vol]:
    ax.set_xticks(x[::5])
    ax.set_xticklabels(date_labels[::5], rotation=12, fontsize=8.5, color='#90a4ae')

# Super Title
fig.suptitle('STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM • #SOL/USDT LONG (1:4.0 RR)', fontsize=15, fontweight='bold', color='#ffffff', y=0.98)

plt.subplots_adjust(top=0.92, bottom=0.08, left=0.05, right=0.96)
out_img = os.path.expanduser('~/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f/side_by_side_chart_15m.png')
plt.savefig(out_img, dpi=180, facecolor=fig.get_facecolor(), edgecolor='none')
plt.close()

# Upload Photo to Telegram Group
token = '8823560993:AAHAtlrSlbedbUJeIBgj2_NUe4-7BxB9Lx8'
chat_id = '-5535049486'
caption = f"""💎 *STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM*

• *Signal*: `🟢 BULLISH LONG ENTRY`
• *Pair*: `#SOL/USDT`
• *Timeframe*: `15-Minute Candlesticks`
• *Setup*: Pure Flat-Bottom Momentum (No EMA Filter)
• *Side-by-Side*: Left = 15M Raw Candles | Right = 15M Heikin-Ashi
• *Entry Level*: `${entry_px:,.2f}`
• *Stop Loss*: `${sl_px:,.2f}` (-1.20%)
• *Take Profit*: `${tp_px:,.2f}` (*1:4.0 RR Target* / +4.80%)
• *Trailing Rules*: BE at +1.5R, Lock Profit at +2.5R"""

boundary = '----WebKitFormBoundary' + hex(int(time.time()*1000))[2:]
with open(out_img, 'rb') as f:
    f_bytes = f.read()

body = []
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode('utf-8'))
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode('utf-8'))
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="parse_mode"\r\n\r\nMarkdown\r\n'.encode('utf-8'))
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="side_by_side_15m.png"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8'))
body.append(f_bytes)
body.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))

req = urllib.request.Request(
    f'https://api.telegram.org/bot{token}/sendPhoto',
    data=b''.join(body),
    headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}
)
res = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=15))
print('✅ 15-Minute Photo Upload Result:', res.get('ok'))
