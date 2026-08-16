import sys
import os
import json
import ccxt
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from platforms.binance.adapter import BinanceExchangeAdapter

def check_account(api_key, api_secret, sandbox, label):
    print(f"\n==========================================")
    print(f"CHECKING ACCOUNT: {label}")
    print(f"==========================================")
    print(f"Sandbox Mode: {sandbox}")
    print(f"API Key: {api_key[:10] if api_key else 'None'}...")
    
    if not api_key or not api_secret:
        print("Credentials missing for this account.")
        return

    # Create config for ccxt manually to ensure correct keys and modes are used
    config = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {
            "fetchCurrencies": False,
            "warnOnFetchOpenOrdersWithoutSymbol": False
        }
    }
    
    exchange = ccxt.binance(config)
    if sandbox:
        exchange.enable_demo_trading(True)
        exchange.urls['api']['fapiPublic'] = 'https://demo-fapi.binance.com/fapi/v1'
        exchange.urls['api']['fapiPrivate'] = 'https://demo-fapi.binance.com/fapi/v1'
        exchange.urls['api']['fapiPublicV2'] = 'https://demo-fapi.binance.com/fapi/v2'
        exchange.urls['api']['fapiPrivateV2'] = 'https://demo-fapi.binance.com/fapi/v2'
        exchange.urls['api']['fapiPublicV3'] = 'https://demo-fapi.binance.com/fapi/v3'
        exchange.urls['api']['fapiPrivateV3'] = 'https://demo-fapi.binance.com/fapi/v3'
        exchange.options['enableDemoTrading'] = True
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Failed to load markets: {e}")
        return

    # Check positions
    print("\n--- ACTIVE POSITIONS ---")
    try:
        positions = exchange.fetch_positions(params={"type": "future"})
        active = 0
        for pos in positions:
            qty = abs(float(pos.get("contracts", 0) or pos.get("amount", 0) or 0))
            if qty > 0:
                print(f"Symbol: {pos.get('symbol')}, Side: {pos.get('side')}, Size: {qty}, EntryPx: {pos.get('entryPrice')}, PnL: {pos.get('unrealizedPnl')}")
                active += 1
        if active == 0:
            print("No active positions.")
    except Exception as e:
        print(f"Error fetching positions: {e}")

    # Check open orders
    print("\n--- ALL OPEN/PENDING ORDERS ---")
    try:
        all_orders = []
        symbols_to_check = ["SOL/USDT:USDT", "XRP/USDT:USDT", "BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT"]
        
        # Try fetching all open orders directly first
        try:
            all_orders = exchange.fetch_open_orders(params={"type": "future"})
        except Exception as e:
            print(f"Direct fetch_open_orders failed, falling back to per-symbol check: {e}")
            for sym in symbols_to_check:
                try:
                    orders = exchange.fetch_open_orders(sym, params={"type": "future"})
                    all_orders.extend(orders)
                except Exception as e_sym:
                    pass
                    
        print(f"Total open orders found: {len(all_orders)}")
        for order in all_orders:
            symbol = order.get('symbol')
            side = order.get('side')
            amount = order.get('amount')
            price = order.get('price')
            stop_price = order.get('stopPrice')
            order_type = order.get('type')
            status = order.get('status')
            order_id = order.get('id')
            print(f"ID: {order_id} | Symbol: {symbol} | Side: {side} | Type: {order_type} | Qty: {amount} | Price: {price} | StopPrice: {stop_price} | Status: {status}")
    except Exception as e:
        print(f"Error fetching open orders: {e}")

def main():
    dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../.env'))
    load_dotenv(dotenv_path)
    
    # 1. Check Demo Account
    demo_key = os.getenv("BINANCE_DEMO_API_KEY")
    demo_secret = os.getenv("BINANCE_DEMO_API_SECRET")
    check_account(demo_key, demo_secret, sandbox=True, label="BINANCE FUTURES DEMO")
    
    # 2. Check Live Account
    live_key = os.getenv("BINANCE_LIVE_API_KEY")
    live_secret = os.getenv("BINANCE_LIVE_API_SECRET")
    check_account(live_key, live_secret, sandbox=False, label="BINANCE FUTURES LIVE")

if __name__ == "__main__":
    main()
