import os
import sys
import csv
import json
import dateutil.parser
import datetime
from io import BytesIO
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# Ensure go-trader path is in sys.path
_GO_TRADER_ROOT = "/root/go-trader"
if _GO_TRADER_ROOT not in sys.path:
    sys.path.insert(0, _GO_TRADER_ROOT)
    
try:
    sys.path.insert(0, os.path.join(_GO_TRADER_ROOT, "platforms/coindcx"))
    from adapter import CoinDCXExchangeAdapter
except ImportError:
    CoinDCXExchangeAdapter = None

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

router = APIRouter(tags=["vol2b2t"])

TRADES_FILE = os.environ.get("VB2_TRADES_FILE", "/root/vol2b2t_coindcx/trades.csv")

# For local mac testing fallback
if not os.path.exists(TRADES_FILE):
    local_fallback = os.path.join(_GO_TRADER_ROOT, "vol2b2t_coindcx", "trades.csv")
    if os.path.exists(local_fallback):
        TRADES_FILE = local_fallback

class TradeInfo(BaseModel):
    id: str
    coin: str
    entry_time: str
    exit_time: Optional[str] = None
    side: str
    entry_price: float
    exit_price: Optional[float] = None
    qty: float
    status: str
    pnl_str: Optional[str] = None
    extra_entry: Optional[str] = None
    extra_exit: Optional[str] = None


SCREENER_FILE = os.environ.get("VB2_SCREENER_FILE", "/root/vol2b2t_coindcx/screener.json")
if not os.path.exists(SCREENER_FILE):
    local_screener_fallback = os.path.join(_GO_TRADER_ROOT, "vol2b2t_coindcx", "screener.json")
    if os.path.exists(local_screener_fallback):
        SCREENER_FILE = local_screener_fallback

class ScreenerInfo(BaseModel):
    coin: str
    avg_vol: float
    curr_vol: float
    mult: float
    status: str
    threshold: float

@router.get("/screener", response_model=List[ScreenerInfo])
def get_screener():
    if not os.path.exists(SCREENER_FILE):
        return []
    try:
        with open(SCREENER_FILE, "r") as f:
            data = json.load(f)
            
        data.sort(key=lambda x: x["mult"], reverse=True)
        
        final_data = []
        added_remaining = 0
        for x in data:
            if x["mult"] >= x["threshold"] or x["status"] != "Scanning":
                final_data.append(x)
            elif added_remaining < 10:
                final_data.append(x)
                added_remaining += 1
                
        # Re-sort final_data so that Watching/In Position coins aren't buried at the bottom 
        # WAIT, if I sort by mult, they are buried. Let's just return it sorted purely by mult 
        # so the table looks perfectly sorted numerically.
        return final_data
    except Exception as e:
        return []

@router.get("/trades", response_model=List[TradeInfo])
def get_trades():
    if not os.path.exists(TRADES_FILE):
        return []
        
    trades_by_coin: Dict[str, List[Dict[str, Any]]] = {}
    
    # Parse CSV
    with open(TRADES_FILE, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 6: continue
            ts, coin, action, side_or_reason, price, qty = row[:6]
            extra = row[6] if len(row) > 6 else ""
            
            if coin not in trades_by_coin:
                trades_by_coin[coin] = []
                
            trades_by_coin[coin].append({
                "ts": ts,
                "action": action,
                "side_or_reason": side_or_reason,
                "price": float(price),
                "qty": float(qty),
                "extra": extra
            })
            
    # Reconstruct trades (match ENTRY to EXIT)
    results = []
    
    for coin, events in trades_by_coin.items():
        events.sort(key=lambda x: dateutil.parser.isoparse(x["ts"]))
        
        current_trade = None
        for ev in events:
            if ev["action"] == "ENTRY":
                if current_trade:
                    results.append(current_trade) # push previous unclosed trade
                
                current_trade = TradeInfo(
                    id=f"{coin}-{ev['ts']}",
                    coin=coin,
                    entry_time=ev["ts"],
                    side=ev["side_or_reason"],
                    entry_price=ev["price"],
                    qty=ev["qty"],
                    status="OPEN",
                    extra_entry=ev["extra"]
                )
            elif ev["action"] == "EXIT" and current_trade:
                current_trade.exit_time = ev["ts"]
                current_trade.exit_price = ev["price"]
                current_trade.status = "CLOSED - " + ev["side_or_reason"]
                current_trade.extra_exit = ev["extra"]
                
                # Extract PnL from extra string e.g. "($+100.0 mark)"
                import re
                m = re.search(r'\(\$([+-]?[\d.]+)', ev["extra"])
                if m:
                    current_trade.pnl_str = m.group(1)
                    
                results.append(current_trade)
                current_trade = None
                
        if current_trade:
            results.append(current_trade)
            
    # Sort newest first
    results.sort(key=lambda x: dateutil.parser.isoparse(x.entry_time), reverse=True)
    return results

_CHART_CACHE: Dict[str, bytes] = {}

@router.get("/chart/{coin}")
def render_vol2b2t_chart(coin: str, entry_ts: str):
    """Generates a visual chart for the given coin around the entry time."""
    cache_key = f"{coin}_{entry_ts}"
    if cache_key in _CHART_CACHE:
        from fastapi import Response
        return Response(content=_CHART_CACHE[cache_key], media_type="image/png")

    if not CoinDCXExchangeAdapter:
        raise HTTPException(status_code=500, detail="CoinDCX Adapter not found")
        
    entry_time = dateutil.parser.isoparse(entry_ts)
    
    # We want to fetch candles around this time. 
    # CoinDCX API typically fetches latest. Let's just fetch latest 60 bars of 15m.
    # If the trade is older than 15 hours, it might fall off, but for now we fetch latest.
    try:
        a = CoinDCXExchangeAdapter()
        base_asset = coin[:-4] if coin.endswith("USDT") else coin
        bars = a.get_ohlcv(base_asset, interval="15m", limit=1000)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    if not bars or len(bars) < 20:
        raise HTTPException(status_code=400, detail="Not enough data")
        
    # parse the trade to get entry levels
    trades = get_trades()
    trade = next((t for t in trades if t.coin == coin and t.entry_time == entry_ts), None)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
        
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas

    # Plotting (Thread-Safe Object-Oriented API)
    fig = Figure(figsize=(12, 8), dpi=100)
    fig.patch.set_facecolor("#111217")
    
    ax1, ax2 = fig.subplots(2, 1, gridspec_kw={'height_ratios': [3, 1]})
    
    for ax in [ax1, ax2]:
        ax.set_facecolor("#111217")
        ax.grid(True, color="#2c2d35", linestyle="--", linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_color("#2c2d35")
        ax.tick_params(colors="white")

    # Data
    x = np.arange(len(bars))
    opens = np.array([b[1] for b in bars])
    highs = np.array([b[2] for b in bars])
    lows = np.array([b[3] for b in bars])
    closes = np.array([b[4] for b in bars])
    vols = np.array([b[5] for b in bars])
    timestamps = [b[0] for b in bars]
    
    # Candlesticks
    for i in range(len(bars)):
        hue = "#10b981" if closes[i] >= opens[i] else "#ef4444"
        ax1.vlines(i, lows[i], highs[i], color=hue, linewidth=1, zorder=1)
        body_bottom = min(opens[i], closes[i])
        body_height = max(abs(closes[i] - opens[i]), 0.0001)
        ax1.add_patch(patches.Rectangle((i - 0.3, body_bottom), 0.6, body_height,
                                        facecolor=hue, edgecolor=hue, zorder=2))
                                        
    # Volumes
    for i in range(len(bars)):
        hue = "#10b981" if closes[i] >= opens[i] else "#ef4444"
        ax2.bar(i, vols[i], color=hue, alpha=0.7)
        
    # Volume 20-SMA and 15x threshold
    vol_sma = [np.mean(vols[max(0, i-20):i]) if i > 0 else 0 for i in range(len(vols))]
    ax2.plot(x, vol_sma, color="blue", linewidth=1, label="20 SMA")
    vol_thresh = [v * 15 for v in vol_sma]
    ax2.plot(x, vol_thresh, color="yellow", linewidth=1, linestyle="--", label="15x Threshold")
    ax2.legend(facecolor="#111217", edgecolor="#2c2d35", labelcolor="white", loc="upper left")
    
    # Mark entry and exit
    entry_idx = -1
    exit_idx = -1
    
    for i, ts in enumerate(timestamps):
        bar_time = datetime.datetime.utcfromtimestamp(ts/1000).replace(tzinfo=datetime.timezone.utc)
        if bar_time <= entry_time:
            entry_idx = i
            
        if trade.exit_time:
            ex_time = dateutil.parser.isoparse(trade.exit_time)
            if bar_time <= ex_time:
                exit_idx = i
                
    if entry_idx >= 0:
        ax1.scatter(entry_idx, trade.entry_price, s=200, color="cyan", zorder=5, marker="^")
        ax1.axhline(trade.entry_price, color="cyan", linestyle="--", alpha=0.5)
        
        # SL parsing
        if trade.extra_entry:
            import re
            m = re.search(r'SL=([\d.]+)', trade.extra_entry)
            if m:
                sl = float(m.group(1))
                ax1.axhline(sl, color="red", linestyle="--", alpha=0.5, label=f"SL {sl}")
                
    if exit_idx >= 0 and trade.exit_price:
        ax1.scatter(exit_idx, trade.exit_price, s=200, color="magenta", zorder=5, marker="v")
        ax1.axhline(trade.exit_price, color="magenta", linestyle="--", alpha=0.5)
        
    # Zoom window based on entry and exit indices
    if entry_idx >= 0:
        start_x = max(0, entry_idx - 40)
        end_x = exit_idx + 20 if exit_idx >= 0 else entry_idx + 40
        end_x = min(len(bars) - 1, end_x)
        if end_x - start_x < 30:  # force min view width
            end_x = min(len(bars) - 1, start_x + 30)
            
        ax1.set_xlim(start_x, end_x)
        ax2.set_xlim(start_x, end_x)
        
    ax1.set_title(f"Vol2b2t: {coin}", color="white", fontsize=14, fontweight="bold")
    fig.tight_layout()
    
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none")
    png_bytes = buf.getvalue()
    
    if len(_CHART_CACHE) > 250:
        _CHART_CACHE.clear()
    _CHART_CACHE[cache_key] = png_bytes
    
    from fastapi import Response
    return Response(content=png_bytes, media_type="image/png")
