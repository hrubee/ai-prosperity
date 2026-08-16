#!/usr/bin/env python3
import sys, os, json

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

print("=== PRIMARY ACCOUNT (ACCOUNT 1) EXCHANGE AUDIT ===", flush=True)

try:
    inr_eq = A.get_inr_equity()
    print(f"Live CoinDCX Wallet Equity: ₹{inr_eq:,.2f} INR", flush=True)
except Exception as e:
    print(f"Error fetching equity: {e}", flush=True)

all_trades = []
for page in range(1, 20):
    try:
        res = A._post("/exchange/v1/derivatives/futures/trades", {"page": str(page), "size": "100"})
        if isinstance(res, list) and len(res) > 0:
            all_trades.extend(res)
        else:
            break
    except Exception as e:
        print(f"Page {page} error: {e}", flush=True)
        break

print(f"Total Trade Fills Returned for Primary Account: {len(all_trades)}", flush=True)

if not all_trades:
    print("No trades found.")
    sys.exit(0)

by_pair = {}
for t in all_trades:
    pair = t.get("pair")
    if pair:
        by_pair.setdefault(pair, []).append(t)

tot_gross_win = 0.0
tot_gross_loss = 0.0
tot_fees = 0.0
wins = 0
losses = 0
breakevens = 0

for pair, fills in sorted(by_pair.items()):
    buys = [f for f in fills if f.get("side") == "buy"]
    sells = [f for f in fills if f.get("side") == "sell"]
    
    b_qty = sum(float(f.get("quantity", 0)) for f in buys)
    b_vol = sum(float(f.get("quantity", 0)) * float(f.get("price", 0)) for f in buys)
    b_vwap = b_vol / b_qty if b_qty > 0 else 0.0
    
    s_qty = sum(float(f.get("quantity", 0)) for f in sells)
    s_vol = sum(float(f.get("quantity", 0)) * float(f.get("price", 0)) for f in sells)
    s_vwap = s_vol / s_qty if s_qty > 0 else 0.0
    
    fee_usd = sum(float(f.get("fee_amount", 0) or f.get("fee", 0)) for f in fills)
    matched_qty = min(b_qty, s_qty)
    if matched_qty <= 0: continue
    
    gross_pnl = (s_vwap - b_vwap) * matched_qty
    net_pnl = gross_pnl - fee_usd
    tot_fees += fee_usd
    
    if net_pnl > 0.05:
        wins += 1
        tot_gross_win += net_pnl
    elif net_pnl < -0.05:
        losses += 1
        tot_gross_loss += abs(net_pnl)
    else:
        breakevens += 1

tot_net_usd = tot_gross_win - tot_gross_loss
tot_net_inr = tot_net_usd * 86.0
total_pairs = wins + losses + breakevens
win_rate = (wins / total_pairs * 100) if total_pairs > 0 else 0.0

print("\n==================================================")
print("📊 PRIMARY ACCOUNT (ACCOUNT 1) TOTAL EXCHANGE PNL")
print("==================================================")
print(f"Total Unique Pairs Traded:  {total_pairs} (across {len(all_trades)} trade fills)")
print(f"Winning Pairs / Trades:     {wins} ({win_rate:.1f}%)")
print(f"Losing Pairs / Trades:      {losses} ({(losses/total_pairs*100 if total_pairs else 0):.1f}%)")
print(f"Breakeven Trades:           {breakevens} ({(breakevens/total_pairs*100 if total_pairs else 0):.1f}%)")
print("--------------------------------------------------")
print(f"Gross Realized Profits:     +${tot_gross_win:.2f} USD (+Rs.{tot_gross_win*86.0:,.2f} INR)")
print(f"Gross Realized Losses:      -${tot_gross_loss:.2f} USD (-Rs.{tot_gross_loss*86.0:,.2f} INR)")
print(f"Total Exchange Fees Paid:   ${tot_fees:.4f} USD (Rs.{tot_fees*86.0:,.2f} INR)")
print("--------------------------------------------------")
print(f"🎯 NET REALIZED PnL:         ${tot_net_usd:+.2f} USD | Rs.{tot_net_inr:+.2f} INR")
print("==================================================")
