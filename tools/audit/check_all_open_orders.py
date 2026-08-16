import sys
import os
import json
import ccxt
from dotenv import load_dotenv

def main():
    dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../.env'))
    load_dotenv(dotenv_path)
    
    api_key = os.getenv("BINANCE_DEMO_API_KEY")
    demo_secret = os.getenv("BINANCE_DEMO_API_SECRET")
    
    config = {
        "apiKey": api_key,
        "secret": demo_secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future"
        }
    }
    
    exchange = ccxt.binance(config)
    exchange.enable_demo_trading(True)
    exchange.urls['api']['fapiPublic'] = 'https://demo-fapi.binance.com/fapi/v1'
    exchange.urls['api']['fapiPrivate'] = 'https://demo-fapi.binance.com/fapi/v1'
    exchange.urls['api']['fapiPublicV2'] = 'https://demo-fapi.binance.com/fapi/v2'
    exchange.urls['api']['fapiPrivateV2'] = 'https://demo-fapi.binance.com/fapi/v2'
    exchange.urls['api']['fapiPublicV3'] = 'https://demo-fapi.binance.com/fapi/v3'
    exchange.urls['api']['fapiPrivateV3'] = 'https://demo-fapi.binance.com/fapi/v3'
    exchange.options['enableDemoTrading'] = True
    
    try:
        # Call the private endpoint directly to fetch all open orders
        print("Calling fapiPrivateGetOpenOrders...")
        orders = exchange.fapiPrivateGetOpenOrders()
        print(f"Total raw open orders on account: {len(orders)}")
        
        symbol_counts = {}
        for o in orders:
            sym = o.get("symbol")
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
            
        print("\nBreakdown of open orders by symbol:")
        for sym, count in symbol_counts.items():
            print(f"  {sym}: {count} orders")
            
        print("\nListing first 20 open orders:")
        for idx, o in enumerate(orders[:20]):
            print(f"  {idx+1}. ID: {o.get('orderId')} | Symbol: {o.get('symbol')} | Side: {o.get('side')} | Type: {o.get('type')} | StopPrice: {o.get('stopPrice')} | OrigQty: {o.get('origQty')} | Status: {o.get('status')}")
            
    except Exception as e:
        print(f"Error calling fapiPrivateGetOpenOrders: {e}")

if __name__ == "__main__":
    main()
