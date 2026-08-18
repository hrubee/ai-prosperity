import sys, os, sqlite3, json
from datetime import datetime, timezone

sys.path.insert(0, "/root/trading-bot/crypto")
from dotenv import load_dotenv
load_dotenv("/root/trading-bot/crypto/.env")
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A1 = CoinDCXExchangeAdapter()
A2 = CoinDCXExchangeAdapter(key=os.getenv("COINDCX_API_KEY_2"), secret=os.getenv("COINDCX_API_SECRET_2"))

# 1. Check SQLite recorded trades
print("=== DUMPRIDE SQLITE EXECUTED SIGNALS ===")
db_file = "/root/dumpride_coindcx/dumpride_executed.db"
if os.path.exists(db_file):
    conn = sqlite3.connect(db_file)
    rows = conn.execute("SELECT symbol, candle_timestamp, vol_mult, entry_px, sl_px, tp_px, executed_at FROM executed_signals").fetchall()
    conn.close()
    for r in rows:
        t_ist = datetime.fromtimestamp(r[6]/1000 + 19800, tz=timezone.utc).strftime("%d-%b %I:%M %p IST")
        print(f"{t_ist} | #{r[0]:<8} | Spike: {r[2]:.1f}x | Entry: {r[3]} | SL: {r[4]} | TP: {r[5]}")
else:
    print("No SQLite DB found.")

# 2. Check CoinDCX Account Balances & Open Positions
print("\n=== LIVE EXCHANGE POSITIONS ===")
pos1 = [p for p in A1.fetch_positions() if float(p.get("active_units", 0) or 0) != 0]
pos2 = [p for p in A2.fetch_positions() if float(p.get("active_units", 0) or 0) != 0]
print(f"Primary Account Open Positions: {len(pos1)}")
print(f"Secondary Account Open Positions: {len(pos2)}")

# 3. Check Account 1 & 2 Recent Fills
print("\n=== PRIMARY ACCOUNT RECENT FILLS ===")
try:
    fills1 = A1._post("/exchange/v1/derivatives/futures/orders/trade_history", {"page": 1, "size": 10})
    for f in fills1:
        print(f"{f.get('symbol')} | {f.get('side')} | Qty: {f.get('quantity')} | Px: {f.get('price')} | Fee: {f.get('fee')} | Realized PnL: {f.get('realised_pnl')} | Time: {f.get('created_at')}")
except Exception as e:
    print(f"Error fetching fills 1: {e}")

try:
    fills2 = A2._post("/exchange/v1/derivatives/futures/orders/trade_history", {"page": 1, "size": 10})
    print("\n=== SECONDARY ACCOUNT RECENT FILLS ===")
    for f in fills2:
        print(f"{f.get('symbol')} | {f.get('side')} | Qty: {f.get('quantity')} | Px: {f.get('price')} | Fee: {f.get('fee')} | Realized PnL: {f.get('realised_pnl')} | Time: {f.get('created_at')}")
except Exception as e:
    print(f"Error fetching fills 2: {e}")

# 4. Check Current Wallet Balances
print("\n=== WALLET BALANCES ===")
bal1 = A1.get_wallet_balance_inr()
free1 = A1.get_free_inr_balance()
bal2 = A2.get_wallet_balance_inr()
free2 = A2.get_free_inr_balance()
print(f"Primary Account: Total ₹{bal1:.2f} | Free ₹{free1:.2f}")
print(f"Secondary Account: Total ₹{bal2:.2f} | Free ₹{free2:.2f}")
