import sys
import datetime
sys.path.insert(0, "/Users/hrushi/Downloads/Desktop offline/vibe coding/go trader/go-trader/platforms/coindcx")
from adapter import CoinDCXExchangeAdapter

a = CoinDCXExchangeAdapter()
coins = ["FLOW", "PRL", "KSM", "MANA", "NOM"]
for coin in coins:
    bars = a.get_ohlcv(coin, interval="15m", limit=200)
    if not bars: continue
    
    vols = [b[5] for b in bars]
    max_vol = max(vols)
    max_idx = vols.index(max_vol)
    b = bars[max_idx]
    
    o = float(b[1])
    c = float(b[4])
    ts = int(b[0])
    dt = datetime.datetime.utcfromtimestamp(ts/1000)
    color = "GREEN" if c >= o else "RED"
    
    print(f"{coin}: max vol {max_vol} at {dt}, color={color}, open={o}, close={c}")
