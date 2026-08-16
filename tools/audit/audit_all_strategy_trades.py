#!/usr/bin/env python3
"""audit_all_strategy_trades.py — Audit all live & paper strategy trades on VPS since recent updates.
"""
import os
import sys
import json
import glob
import datetime

OUT_DIRS = {
    "FIBVOL (CoinDCX)": "/root/fibvol_coindcx",
    "VOL2B2T (CoinDCX)": "/root/vol2b2t_coindcx",
    "TTM (CoinDCX)": "/root/ttm_coindcx",
    "2B2T Delta": "/root/coindcx_2b2t",
    "Moonshot": "/root/moonshot_paper"
}

def main():
    print("==========================================================================================================")
    print("📊 FULL MULTI-STRATEGY TRADE & POSITION AUDIT (IST TIMEZONE)")
    print("==========================================================================================================")

    for strat_name, out_dir in OUT_DIRS.items():
        print(f"\n🔹 STRATEGY: {strat_name}")
        print("-" * 90)

        # 1. State File Inspection
        state_path = os.path.join(out_dir, "state.json")
        state = {}
        if os.path.exists(state_path):
            try:
                state = json.load(open(state_path))
            except Exception:
                pass

        bal_inr = state.get("_bal_inr", "N/A")
        watching = state.get("watching", {})
        positions = state.get("positions", {})
        last_spikes = state.get("last_spikes", {})

        print(f"  Current Wallet Balance (INR): ₹{bal_inr}")
        print(f"  Active Open Positions ({len(positions)}): {list(positions.keys()) if positions else 'None'}")
        print(f"  Active Watching Coins ({len(watching)}): {list(watching.keys()) if watching else 'None'}")

        # 2. Trades File Inspection
        trades_path = os.path.join(out_dir, "trades.csv")
        if not os.path.exists(trades_path):
            # Fallback search
            alt_matches = glob.glob(f"{out_dir}*/*.csv")
            if alt_matches: trades_path = alt_matches[0]

        if os.path.exists(trades_path):
            with open(trades_path) as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]

            print(f"  Total Historical Recorded Trades: {len(lines)}")
            print(f"\n  Recent Trade Executions (Last 10):")
            print(f"  {'Symbol':<10} {'Action':<10} {'Side':<6} {'Price':<12} {'Qty':<10} {'Time in IST':<30} {'Details'}")
            print("  " + "-" * 85)

            recent = lines[-10:] if len(lines) >= 10 else lines
            for line in recent:
                parts = line.split(",")
                if len(parts) < 5: continue
                raw_t = parts[0]
                sym = parts[1]
                action = parts[2]
                side = parts[3]
                px = parts[4]
                qty = parts[5] if len(parts) > 5 else "0"
                extra = ", ".join(parts[6:]) if len(parts) > 6 else ""

                try:
                    raw_clean = raw_t.replace("Z", "").split(".")[0]
                    if "T" in raw_clean:
                        dt_u = datetime.datetime.fromisoformat(raw_clean)
                    else:
                        dt_u = datetime.datetime.strptime(raw_clean, "%Y-%m-%d %H:%M:%S")
                    dt_ist = dt_u + datetime.timedelta(hours=5, minutes=30)
                    t_str = dt_ist.strftime("%d-%b %H:%M:%S IST")
                except Exception:
                    t_str = raw_t

                print(f"  {sym:<10} {action:<10} {side:<6} {px:<12} {qty:<10} {t_str:<30} {extra}")
        else:
            print("  Trades log file: None found yet.")

if __name__ == "__main__":
    main()
