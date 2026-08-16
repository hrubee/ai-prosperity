#!/usr/bin/env python3
import sys, json, time, urllib.request, os, ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

ssl._create_default_https_context = ssl._create_unverified_context

def fetch_pairs():
    url = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        prices_dict = raw.get("prices", {})
        pairs = []
        for p in prices_dict.keys():
            if p.startswith("B-") and p.endswith("_USDT"):
                base = p[2:-5]
                pairs.append(base)
        return sorted(list(set(pairs)))
    except Exception as e:
        print("Error fetching pairs:", e)
        return []

def fetch_15m_klines(sym):
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=15m&limit=1000"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        if isinstance(raw, list) and len(raw) > 0:
            return sym, sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception:
        pass
    return sym, []

def fetch_1m_around_spike(sym, spike_t):
    # Fetch 1m candles around spike timestamp
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=1m&limit=500"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        if isinstance(raw, list) and len(raw) > 0:
            return sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception:
        pass
    return []

print("=== STEP 1: DOWNLOADING 10-DAY 15M CANDLE ARCHIVES FOR 527 PAIRS ===")
pairs = fetch_pairs()
all_15m_data = {}
with ThreadPoolExecutor(max_workers=25) as ex:
    futures = [ex.submit(fetch_15m_klines, sym) for sym in pairs]
    for f in as_completed(futures):
        sym, candles = f.result()
        if candles:
            all_15m_data[sym] = candles

print(f"Downloaded 15m archives for {len(all_15m_data)} pairs.")

# Detect all volume spikes
spike_events = []
for sym, candles in all_15m_data.items():
    if len(candles) < 50:
        continue
    vols = np.array([float(c.get('volume', 0)) for c in candles])
    closes = np.array([float(c.get('close', 0)) for c in candles])
    opens = np.array([float(c.get('open', 0)) for c in candles])
    times = np.array([float(c.get('time', 0)) for c in candles])
    highs = np.array([float(c.get('high', 0)) for c in candles])
    lows = np.array([float(c.get('low', 0)) for c in candles])
    
    for i in range(40, len(closes)):
        avg_v = np.mean(vols[i-40:i])
        if avg_v <= 0:
            continue
        cur_v = vols[i]
        is_green = closes[i] > opens[i]
        vol_mult = cur_v / avg_v
        
        if is_green and vol_mult >= 15.0:
            spike_events.append({
                "sym": sym,
                "spike_t": times[i],
                "high": highs[i],
                "low": lows[i]
            })

print(f"\n=== STEP 2: REPLAYING 1-MINUTE INTRABAR CANDLES FOR ALL {len(spike_events)} HISTORICAL VOLUME SPIKES ===")
print("Strategy Parameters: Entry @ 0.60 Fib | SL @ 0.70 Fib | RR 1:5.0")
print("-" * 90)

def simulate_1m_spike(ev):
    sym = ev["sym"]
    spike_t = ev["spike_t"]
    high = ev["high"]
    low = ev["low"]
    
    rng = high - low
    if rng <= 0:
        return "ZERO_RNG", 0.0, 0.0
        
    entry_px = high - (0.60 * rng)
    sl_px = high - (0.70 * rng)
    risk = entry_px - sl_px
    if risk <= 0:
        return "BAD_RISK", 0.0, 0.0
    tp_px = entry_px + (5.0 * risk)
    
    risk_usd = 1.50 # $1.50 USD risk per trade
    qty = risk_usd / risk
    
    m1_candles = fetch_1m_around_spike(sym, spike_t)
    if not m1_candles:
        return "NO_1M_DATA", 0.0, 0.0
        
    post_candles = [c for c in m1_candles if float(c.get('time', 0)) >= spike_t]
    if not post_candles:
        return "EXPIRED_1M", 0.0, 0.0
        
    filled = False
    fill_px = 0.0
    exit_px = 0.0
    pnl = 0.0
    status = "NO_FILL"
    peak_r = 0.0
    trail_sl = sl_px
    
    for c in post_candles:
        c_high = float(c.get('high', 0))
        c_low = float(c.get('low', 0))
        
        if not filled:
            if c_low <= sl_px:
                status = "SKIPPED_SL"
                break
            if c_low <= entry_px:
                filled = True
                fill_px = entry_px
        else:
            curr_r = (c_high - fill_px) / risk
            if curr_r > peak_r:
                peak_r = curr_r
                
            if peak_r >= 2.0:
                new_t = fill_px + ((peak_r - 1.0) * risk)
                if new_t > trail_sl:
                    trail_sl = new_t
                    
            if c_low <= trail_sl:
                exit_px = trail_sl
                pnl = (exit_px - fill_px) * qty
                status = "WIN_TRAIL" if pnl > 0 else "STOP_OUT"
                break
                
            if c_high >= tp_px:
                exit_px = tp_px
                pnl = (exit_px - fill_px) * qty
                status = "WIN_TP"
                break

    return status, pnl, peak_r

tot_trades = 0
wins = 0
losses = 0
skips = 0
no_fills = 0
tot_pnl = 0.0
pnls = []

with ThreadPoolExecutor(max_workers=25) as ex:
    futures = [ex.submit(simulate_1m_spike, ev) for ev in spike_events]
    for f in as_completed(futures):
        st, pnl, r = f.result()
        if st in ["WIN_TRAIL", "WIN_TP", "STOP_OUT"]:
            tot_trades += 1
            tot_pnl += pnl
            pnls.append(pnl)
            if pnl > 0:
                wins += 1
            else:
                losses += 1
        elif st == "SKIPPED_SL":
            skips += 1
        elif st == "NO_FILL":
            no_fills += 1

win_rate = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
gross_profit = sum(p for p in pnls if p > 0)
gross_loss = abs(sum(p for p in pnls if p < 0))
pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

# Enhanced Metrics Computation
winning_trades = [p for p in pnls if p > 0]
losing_trades = [p for p in pnls if p < 0]

avg_win_usd = (sum(winning_trades) / len(winning_trades)) if winning_trades else 0.0
avg_loss_usd = (sum(losing_trades) / len(losing_trades)) if losing_trades else 0.0
avg_win_r = avg_win_usd / 1.50
avg_loss_r = avg_loss_usd / 1.50
payoff_ratio = (avg_win_usd / avg_loss_usd) if avg_loss_usd > 0 else 0.0

expectancy_usd = (tot_pnl / tot_trades) if tot_trades > 0 else 0.0
expectancy_r = expectancy_usd / 1.50

max_win_usd = max(pnls) if pnls else 0.0
max_loss_usd = min(pnls) if pnls else 0.0

# Drawdown Calculation (assuming $200 initial capital, $1.50 risk per trade = 0.75% account risk)
initial_capital = 200.00
equity_curve = [initial_capital]
peak = initial_capital
max_dd_usd = 0.0
max_dd_pct = 0.0

for p in pnls:
    curr_eq = equity_curve[-1] + p
    equity_curve.append(curr_eq)
    if curr_eq > peak:
        peak = curr_eq
    dd = peak - curr_eq
    dd_pct = (dd / peak) * 100.0
    if dd > max_dd_usd:
        max_dd_usd = dd
        max_dd_pct = dd_pct

max_dd_r = max_dd_usd / 1.50

# Streak Analysis
max_consec_wins = 0
max_consec_losses = 0
cur_w = 0
cur_l = 0

for p in pnls:
    if p > 0:
        cur_w += 1
        cur_l = 0
        if cur_w > max_consec_wins:
            max_consec_wins = cur_w
    else:
        cur_l += 1
        cur_w = 0
        if cur_l > max_consec_losses:
            max_consec_losses = cur_l

roi_pct = (tot_pnl / initial_capital) * 100.0

print(f"\n=========================================================================")
print(f"       COMPREHENSIVE BACKTEST METRICS (FIBVOL 0.6 FIB / 0.7 SL)")
print(f"=========================================================================")
print(f"--- 1. OVERALL PERFORMANCE & PROFITABILITY ---")
print(f"Initial Account Capital:         ${initial_capital:.2f} USD")
print(f"Ending Equity:                   ${equity_curve[-1]:.2f} USD")
print(f"Total Net Profit:                +${tot_pnl:.2f} USD (+₹{tot_pnl*84:.2f} INR)")
print(f"Return on Capital (ROI):         +{roi_pct:.2f}%")
print(f"Profit Factor (PF):              {pf:.2f}")
print(f"Expectancy per Trade:            +${expectancy_usd:.2f} USD (+{expectancy_r:.2f}R)")

print(f"\n--- 2. TRADE STATISTICAL BREAKDOWN ---")
print(f"Total Volume Spikes Evaluated:  {len(spike_events)}")
print(f"Filled & Executed Trades:        {tot_trades}")
print(f"Winning Trades:                  {wins} ({win_rate:.1f}%)")
print(f"Losing Trades:                   {losses} ({100.0-win_rate:.1f}%)")
print(f"Skipped (Initial SL Breached):   {skips}")
print(f"No Fill (Dipped < 0.6 Fib):      {no_fills}")

print(f"\n--- 3. PAYOFF & RISK/REWARD ANALYSIS ---")
print(f"Fixed Risk per Trade:            $1.50 USD (0.75% of account)")
print(f"Gross Profit:                    +${gross_profit:.2f} USD")
print(f"Gross Loss:                      -${gross_loss:.2f} USD")
print(f"Average Win:                     +${avg_win_usd:.2f} USD (+{avg_win_r:.2f}R)")
print(f"Average Loss:                    -${avg_loss_usd:.2f} USD (-{avg_loss_r:.2f}R)")
print(f"Win/Loss Payoff Ratio:           {payoff_ratio:.2f}x")
print(f"Largest Single Win:              +${max_win_usd:.2f} USD (+{max_win_usd/1.50:.2f}R)")
print(f"Largest Single Loss:             -${abs(max_loss_usd):.2f} USD (-{abs(max_loss_usd)/1.50:.2f}R)")

print(f"\n--- 4. DRAWDOWN & STREAK RISK METRICS ---")
print(f"Maximum Peak-to-Trough Drawdown: ${max_dd_usd:.2f} USD (-{max_dd_pct:.2f}%)")
print(f"Maximum Drawdown in R-multiples: -{max_dd_r:.2f}R")
print(f"Max Consecutive Winning Streak:   {max_consec_wins} trades")
print(f"Max Consecutive Losing Streak:    {max_consec_losses} trades")
print(f"=========================================================================\n")

