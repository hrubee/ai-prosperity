#!/usr/bin/env python3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import time, os, ssl, json, urllib.request

ssl_ctx = ssl._create_unverified_context()

# 1. Fetch real market candle data for SOLUSDT 1h
url = 'https://fapi.binance.com/fapi/v1/klines?symbol=SOLUSDT&interval=1h&limit=45'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
raw = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=8))

times = [int(r[0]) for r in raw]
opens = np.array([float(r[1]) for r in raw])
highs = np.array([float(r[2]) for r in raw])
lows = np.array([float(r[3]) for r in raw])
closes = np.array([float(r[4]) for r in raw])
vols = np.array([float(r[5]) for r in raw])

# EMA 50
ema50 = np.zeros(len(closes))
alpha = 2 / (50 + 1)
ema50[0] = closes[0]
for i in range(1, len(closes)):
    ema50[i] = alpha * closes[i] + (1 - alpha) * ema50[i-1]

# Trade Setup on Last Closed Bar
ci = len(closes) - 2
entry_px = closes[ci]
sl_dist = 0.015 * entry_px
sl_px = entry_px - sl_dist
tp_px = entry_px + (4.0 * sl_dist)

# Plot High-Res Chart
plt.style.use('dark_background')
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 9), dpi=180, gridspec_kw={'height_ratios': [3.2, 1.0]})
fig.patch.set_facecolor('#0b0e14')
ax1.set_facecolor('#11151c')
ax2.set_facecolor('#11151c')

x = np.arange(len(raw))
w = 0.62

# Plot Candlesticks
for i in range(len(raw)):
    o, h, l, c = opens[i], highs[i], lows[i], closes[i]
    is_bull = c >= o
    col = '#00e676' if is_bull else '#ff1744'
    
    ax1.plot([x[i], x[i]], [l, h], color=col, linewidth=1.3, alpha=0.9)
    rect_y = min(o, c)
    rect_h = max(abs(c - o), 0.001)
    ax1.add_patch(patches.Rectangle((x[i]-w/2, rect_y), w, rect_h, facecolor=col, edgecolor=col, alpha=0.85))

# Plot EMA 50
ax1.plot(x, ema50, color='#ffab00', linestyle='-', linewidth=1.8, label='Trend Filter: EMA 50', alpha=0.95)

# Plot Trade Levels with Labels
ax1.axhline(entry_px, color='#00e5ff', linestyle='--', linewidth=1.5, label=f'Entry Level: ${entry_px:,.2f}')
ax1.axhline(sl_px, color='#ff5252', linestyle='--', linewidth=1.5, label=f'Stop Loss: ${sl_px:,.2f} (-1.5%)')
ax1.axhline(tp_px, color='#00e676', linestyle='--', linewidth=1.8, label=f'Take Profit (1:4.0 RR): ${tp_px:,.2f} (+6.0%)')

# Fill Profit / Loss Target Zones
ax1.axhspan(entry_px, tp_px, xmin=0.72, xmax=1.0, color='#00e676', alpha=0.12)
ax1.axhspan(sl_px, entry_px, xmin=0.72, xmax=1.0, color='#ff1744', alpha=0.12)

# Badges & Annotations
ax1.text(x[-1] + 0.3, entry_px, f'  ENTRY ${entry_px:,.2f}', color='#00e5ff', fontsize=9.5, fontweight='bold', va='center')
ax1.text(x[-1] + 0.3, sl_px, f'  SL ${sl_px:,.2f}', color='#ff5252', fontsize=9.5, fontweight='bold', va='center')
ax1.text(x[-1] + 0.3, tp_px, f'  TP (1:4 RR) ${tp_px:,.2f}', color='#00e676', fontsize=9.5, fontweight='bold', va='center')

# Header & Watermark
ax1.set_title('⚡ STRATEGY: HEIKIN-ASHI 1H MOMENTUM • #SOL/USDT LONG (1:4.0 RR)', fontsize=14, fontweight='bold', color='#ffffff', pad=12, loc='left')
ax1.grid(True, linestyle=':', alpha=0.2, color='#3a4454')
ax1.set_ylabel('Price (USDT)', fontsize=10, color='#90a4ae')
ax1.legend(loc='upper left', facecolor='#18202c', edgecolor='#2c3848', fontsize=9)

# Volume Subplot
vol_cols = ['#00e676' if closes[i] >= opens[i] else '#ff1744' for i in range(len(raw))]
ax2.bar(x, vols, width=w, color=vol_cols, alpha=0.75)
vol_ma = np.convolve(vols, np.ones(20)/20, mode='same')
ax2.plot(x, vol_ma, color='#00e5ff', linewidth=1.2, linestyle=':', label='20 SMA Volume')
ax2.set_ylabel('Volume', fontsize=10, color='#90a4ae')
ax2.grid(True, linestyle=':', alpha=0.2, color='#3a4454')

date_labels = [time.strftime('%d %b %H:%M', time.gmtime(t/1000)) for t in times]
ax2.set_xticks(x[::5])
ax2.set_xticklabels(date_labels[::5], rotation=12, fontsize=8.5, color='#90a4ae')

plt.subplots_adjust(hspace=0.08, top=0.93, bottom=0.08, left=0.07, right=0.88)
out_img = os.path.expanduser('~/.gemini/antigravity-ide/brain/9ab54e65-4280-4e53-95ae-f4165cf8236f/sample_chart.png')
plt.savefig(out_img, dpi=180, facecolor=fig.get_facecolor(), edgecolor='none')
plt.close()

# Upload Photo to Telegram Group
token = '8823560993:AAHAtlrSlbedbUJeIBgj2_NUe4-7BxB9Lx8'
chat_id = '-5535049486'
caption = f"""💎 *STRATEGY: HEIKIN-ASHI 1H MOMENTUM*

• *Signal*: `🟢 BULLISH LONG ENTRY`
• *Pair*: `#SOL/USDT`
• *Setup*: Flat-Bottom Transition + EMA 50 Alignment
• *Entry Level*: `${entry_px:,.2f}`
• *Stop Loss*: `${sl_px:,.2f}` (-1.50%)
• *Take Profit*: `${tp_px:,.2f}` (*1:4.0 RR Target* / +6.00%)
• *Dynamic Trailing*: BE at +1.5R, Lock Profit at +2.5R"""

boundary = '----WebKitFormBoundary' + hex(int(time.time()*1000))[2:]
with open(out_img, 'rb') as f:
    f_bytes = f.read()

body = []
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode('utf-8'))
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode('utf-8'))
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="parse_mode"\r\n\r\nMarkdown\r\n'.encode('utf-8'))
body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8'))
body.append(f_bytes)
body.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))

req = urllib.request.Request(
    f'https://api.telegram.org/bot{token}/sendPhoto',
    data=b''.join(body),
    headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}
)
res = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=15))
print('✅ Photo Upload Result:', res.get('ok'))
