#!/usr/bin/env python3
"""b2_telegram.py — Telegram setup-chart posting for the 2b2t bots.
on entry -> render a candlestick chart with entry/SL/TP marked, sendPhoto, return message_id.
on exit  -> render exit chart and sendPhoto as a REPLY to the entry message (reply_to_message_id) so each trade threads entry->exit.
"""
import io, json, os, urllib.request, urllib.parse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd

API = "https://api.telegram.org/bot%s/%s"

# ── Original 2b2t setup renderer (preserved) ──────────────────────────────────
def _render(base, bias, entry, sl, tp, k, lookback, tf="4h"):
    """k = (o,h,l,c,t) arrays. Render the setup as clean PNG bytes: ~22 candles of context, shaded
    risk (red) + reward (green) zones so the 1:R is visually obvious, price-labelled levels."""
    o, h, l, c, t = k

    # Fetch live 15m candles from CoinDCX and aggregate to forming 30m candle
    if tf == "30m":
        try:
            sym = f"B-{base.upper()}_USDT"
            url = f"https://public.coindcx.com/market_data/candles?pair={sym}&interval=15m&limit=2"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = json.loads(urllib.request.urlopen(req, timeout=3).read())
            
            if resp and isinstance(resp, list):
                resp = sorted(resp, key=lambda x: x["time"], reverse=True)
                import time
                now_ms = int(time.time() * 1000)
                tf_ms = 1800 * 1000
                cur_bucket = (now_ms // tf_ms) * tf_ms
                
                bucket_candles = [r for r in resp if r["time"] >= cur_bucket]
                if bucket_candles:
                    o_live = bucket_candles[-1]["open"]
                    h_live = max([r["high"] for r in bucket_candles])
                    l_live = min([r["low"] for r in bucket_candles])
                    c_live = bucket_candles[0]["close"]
                    
                    o = np.append(o, float(o_live))
                    h = np.append(h, float(h_live))
                    l = np.append(l, float(l_live))
                    c = np.append(c, float(c_live))
                    t = np.append(t, int(cur_bucket))
        except Exception:
            pass

    j = len(c) - 2                                   # signal candle (last closed)
    a = max(0, j - 20); b = min(len(c), j + 4)       # ~22 candles of context
    O, H, L, C = o[a:b], h[a:b], l[a:b], c[a:b]
    
    # Calculate 21 EMA for context
    ema = np.zeros_like(c)
    if len(c) > 0:
        ema[0] = c[0]
        alpha = 2 / (21 + 1)
        for i in range(1, len(c)):
            ema[i] = c[i] * alpha + ema[i-1] * (1 - alpha)
    EMA_plot = ema[a:b]

    up = bias == "long"
    rr = abs(tp - entry) / max(abs(entry - sl), 1e-9)
    fig, ax = plt.subplots(figsize=(9.5, 6.2))
    nx = len(O); xr = nx + 7                          # extra room on the right for level labels

    # risk + reward zones spanning the chart width
    rl, rh = sorted([entry, sl]); ax.axhspan(rl, rh, xmax=(nx + 0.5) / xr, color="#ef5350", alpha=0.10, zorder=0)   # risk
    gl, gh = sorted([entry, tp]); ax.axhspan(gl, gh, xmax=(nx + 0.5) / xr, color="#26a69a", alpha=0.09, zorder=0)   # reward

    # 21 EMA line
    ax.plot(range(nx), EMA_plot, color="#ff9800", lw=1.5, zorder=2, alpha=0.8, label="21 EMA")

    # candles
    for i in range(nx):
        col = "#26a69a" if C[i] >= O[i] else "#ef5350"
        ax.add_line(plt.Line2D([i, i], [L[i], H[i]], color=col, lw=1.4, zorder=3, solid_capstyle="round"))
        lo, hi = sorted([O[i], C[i]])
        ax.add_patch(patches.Rectangle((i - 0.34, lo), 0.68, max(hi - lo, (H[i] - L[i]) * 0.02), facecolor=col, edgecolor=col, zorder=4))

    # shade the same-color run + mark the sweep
    s0 = (j - lookback) - a; s1 = j - a
    ax.axvspan(s0 - 0.45, s1 - 0.55, color=("#ef5350" if up else "#26a69a"), alpha=0.12, zorder=1)
    sx = j - a
    ax.annotate("sweep", xy=(sx, (L[sx] if up else H[sx])), xytext=(-26, (-26 if up else 26)),
                textcoords="offset points", fontsize=9.5, color="#6a1b9a", ha="center",
                va=("top" if up else "bottom"), fontweight="bold",
                arrowprops=dict(arrowstyle="->", color="#6a1b9a", lw=1.4))

    # level lines + right-side price labels
    for y, txt, col in [(tp, "TP  %.6g   +%.0fR" % (tp, round(rr)), "#1b5e20"),
                        (entry, "ENTRY  %.6g" % entry, "#1565c0"),
                        (sl, "SL  %.6g   −1R" % sl, "#b71c1c")]:
        ax.axhline(y, xmax=(nx + 0.5) / xr, ls="--", lw=1.5, color=col, zorder=2)
        ax.annotate(txt, xy=(nx + 0.5, y), xytext=(8, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=10, color=col, fontweight="bold")

    ax.set_title("2b2t   %s   %s   ·   %s" % ("▲ LONG" if up else "▼ SHORT", base, tf),
                 fontsize=14, fontweight="bold", color=("#1b5e20" if up else "#b71c1c"), loc="left")
    ax.set_xlim(-0.5, xr); ax.grid(alpha=0.18, axis="y"); ax.set_xticks([])
    ax.set_ylabel("price"); ax.tick_params(labelsize=9)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=130, facecolor="white"); plt.close(fig)
    buf.seek(0); return buf.read()


# ── High-Precision 8-Decimal Renderer (Finalized) ──────────────────────────────
def fetch_high_precision_candles(coin, interval="15m", limit=50):
    import requests
    symbol = f"{coin.upper().replace('B-', '').replace('_USDT', '').replace('USDT', '')}USDT"
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=5)
        data = r.json()
        if isinstance(data, list) and len(data) >= 10:
            rows = []
            for k in data:
                rows.append({
                    "time": pd.to_datetime(k[0], unit="ms"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "vol": float(k[5]),
                })
            return pd.DataFrame(rows)
    except Exception:
        pass

    pair = f"B-{coin}_USDT" if not coin.startswith("B-") else coin
    url = "https://public.coindcx.com/market_data/candles"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    try:
        req = urllib.request.Request(url + f"?pair={pair}&interval={interval}&limit={limit}", headers={"User-Agent": ua})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if isinstance(resp, list) and len(resp) >= 10:
            rows = []
            for c in resp:
                ts_sec = float(c["time"]) / 1000.0 if float(c["time"]) > 1e10 else float(c["time"])
                rows.append({
                    "time": pd.to_datetime(ts_sec, unit="s"),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "vol": float(c["volume"]),
                })
            return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    except Exception:
        pass
    return None

def render_chart_bytes(coin, entry_px, stop_px, tp_px=None, exit_px=None, exit_reason=None, prev_low=None, tf="4h", title_tag="DumpRide 4H"):
    df = fetch_high_precision_candles(coin, interval=tf, limit=35)
    if df is None or len(df) < 10:
        df = fetch_high_precision_candles(coin, interval="15m", limit=35)
    if df is None or len(df) < 10:
        return None

    n = len(df)
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["vol_avg"] = df["vol"].rolling(20, min_periods=3).mean()

    fig, (ax, ax_vol) = plt.subplots(2, 1, figsize=(13.5, 7.5), gridspec_kw={'height_ratios': [3.6, 1.1]}, sharex=True, dpi=160)
    
    bg_color = '#0b0e14'
    card_color = '#121721'
    grid_color = '#1b2230'
    
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(card_color)
    ax_vol.set_facecolor(card_color)

    ax.grid(True, color=grid_color, linestyle='--', linewidth=0.6, alpha=0.8)
    ax_vol.grid(True, color=grid_color, linestyle='--', linewidth=0.6, alpha=0.8)

    col_up = '#26a69a'
    col_down = '#ef5350'

    # Dynamic tight zoom bounds (focus on candle structure, entry, and stop loss; do NOT stretch for TP)
    p_lows = [df['low'].min(), entry_px]
    p_highs = [df['high'].max(), entry_px]
    if stop_px and stop_px > 0:
        p_highs.append(stop_px)
    if exit_px and exit_px > 0:
        # If exit happened within or near candle history, include it
        p_lows.append(exit_px)
        p_highs.append(exit_px)

    y_min = min(p_lows)
    y_max = max(p_highs)
    pad = (y_max - y_min) * 0.07 if y_max > y_min else 0.001
    y_min -= pad
    y_max += pad

    # Shaded Risk (Red) & Reward (Green) Zones
    if stop_px and stop_px > entry_px:
        ax.axhspan(entry_px, stop_px, color='#ef5350', alpha=0.10, zorder=0)
    if tp_px and tp_px < entry_px:
        ax.axhspan(max(tp_px, y_min), entry_px, color='#26a69a', alpha=0.08, zorder=0)

    # Draw Candlesticks & Volume Bars
    for i, row in df.iterrows():
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        is_green = c >= o
        color = col_up if is_green else col_down
        
        # Highlight last closed bar if volume surge
        is_signal_bar = (i == n - 1)
        
        ax.vlines(i, l, h, color=color, linewidth=1.3, alpha=0.95, zorder=3)

        body_bottom = min(o, c)
        body_height = max(abs(c - o), (y_max - y_min) * 0.008)
        
        edge_col = '#ffd700' if is_signal_bar else color
        edge_lw = 1.4 if is_signal_bar else 0.5
        rect = patches.Rectangle(
            (i - 0.36, body_bottom), 0.72, body_height,
            facecolor=color, edgecolor=edge_col, linewidth=edge_lw, alpha=0.95, zorder=4
        )
        ax.add_patch(rect)

        vol_height = row['vol']
        rect_vol = patches.Rectangle(
            (i - 0.36, 0), 0.72, vol_height,
            facecolor=color, edgecolor=edge_col, alpha=0.80, linewidth=edge_lw, zorder=4
        )
        ax_vol.add_patch(rect_vol)

    # 21 EMA & Volume Baseline
    ax.plot(df.index, df['ema21'], color='#ff9800', label='21 EMA', linewidth=1.5, alpha=0.9, zorder=5)
    ax_vol.plot(df.index, df['vol_avg'], color='#ff9800', label='20-bar Vol Baseline', linewidth=1.3, alpha=0.85, zorder=5)
    ax_vol.set_ylim(0, df['vol'].max() * 1.20)
    ax.set_ylim(y_min, y_max)

    # Horizontal Price Level Lines & Pill Tags
    xr = n + 6.5
    ax.set_xlim(-0.8, xr)

    # ENTRY
    ax.axhline(y=entry_px, color='#00e5ff', linestyle='--', linewidth=1.5, alpha=0.9, zorder=6)
    ax.text(n + 0.6, entry_px, f" ENTRY {entry_px:.5g} ", color='#0b0e14', fontsize=9, fontweight='bold',
            va='center', ha='left', bbox=dict(boxstyle='round,pad=0.32', facecolor='#00e5ff', edgecolor='none'), zorder=7)

    # STOP LOSS
    if stop_px and stop_px > 0:
        ax.axhline(y=stop_px, color='#ff2d55', linestyle=':', linewidth=1.5, alpha=0.9, zorder=6)
        sl_pct = ((stop_px - entry_px) / entry_px) * 100
        ax.text(n + 0.6, stop_px, f" SL {stop_px:.5g} (+{sl_pct:.1f}%) ", color='#ffffff', fontsize=9, fontweight='bold',
                va='center', ha='left', bbox=dict(boxstyle='round,pad=0.32', facecolor='#d32f2f', edgecolor='none'), zorder=7)

    # TAKE PROFIT (Render if within visible chart bounds)
    if tp_px and tp_px > 0:
        tp_pct = ((entry_px - tp_px) / entry_px) * 100
        if tp_px >= y_min:
            ax.axhline(y=tp_px, color='#00e676', linestyle='-.', linewidth=1.5, alpha=0.9, zorder=6)
            ax.text(n + 0.6, tp_px, f" TP 1:2 {tp_px:.5g} (-{tp_pct:.1f}%) ", color='#ffffff', fontsize=9, fontweight='bold',
                    va='center', ha='left', bbox=dict(boxstyle='round,pad=0.32', facecolor='#2e7d32', edgecolor='none'), zorder=7)

    # EXIT (if closed)
    if exit_px and exit_px > 0:
        exit_col = '#00e676' if exit_px < entry_px else '#ff2d55'
        ax.axhline(y=exit_px, color=exit_col, linestyle='-', linewidth=1.8, alpha=0.95, zorder=6)
        exit_text = f" EXIT {exit_px:.5g} "
        if exit_reason: exit_text += f"({exit_reason}) "
        ax.text(n + 0.6, exit_px, exit_text, color='#ffffff', fontsize=9, fontweight='bold',
                va='center', ha='left', bbox=dict(boxstyle='round,pad=0.32', facecolor=exit_col, edgecolor='none'), zorder=7)

    # Header Titles
    status_str = f"CLOSED ({exit_reason})" if exit_px else "ACTIVE IN-POSITION"
    pnl_str = ""
    if exit_px:
        pnl = ((entry_px - exit_px) / entry_px) * 100
        pnl_str = f"  |  Realized PnL: {pnl:+.2f}%"

    title_main = f"[SHORT] {coin}/USDT — {tf.upper()} {title_tag} [{status_str}]"
    title_sub = f"Entry: {entry_px:.5g}   SL: {stop_px:.5g}" + (f"   TP: {tp_px:.5g}" if tp_px else "") + pnl_str

    ax.set_title(f"{title_main}\n{title_sub}", fontsize=12.5, color='#ffffff', fontweight='bold', pad=12, loc='left')
    ax.set_ylabel("Price (USDT)", fontsize=9.5, color='#8b949e')
    ax_vol.set_ylabel("Volume", fontsize=9.5, color='#8b949e')

    ax.legend(facecolor=card_color, edgecolor=grid_color, labelcolor='#c9d1d9', loc='upper left', fontsize=8.5)
    ax_vol.legend(facecolor=card_color, edgecolor=grid_color, labelcolor='#c9d1d9', loc='upper left', fontsize=8.5)

    for a in [ax, ax_vol]:
        for spine in a.spines.values():
            spine.set_color(grid_color)
        a.tick_params(colors='#8b949e', labelsize=8.5)

    # Date + Time formatting on X axis
    step = max(1, n // 7)
    tick_positions = list(range(0, n, step))
    tick_labels = [df['time'].iloc[i].strftime('%d %b %H:%M') for i in tick_positions]
    ax_vol.set_xticks(tick_positions)
    ax_vol.set_xticklabels(tick_labels, color='#8b949e', fontsize=8.5)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def _multipart(fields, files):
    bnd = "----b2tgboundary" + str(np.random.randint(100000, 999999))
    out = b""
    for k, v in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (bnd, k, v)).encode()
    for k, (fn, data) in files.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n" % (bnd, k, fn)).encode()
        out += data + b"\r\n"
    out += ("--%s--\r\n" % bnd).encode()
    return out, bnd

def post_entry(token, chat, base, bias, entry, sl, tp, risk_pct, k=None, account="CoinDCX Live", lookback=3, tf="4h", prev_low=None):
    """Render + sendPhoto. Returns the Telegram message_id (or dict of msg_ids) to reply to on exit."""
    chats = [c.strip() for c in str(chat).split(",") if c.strip()]
    if not chats:
        return None
    try:
        tag_name = "DumpRide 4H" if "DUMPRIDE" in str(account).upper() else ("FibVOL" if "FIBVOL" in str(account).upper() else "2b2t")
        png = render_chart_bytes(base, entry, sl, tp_px=tp, prev_low=prev_low, tf=tf, title_tag=tag_name)
        if not png and k is not None:
            png = _render(base, bias, entry, sl, tp, k, lookback, tf)

        emoji = "📉" if bias == "short" else "🚀"
        tag = "DUMPRIDE 4H" if "DUMPRIDE" in str(account).upper() else ("FIBVOL" if "FIBVOL" in str(account).upper() else "2B2T")
        cap = (f"{emoji} {tag} ENTRY — {base}/USDT ({account})\n"
               f"Entry: {entry:.6g} | SL: {sl:.6g} | TP: {tp:.6g}\n"
               f"Risk: {risk_pct*100:.2f}% | TF: {tf}")
        
        msg_ids = {}
        for cid in chats:
            try:
                fields = {"chat_id": str(cid), "caption": cap}
                files = {"photo": ("setup.png", png)} if png else {}
                if not png:
                    data = urllib.parse.urlencode({"chat_id": str(cid), "text": cap}).encode()
                    req = urllib.request.Request(API % (token, "sendMessage"), data=data)
                else:
                    body, bnd = _multipart(fields, files)
                    req = urllib.request.Request(API % (token, "sendPhoto"), data=body,
                                                 headers={"Content-Type": "multipart/form-data; boundary=%s" % bnd})
                resp = json.load(urllib.request.urlopen(req, timeout=15))
                if resp.get("ok"):
                    msg_ids[str(cid)] = resp.get("result", {}).get("message_id")
            except Exception as ce:
                print(f"Error posting entry to chat {cid}: {ce}")
                
        return msg_ids if len(chats) > 1 else (list(msg_ids.values())[0] if msg_ids else None)
    except Exception as e:
        print("Telegram post_entry error:", e)
        return None

def post_exit(token, chat, reply_to_msg_id, base, bias, reason, exit_px, r, pnl, account="CoinDCX Live", entry=0, sl=0, tp=0, prev_low=None, tf="4h"):
    """Render exit chart & sendPhoto as a direct REPLY to entry message."""
    chats = [c.strip() for c in str(chat).split(",") if c.strip()]
    if not chats:
        return False
    try:
        win = r > 0
        emoji = "✅" if win else "❌"
        txt = (f"{emoji} EXIT {reason} — {base}/USDT ({account})\n"
               f"Exit: {exit_px:.6g} | Return: {r:+.2f}R | PnL: ${pnl:+.2f}")
        
        tag_name = "DumpRide 4H" if "DUMPRIDE" in str(account).upper() else ("FibVOL" if "FIBVOL" in str(account).upper() else "2b2t")
        png = render_chart_bytes(base, entry, sl, tp_px=tp, exit_px=exit_px, exit_reason=reason, prev_low=prev_low, tf=tf, title_tag=tag_name) if entry > 0 else None
        
        success = False
        for cid in chats:
            try:
                rep_id = None
                if isinstance(reply_to_msg_id, dict):
                    rep_id = reply_to_msg_id.get(str(cid))
                elif isinstance(reply_to_msg_id, (int, str)):
                    rep_id = reply_to_msg_id
                    
                fields = {"chat_id": str(cid), "caption": txt}
                if rep_id:
                    fields["reply_to_message_id"] = str(rep_id)

                if png:
                    files = {"photo": ("exit.png", png)}
                    body, bnd = _multipart(fields, files)
                    req = urllib.request.Request(API % (token, "sendPhoto"), data=body,
                                                 headers={"Content-Type": "multipart/form-data; boundary=%s" % bnd})
                else:
                    data_dict = {"chat_id": str(cid), "text": txt}
                    if rep_id: data_dict["reply_to_message_id"] = str(rep_id)
                    data = urllib.parse.urlencode(data_dict).encode()
                    req = urllib.request.Request(API % (token, "sendMessage"), data=data)

                resp = json.load(urllib.request.urlopen(req, timeout=15))
                if resp.get("ok"):
                    success = True
            except Exception as ce:
                print(f"Error posting exit to chat {cid}: {ce}")
                
        return success
    except Exception as e:
        print("Telegram post_exit error:", e)
        return False
