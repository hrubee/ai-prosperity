import os
import sys
import json

# Add project root to sys.path
sys.path.append("/Users/hrushi/Downloads/Desktop offline/vibe coding/go trader/go-trader")
from platforms.binance.adapter import BinanceExchangeAdapter

def get_market_intelligence(symbol):
    adapter = BinanceExchangeAdapter(api_key=None, secret=None) # Public data doesn't need keys
    
    # 1. Order Book
    depth = adapter.fapiPublicGetDepth({"symbol": symbol.replace("/", ""), "limit": 10})
    bids = [[float(p), float(q)] for p, q in depth['bids']]
    asks = [[float(p), float(q)] for p, q in depth['asks']]
    
    bid_vol = sum(q for p, q in bids)
    ask_vol = sum(q for p, q in asks)
    imbalance = bid_vol / ask_vol if ask_vol > 0 else 0
    
    # 2. Long/Short Ratio
    ratio_data = adapter.fapiPublicGetGlobalLongShortAccountRatio({"symbol": symbol.replace("/", ""), "period": "5m", "limit": 1})
    ratio = float(ratio_data[0]['longShortRatio']) if ratio_data else 1.0
    
    return {
        "symbol": symbol,
        "imbalance": round(imbalance, 2),
        "long_short_ratio": round(ratio, 2),
        "top_bid": bids[0][0],
        "top_ask": asks[0][0],
        "bid_vol": round(bid_vol, 2),
        "ask_vol": round(ask_vol, 2)
    }

def main():
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    results = [get_market_intelligence(s) for s in symbols]
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
