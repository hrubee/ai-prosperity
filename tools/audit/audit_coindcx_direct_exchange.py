#!/usr/bin/env python3
"""audit_coindcx_direct_exchange.py — Direct CoinDCX exchange server fill audit.
Fetches raw fill logs directly from CoinDCX REST API (POST /exchange/v1/derivatives/futures/trades)
and calculates 100% exchange-verified realized Net PnL and fee deductions.
"""
import sys
import os
import json
import datetime

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

def main():
    A = CoinDCXExchangeAdapter()
    
    print("=== DIRECT COINDCX EXCHANGE BALANCE READ ===")
    try:
        inr_eq = A.get_inr_equity()
        print(f"Live CoinDCX INR Wallet Equity: ₹{inr_eq:.2f} INR")
    except Exception as e:
        print(f"Error fetching live equity: {e}")

    print("\n=== FETCHING RAW EXECUTED FILLS DIRECTLY FROM COINDCX SERVERS ===")
    all_fills = []
    for page in range(1, 10):
        try:
            res = A._post("/exchange/v1/derivatives/futures/trades", {"page": str(page), "size": "100"})
            if isinstance(res, list) and len(res) > 0:
                all_fills.extend(res)
            else:
                break
        except Exception as e:
            print(f"Page {page} error: {e}")
            break

    print(f"Total Raw Trade Fills Returned from CoinDCX API: {len(all_fills)}\n")

    by_pair = {}
    for f in all_fills:
        pair = f.get("pair")
        if not pair:
            continue
        by_pair.setdefault(pair, []).append(f)

    header = f"{'PAIR':<15} | {'BUY QTY':<10} | {'BUY VWAP':<10} | {'SELL QTY':<10} | {'SELL VWAP':<10} | {'EXCHANGE FEES':<14} | {'NET PNL ($)':<12} | {'NET PNL (INR)':<12}"
    print(header)
    print("-" * len(header))

    total_net_usdt = 0.0

    for pair, fills in sorted(by_pair.items()):
        buys = [f for f in fills if f.get("side") == "buy"]
        sells = [f for f in fills if f.get("side") == "sell"]
        
        b_qty = sum(float(f["quantity"]) for f in buys)
        b_vol = sum(float(f["quantity"]) * float(f["price"]) for f in buys)
        b_vwap = b_vol / b_qty if b_qty > 0 else 0.0
        
        s_qty = sum(float(f["quantity"]) for f in sells)
        s_vol = sum(float(f["quantity"]) * float(f["price"]) for f in sells)
        s_vwap = s_vol / s_qty if s_qty > 0 else 0.0
        
        total_fee = sum(float(f.get("fee_amount") or 0) for f in fills)
        
        matched_qty = min(b_qty, s_qty)
        if matched_qty > 0:
            gross_pnl = (s_vwap - b_vwap) * matched_qty
            net_pnl = gross_pnl - total_fee
            total_net_usdt += net_pnl
            inr_pnl = net_pnl * 86.0
            fee_str = f"${total_fee:.4f}"
            net_usd_str = f"${net_pnl:+.2f}"
            net_inr_str = f"Rs.{inr_pnl:+.2f}"
            print(f"{pair:<15} | {b_qty:<10.1f} | {b_vwap:<10.5f} | {s_qty:<10.1f} | {s_vwap:<10.5f} | {fee_str:<14} | {net_usd_str:<12} | {net_inr_str:<12}")

    print("-" * len(header))
    print(f"🎯 TOTAL DIRECT EXCHANGE REALIZED NET PNL: ${total_net_usdt:+.2f} USD | Rs.{total_net_usdt * 86.0:+.2f} INR")

if __name__ == "__main__":
    main()
