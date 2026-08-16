import requests
import time

def test_candles():
    url = "https://public.coindcx.com/market_data/candles?pair=B-BTC_USDT&interval=1d&limit=5&endTime=1704067200000" # Jan 1, 2024
    r = requests.get(url)
    print(r.json())

test_candles()
