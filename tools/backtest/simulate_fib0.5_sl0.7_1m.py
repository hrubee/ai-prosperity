#!/usr/bin/env python3
import sys, json, time, urllib.request

spikes = [
    {"sym": "AWE", "spike_t": 1786648500000},
    {"sym": "HANA", "spike_t": 1786656600000},
    {"sym": "PIEVERSE", "spike_t": 1786664700000},
    {"sym": "SOPH", "spike_t": 1786666500000},
    {"sym": "SWARMS", "spike_t": 1786669200000},
    {"sym": "RED", "spike_t": 1786674600000},
    {"sym": "PIXEL", "spike_t": 1786678200000},
    {"sym": "BANANAS31", "spike_t": 1786680900000},
    {"sym": "CHIP", "spike_t": 1786682700000},
    {"sym": "VELVET", "spike_t": 1786685400000},
    {"sym": "XAI", "spike_t": 1786686300000},
    {"sym": "2Z", "spike_t": 1786687200000},
    {"sym": "STBL", "spike_t": 1786689000000},
    {"sym": "CROSS", "spike_t": 1786693500000},
    {"sym": "C", "spike_t": 1786695300000}
]

def fetch_1m(sym):
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=1m&limit=500"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        return sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception:
        return []

print("=== 1m INTRABAR REPLAY: ENTRY @ 0.5 FIB | WIDER SL @ 0.7 FIB ===")
print(f"{'Symbol':<10} | {'Spike Time':<10} | {'Status':<12} | {'Filled Px':<10} | {'Exit Px':<10} | {'Peak R':<8} | {'PnL ($)':<10}")
print("-" * 85)

tot_pnl = 0.0
wins = 0
losses = 0
skips = 0

for s in spikes:
    sym = s["sym"]
    spike_t = s["spike_t"]
    candles = fetch_1m(sym)
    if not candles:
        continue
        
    c_15m = [c for c in candles if float(c.get('time', 0)) <= spike_t]
    if len(c_15m) < 15:
        continue
    c_spike_15m = c_15m[-15:]
    high = max(float(c.get('high', 0)) for c in c_spike_15m)
    low = min(float(c.get('low', 0)) for c in c_spike_15m)
    
    rng = high - low
    if rng <= 0:
        continue
        
    entry_px = high - (0.50 * rng)  # Entry at 0.5 Fib Retracement
    sl_px = high - (0.70 * rng)     # Wider SL at 0.7 Fib Retracement
    risk = entry_px - sl_px
    if risk <= 0:
        continue
    tp_px = entry_px + (5.0 * risk) # 1:5.0 Risk Reward
    
    risk_usd = 1.50 # $1.50 USD risk per trade (1.0% of $150 equity)
    qty = risk_usd / risk
    
    post_spike_candles = [c for c in candles if float(c.get('time', 0)) >= spike_t]
    
    filled = False
    fill_px = 0.0
    exit_px = 0.0
    trade_pnl = 0.0
    status = "NO_FILL"
    peak_r = 0.0
    trail_sl = sl_px
    
    for c in post_spike_candles:
        c_high = float(c.get('high', 0))
        c_low = float(c.get('low', 0))
        
        if not filled:
            # Check if price dropped below SL before fill
            if c_low <= sl_px:
                status = "SKIPPED_SL"
                skips += 1
                break
            # Check fill at entry_px
            if c_low <= entry_px:
                filled = True
                fill_px = entry_px
        else:
            current_r = (c_high - fill_px) / risk
            if current_r > peak_r:
                peak_r = current_r
                
            # Dynamic Trailing SL step (+2.0R activation, 1.0R distance)
            if peak_r >= 2.0:
                new_trail = fill_px + ((peak_r - 1.0) * risk)
                if new_trail > trail_sl:
                    trail_sl = new_trail
                    
            # Check Exit Conditions: SL or TP
            if c_low <= trail_sl:
                exit_px = trail_sl
                trade_pnl = (exit_px - fill_px) * qty
                if trade_pnl > 0:
                    status = "WIN_TRAIL"
                    wins += 1
                else:
                    status = "STOP_OUT"
                    losses += 1
                break
                
            if c_high >= tp_px:
                exit_px = tp_px
                trade_pnl = (exit_px - fill_px) * qty
                status = "WIN_FULL_TP"
                wins += 1
                break

    if filled and status not in ["STOP_OUT", "WIN_TRAIL", "WIN_FULL_TP"]:
        # Still open at current price
        last_px = float(post_spike_candles[-1].get('close', fill_px))
        trade_pnl = (last_px - fill_px) * qty
        status = "OPEN"
        exit_px = last_px

    tot_pnl += trade_pnl
    spike_time_str = time.strftime('%H:%M', time.gmtime(spike_t/1000))
    print(f"{sym:<10} | {spike_time_str:<10} | {status:<12} | {fill_px:<10.6g} | {exit_px:<10.6g} | {peak_r:<8.2f} | ${trade_pnl:<10.2f}")

print("-" * 85)
print(f"SUMMARY (Entry 0.5 Fib / Wider SL 0.7 Fib / 1m Intrabar Replay):")
print(f"  Wins: {wins} | Losses: {losses} | Skips: {skips}")
print(f"  TOTAL NET PROFIT: ${tot_pnl:.2f} USD (₹{tot_pnl*84:.2f} INR)")
