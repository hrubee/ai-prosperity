#!/usr/bin/env python3
import sys, json, time, urllib.request, os
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

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
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=1m&limit=500"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        if isinstance(raw, list) and len(raw) > 0:
            return sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception:
        pass
    return []

pairs = fetch_pairs()
all_15m_data = {}
with ThreadPoolExecutor(max_workers=25) as ex:
    futures = [ex.submit(fetch_15m_klines, sym) for sym in pairs]
    for f in as_completed(futures):
        sym, candles = f.result()
        if candles:
            all_15m_data[sym] = candles

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

def simulate_1m_spike(ev):
    sym = ev["sym"]
    spike_t = ev["spike_t"]
    high = ev["high"]
    low = ev["low"]
    
    rng = high - low
    if rng <= 0:
        return None
        
    entry_px = high - (0.60 * rng)
    sl_px = high - (0.70 * rng)
    risk = entry_px - sl_px
    if risk <= 0:
        return None
    tp_px = entry_px + (5.0 * risk)
    
    risk_usd = 1.50 # $1.50 USD risk per trade
    qty = risk_usd / risk
    
    m1_candles = fetch_1m_around_spike(sym, spike_t)
    if not m1_candles:
        return None
        
    post_candles = [c for c in m1_candles if float(c.get('time', 0)) >= spike_t]
    if not post_candles:
        return None
        
    filled = False
    fill_px = 0.0
    exit_px = 0.0
    pnl = 0.0
    status = "NO_FILL"
    peak_r = 0.0
    trail_sl = sl_px
    bars_in_trade = 0
    fill_time = 0
    exit_time = 0
    
    for c in post_candles:
        c_high = float(c.get('high', 0))
        c_low = float(c.get('low', 0))
        c_time = float(c.get('time', 0))
        
        if not filled:
            if c_low <= sl_px:
                return {"status": "SKIPPED_SL", "pnl": 0.0, "r_multiple": 0.0, "duration_m": 0, "sym": sym}
            if c_low <= entry_px:
                filled = True
                fill_px = entry_px
                fill_time = c_time
        else:
            bars_in_trade += 1
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
                r_mult = (exit_px - fill_px) / risk
                status = "WIN_TRAIL" if pnl > 0 else "STOP_OUT"
                return {"status": status, "pnl": pnl, "r_multiple": r_mult, "duration_m": bars_in_trade, "sym": sym, "peak_r": peak_r, "time": c_time}
                
            if c_high >= tp_px:
                exit_px = tp_px
                pnl = (exit_px - fill_px) * qty
                r_mult = 5.0
                status = "WIN_TP"
                return {"status": status, "pnl": pnl, "r_multiple": r_mult, "duration_m": bars_in_trade, "sym": sym, "peak_r": peak_r, "time": c_time}

    return {"status": "NO_FILL", "pnl": 0.0, "r_multiple": 0.0, "duration_m": 0, "sym": sym}

results = []
with ThreadPoolExecutor(max_workers=25) as ex:
    futures = [ex.submit(simulate_1m_spike, ev) for ev in spike_events]
    for f in as_completed(futures):
        res = f.result()
        if res and res["status"] in ["WIN_TRAIL", "WIN_TP", "STOP_OUT"]:
            results.append(res)

# Sort trades chronologically
results = sorted(results, key=lambda x: x.get("time", 0))

tot_trades = len(results)
wins = [r for r in results if r["pnl"] > 0]
losses = [r for r in results if r["pnl"] <= 0]

gross_profit = sum(w["pnl"] for w in wins)
gross_loss = abs(sum(l["pnl"] for l in losses))
net_pnl = gross_profit - gross_loss

win_rate = (len(wins) / tot_trades * 100.0) if tot_trades > 0 else 0.0
loss_rate = 100.0 - win_rate

pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
avg_win_usd = (gross_profit / len(wins)) if wins else 0.0
avg_loss_usd = (gross_loss / len(losses)) if losses else 0.0
avg_win_r = np.mean([w["r_multiple"] for w in wins]) if wins else 0.0
avg_loss_r = np.mean([l["r_multiple"] for l in losses]) if losses else 0.0

win_loss_payoff_ratio = (avg_win_usd / avg_loss_usd) if avg_loss_usd > 0 else 0.0
expectancy_usd = (net_pnl / tot_trades) if tot_trades > 0 else 0.0
expectancy_r = np.mean([r["r_multiple"] for r in results]) if results else 0.0

max_win = max(results, key=lambda x: x["pnl"]) if results else None
max_loss = min(results, key=lambda x: x["pnl"]) if results else None

# Drawdown & Equity Curve
initial_equity = 200.0
equity_curve = [initial_equity]
r_curve = [0.0]
peak_eq = initial_equity
max_dd_usd = 0.0
max_dd_pct = 0.0
max_dd_r = 0.0

curr_r = 0.0
for r in results:
    eq = equity_curve[-1] + r["pnl"]
    equity_curve.append(eq)
    curr_r += r["r_multiple"]
    r_curve.append(curr_r)
    
    if eq > peak_eq:
        peak_eq = eq
    dd_usd = peak_eq - eq
    dd_pct = (dd_usd / peak_eq) * 100.0 if peak_eq > 0 else 0.0
    if dd_usd > max_dd_usd:
        max_dd_usd = dd_usd
        max_dd_pct = dd_pct

# Streak analysis
max_consec_wins = 0
max_consec_losses = 0
cur_w = 0
cur_l = 0

for r in results:
    if r["pnl"] > 0:
        cur_w += 1
        cur_l = 0
        if cur_w > max_consec_wins:
            max_consec_wins = cur_w
    else:
        cur_l += 1
        cur_w = 0
        if cur_l > max_consec_losses:
            max_consec_losses = cur_l

# Holding Durations
durations = [r["duration_m"] for r in results]
avg_duration = np.mean(durations) if durations else 0
median_duration = np.median(durations) if durations else 0
max_duration = max(durations) if durations else 0
min_duration = min(durations) if durations else 0

output_metrics = {
    "tot_spikes_eval": len(spike_events),
    "tot_trades": tot_trades,
    "wins_count": len(wins),
    "losses_count": len(losses),
    "win_rate_pct": win_rate,
    "loss_rate_pct": loss_rate,
    "gross_profit_usd": gross_profit,
    "gross_loss_usd": gross_loss,
    "net_pnl_usd": net_pnl,
    "net_pnl_inr": net_pnl * 84.0,
    "starting_equity_usd": initial_equity,
    "ending_equity_usd": equity_curve[-1],
    "roi_pct": (net_pnl / initial_equity) * 100.0,
    "profit_factor": pf,
    "payoff_ratio": win_loss_payoff_ratio,
    "expectancy_usd": expectancy_usd,
    "expectancy_r": expectancy_r,
    "avg_win_usd": avg_win_usd,
    "avg_win_r": avg_win_r,
    "avg_loss_usd": avg_loss_usd,
    "avg_loss_r": avg_loss_r,
    "max_win_symbol": max_win["sym"] if max_win else "",
    "max_win_usd": max_win["pnl"] if max_win else 0.0,
    "max_win_r": max_win["r_multiple"] if max_win else 0.0,
    "max_loss_symbol": max_loss["sym"] if max_loss else "",
    "max_loss_usd": max_loss["pnl"] if max_loss else 0.0,
    "max_loss_r": max_loss["r_multiple"] if max_loss else 0.0,
    "max_dd_usd": max_dd_usd,
    "max_dd_pct": max_dd_pct,
    "max_consec_wins": max_consec_wins,
    "max_consec_losses": max_consec_losses,
    "avg_duration_min": avg_duration,
    "median_duration_min": median_duration,
    "min_duration_min": min_duration,
    "max_duration_min": max_duration
}

print(json.dumps(output_metrics, indent=2))
