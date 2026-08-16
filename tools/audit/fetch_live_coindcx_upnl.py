#!/usr/bin/env python3
"""fetch_live_coindcx_upnl.py — Fetch active CoinDCX positions and live unrealized PnL (uPnL).
"""
import sys
import os
sys.path.append(".")

def load_env():
    for envf in ["/root/go-trader/.env", ".env"]:
        try:
            if os.path.exists(envf):
                for ln in open(envf):
                    ln = ln.strip()
                    if ln and "=" in ln and not ln.startswith("#"):
                        k, v = ln.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass

load_env()
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

def main():
    A = CoinDCXExchangeAdapter()
    print("==========================================================================================================")
    print("📊 COINDCX LIVE OPEN POSITIONS & UNREALIZED PNL (uPnL)")
    print("==========================================================================================================")

    try:
        positions = A.fetch_positions()
        if not positions:
            print("No open positions found on CoinDCX account right now.")
            return

        total_upnl_usd = 0.0
        inr_rate = A.inr_per_usdt or 86.0

        print(f"{'#':<3} {'Symbol':<10} {'Side':<6} {'Qty':<10} {'Entry Px':<12} {'Mark Px':<12} {'uPnL ($)':<12} {'uPnL (₹)':<12} {'SL Level'}")
        print("-" * 105)

        for i, p in enumerate(positions, 1):
            sym = p.get("base", "UNKNOWN")
            side = p.get("side", "long").upper()
            qty = float(p.get("qty") or 0.0)
            entry_px = float(p.get("entry") or 0.0)
            mark_px = float(p.get("price") or A.get_price(sym) or entry_px)
            sl_px = float(p.get("sl_trigger") or 0.0)

            sign = 1.0 if side == "LONG" else -1.0
            upnl_usd = (mark_px - entry_px) * qty * sign
            upnl_inr = upnl_usd * inr_rate
            total_upnl_usd += upnl_usd

            sl_str = f"{sl_px:.6g}" if sl_px > 0 else "None"
            print(f"{i:<3} {sym:<10} {side:<6} {qty:<10.4f} {entry_px:<12.6g} {mark_px:<12.6g} ${upnl_usd:<11.2f} ₹{upnl_inr:<11.2f} {sl_str}")

        total_upnl_inr = total_upnl_usd * inr_rate
        print("-" * 105)
        print(f"💰 TOTAL UNREALIZED PNL: ${total_upnl_usd:+.2f} USD | ₹{total_upnl_inr:+.2f} INR")

    except Exception as e:
        print(f"Error fetching live positions: {e}")

if __name__ == "__main__":
    main()
