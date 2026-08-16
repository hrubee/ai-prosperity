#!/usr/bin/env python3
import sys, os, json, time, urllib.request, hmac, hashlib

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()

key1 = os.environ.get("COINDCX_LIVE_API_KEY") or os.environ.get("COINDCX_API_KEY")
secret1 = os.environ.get("COINDCX_LIVE_API_SECRET") or os.environ.get("COINDCX_API_SECRET")

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_ACCOUNT2_API_KEY")
secret2 = os.environ.get("COINDCX_SECRET_2") or os.environ.get("COINDCX_ACCOUNT2_API_SECRET")

def fetch_fills(key, secret):
    if not key or not secret:
        return []
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/trades"
    all_trades = []
    for page in range(1, 10):
        body = {"page": str(page), "size": "100"}
        json_body = json.dumps(body, separators=(",", ":"))
        sig = hmac.new(secret.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": key, "X-AUTH-SIGNATURE": sig, "User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, data=json_body.encode("utf-8"), headers=headers)
        try:
            res = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
            if isinstance(res, list) and len(res) > 0:
                all_trades.extend(res)
            else:
                break
        except Exception:
            break
    return all_trades

trades1 = fetch_fills(key1, secret1)
trades2 = fetch_fills(key2, secret2)

trades = trades1 + trades2

by_pair = {}
for t in trades:
    pair = t.get("pair")
    if pair:
        by_pair.setdefault(pair, []).append(t)

pnls_usd = []
r_multiples = []
RISK_USD_PER_TRADE = 1.50  # $1.50 USD risk per trade (1.0% of $150 equity)

wins = []
losses = []
breakevens = []

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
    r_mult = net_pnl / RISK_USD_PER_TRADE
    
    pnls_usd.append(net_pnl)
    r_multiples.append(r_mult)
    
    if net_pnl > 0.05:
        wins.append((pair, net_pnl, r_mult))
    elif net_pnl < -0.05:
        losses.append((pair, net_pnl, r_mult))
    else:
        breakevens.append((pair, net_pnl, r_mult))

total_trades = len(pnls_usd)
win_count = len(wins)
loss_count = len(losses)
be_count = len(breakevens)

if total_trades == 0:
    print("No completed trades found.")
    sys.exit(0)

win_rate = (win_count / total_trades * 100)
loss_rate = (loss_count / total_trades * 100)
be_rate = (be_count / total_trades * 100)

avg_r = sum(r_multiples) / total_trades
avg_win_usd = sum(w[1] for w in wins) / win_count if win_count > 0 else 0.0
avg_win_r = sum(w[2] for w in wins) / win_count if win_count > 0 else 0.0
avg_loss_usd = sum(l[1] for l in losses) / loss_count if loss_count > 0 else 0.0
avg_loss_r = sum(l[2] for l in losses) / loss_count if loss_count > 0 else 0.0

gross_wins = sum(w[1] for w in wins)
gross_losses = abs(sum(l[1] for l in losses))
profit_factor = gross_wins / gross_losses if gross_losses > 0 else 99.9
payoff_ratio = abs(avg_win_r / avg_loss_r) if abs(avg_loss_r) > 0 else 0.0

print("==================================================")
print("📊 LIVE EXCHANGE TRADE AUDIT: WIN RATE & AVERAGE R")
print("==================================================")
print(f"Total Completed Pair Trades: {total_trades}")
print(f"Winning Trades:               {win_count} ({win_rate:.1f}%)")
print(f"Losing Trades:                {loss_count} ({loss_rate:.1f}%)")
print(f"Breakeven Trades:             {be_count} ({be_rate:.1f}%)")
print("--------------------------------------------------")
print(f"🎯 AVERAGE R-MULTIPLE PER TRADE: {avg_r:+.2f} R")
print(f"📈 AVERAGE WINNING TRADE:        ${avg_win_usd:+.2f} USD ({avg_win_r:+.2f} R)")
print(f"📉 AVERAGE LOSING TRADE:         ${avg_loss_usd:+.2f} USD ({avg_loss_r:+.2f} R)")
print(f"⚖️ PAYOFF RATIO (Win/Loss R):   {payoff_ratio:.2f}x")
print(f"🔥 PROFIT FACTOR:               {profit_factor:.2f}")
print("==================================================")
