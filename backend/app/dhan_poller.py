import time
import requests
import json
import os
import datetime
import asyncio
import httpx

from dotenv import load_dotenv
load_dotenv("/root/aiprosperity/backend/.env")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
POLL_INTERVAL_SEC = 0.3
# Use internal API port now since we merged copier into backend
WEBHOOK_URL = "http://127.0.0.1:8000/api/webhook"
SEEN_ORDERS_FILE = "dhan_poller_seen.json"

dhan_headers = {
    "access-token": DHAN_ACCESS_TOKEN,
    "client-id": DHAN_CLIENT_ID,
    "Content-Type": "application/json",
    "Accept": "application/json"
}

def load_seen_orders():
    if os.path.exists(SEEN_ORDERS_FILE):
        try:
            with open(SEEN_ORDERS_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen_orders(seen_orders):
    try:
        with open(SEEN_ORDERS_FILE, "w") as f:
            json.dump(list(seen_orders), f)
    except Exception as e:
        pass

def log_msg(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[DhanPoller] {ts} - {msg}")

async def run_poller_async():
    seen_orders = load_seen_orders()
    log_msg(f"Started Dhan Poller. Loaded {len(seen_orders)} previously seen executed orders.")
    
    first_boot = len(seen_orders) == 0
    last_heartbeat = 0

    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get("https://api.dhan.co/v2/orders", headers=dhan_headers, timeout=5.0)
                
                if resp.status_code == 200:
                    orders = resp.json()
                    if not isinstance(orders, list):
                        await asyncio.sleep(POLL_INTERVAL_SEC)
                        continue

                    new_executions = []
                    for order in orders:
                        oid = order.get("orderId")
                        status = order.get("orderStatus", "")
                        
                        if not oid:
                            continue
                            
                        if status in ("PENDING", "TRANSFERRED", "OPEN", "MODIFIED", "UPDATED", "TRADED", "CANCELLED", "REJECTED", "FAILED", "EXPIRED", "TRANSIT"):
                            state_key = f"{oid}_{status}"
                            if state_key not in seen_orders:
                                if first_boot:
                                    order_copy = dict(order)
                                    order_copy["is_historic"] = True
                                    new_executions.append((state_key, order_copy))
                                else:
                                    new_executions.append((state_key, order))
                    
                    if first_boot:
                        save_seen_orders(seen_orders)
                        log_msg(f"First boot: Pre-filled {len(seen_orders)} terminal orders.")
                        first_boot = False
                    
                    if time.time() - last_heartbeat > 60:
                        try:
                            await client.post(WEBHOOK_URL, json={"type": "status", "message": f"Dhan Poller Active: Monitoring orders... (Seen: {len(seen_orders)})"}, timeout=5.0)
                            last_heartbeat = time.time()
                        except:
                            pass
                    
                    for state_key, order_data in new_executions:
                        log_msg(f"Detected new state: {state_key}. Forwarding to unified backend webhook...")
                        try:
                            wh_resp = await client.post(WEBHOOK_URL, json=order_data, timeout=5.0)
                            if wh_resp.status_code < 500:
                                seen_orders.add(state_key)
                                save_seen_orders(seen_orders)
                        except Exception as we:
                            log_msg(f"Failed to forward webhook: {we}")
                
            except Exception as e:
                pass
                
            await asyncio.sleep(POLL_INTERVAL_SEC)
