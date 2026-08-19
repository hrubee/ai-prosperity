import sys
import os
import json
import requests

sys.path.insert(0, "/root/trading-bot/crypto")
sys.path.insert(0, "/root/trading-bot/crypto/shared_scripts")

from stream_dumpride_coindcx import CoinDCXExchangeAdapter

adapter = CoinDCXExchangeAdapter()
positions = adapter.fetch_positions()

print("=========================================================================================")
print("📊 REAL-TIME PNL BREAKDOWN")
print("=========================================================================================")

if not positions:
    print("No active open positions.")
else:
    for p in positions:
        base = p.get("base")
        mark_px = adapter.get_price(base)
        entry = float(p.get("entry", 0))
        qty = float(p.get("qty", 0))
        side = p.get("side", "short")
        sl = float(p.get("sl_trigger", 0))
        tp = float(p.get("tp_trigger", 0))
        
        if side == "short":
            pnl_usdt = (entry - mark_px) * qty
            pnl_pct = ((entry - mark_px) / entry) * 100.0 if entry > 0 else 0
            max_loss_usdt = (sl - entry) * qty
            max_win_usdt = (entry - tp) * qty
        else:
            pnl_usdt = (mark_px - entry) * qty
            pnl_pct = ((mark_px - entry) / entry) * 100.0 if entry > 0 else 0
            max_loss_usdt = (entry - sl) * qty
            max_win_usdt = (tp - entry) * qty
            
        usdt_inr_rate = 88.0
        pnl_inr = pnl_usdt * usdt_inr_rate
        max_loss_inr = max_loss_usdt * usdt_inr_rate
        max_win_inr = max_win_usdt * usdt_inr_rate
        
        print(f"• ACTIVE POSITION: {base}/USDT ({side.upper()})")
        print(f"   ├─ Position Size   : {qty:,.1f} {base} (${qty * mark_px:,.2f} USDT notional)")
        print(f"   ├─ Entry Price     : ${entry:.6f}")
        print(f"   ├─ Live Mark Price : ${mark_px:.6f}")
        print(f"   ├─ Unrealized PnL  : {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%) / INR {pnl_inr:+.2f}")
        print(f"   ├─ Stop Loss Price : ${sl:.6f} (Max Risk: -{max_loss_usdt:.4f} USDT / -INR {max_loss_inr:.2f})")
        print(f"   └─ Take Profit (TP): ${tp:.6f} (Target  : +{max_win_usdt:.4f} USDT / +INR {max_win_inr:.2f})")

print("=========================================================================================")
