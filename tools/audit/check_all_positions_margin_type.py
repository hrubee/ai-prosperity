#!/usr/bin/env python3
import os
import sys
import json

sys.path.insert(0, "/root/trading-bot/crypto/platforms/coindcx")
from adapter import CoinDCXExchangeAdapter

for env_f in ["/root/trading-bot/crypto/.env", "/root/go-trader/.env"]:
    if os.path.exists(env_f):
        with open(env_f) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

k1 = os.environ.get("COINDCX_LIVE_API_KEY", "").strip()
s1 = os.environ.get("COINDCX_LIVE_API_SECRET", "").strip()

print("===================================================================")
print("  ⚡ COINDCX RECENT POSITIONS MARGIN TYPE AUDIT ⚡")
print("===================================================================")

a1 = CoinDCXExchangeAdapter(key=k1, secret=s1)
raw_positions = a1._post("/exchange/v1/derivatives/futures/positions", {"page": "1", "size": "100"})

if isinstance(raw_positions, list):
    print(f"Total Positions in History: {len(raw_positions)}")
    for p in raw_positions[:10]:
        print("\nPosition Record:")
        print("  - Symbol Pair:", p.get("pair"))
        print("  - Margin Type:", p.get("margin_type"))
        print("  - Active Pos:", p.get("active_pos"))
        print("  - Leverage:", p.get("leverage"))
        print("  - Updated At:", p.get("updated_at"))
else:
    print("API Error Response:", raw_positions)

print("\n===================================================================")
