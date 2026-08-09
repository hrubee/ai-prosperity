import time
import requests
import json
import os
import datetime

from dotenv import load_dotenv
load_dotenv("/root/aiprosperity/backend/.env")

# Configuration
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
POLL_INTERVAL_SEC = 0.3
WEBHOOK_URL = "http://127.0.0.1:8001/api/webhook"
SEEN_ORDERS_FILE = "/root/aiprosperity/backend/dhan_poller_seen.json"

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
        print(f"Failed to save seen orders: {e}")

def log_msg(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def run_poller():
    seen_orders = load_seen_orders()
    log_msg(f"Started Dhan Poller. Loaded {len(seen_orders)} previously seen executed orders.")
    
    # On first run, we probably don't want to execute historical orders from today.
    # So if seen_orders is empty, we will pre-fill it with all existing TRADED orders,
    # OR we just check their updateTime to see if it was in the last 10 seconds.
    # Better yet, let's just pre-fill on the very first boot.
    first_boot = len(seen_orders) == 0
    last_heartbeat = 0

    while True:
        try:
            resp = requests.get("https://api.dhan.co/v2/orders", headers=dhan_headers, timeout=5)
            
            if resp.status_code == 200:
                orders = resp.json()
                if not isinstance(orders, list):
                    log_msg(f"Unexpected response format: {orders}")
                    time.sleep(POLL_INTERVAL_SEC)
                    continue

                new_executions = []
                for order in orders:
                    oid = order.get("orderId")
                    status = order.get("orderStatus", "")
                    
                    if not oid:
                        continue
                        
                    # We only care about executed TRADED orders (or maybe REJECTED if you want UI feedback, but TRADED is critical for copier)
                    # Actually, the webhook parses REJECTED, CANCELLED, TRADED. Let's forward any terminal state that wasn't seen.
                    # Dhan webhooks normally send on all status changes.
                    # Let's just track the 'updateTime' + 'orderId' + 'orderStatus' combination?
                    # No, let's just track terminal states (TRADED, CANCELLED, REJECTED) so we don't spam.
                    if status in ("PENDING", "TRANSFERRED", "OPEN", "MODIFIED", "UPDATED", "TRADED", "CANCELLED", "REJECTED", "FAILED", "EXPIRED", "TRANSIT"):
                        state_key = f"{oid}_{status}"
                        if state_key not in seen_orders:
                            if first_boot:
                                # Send historic trades to UI, but mark them to prevent execution
                                order_copy = dict(order)
                                order_copy["is_historic"] = True
                                new_executions.append((state_key, order_copy))
                            else:
                                new_executions.append((state_key, order))
                
                if first_boot:
                    save_seen_orders(seen_orders)
                    log_msg(f"First boot: Pre-filled {len(seen_orders)} terminal orders to prevent historic execution.")
                    first_boot = False
                
                
                # Heartbeat every 60 seconds to Copier UI
                if time.time() - last_heartbeat > 60:
                    try:
                        requests.post(WEBHOOK_URL, json={"type": "status", "message": f"Dhan Poller Active: Monitoring orders... (Seen: {len(seen_orders)})"}, timeout=5)
                        last_heartbeat = time.time()
                    except:
                        pass
                
                for state_key, order_data in new_executions:
                    log_msg(f"Detected new state: {state_key}. Forwarding to local Copier Webhook...")
                    
                    # Forward to local webhook
                    try:
                        wh_resp = requests.post(WEBHOOK_URL, json=order_data, timeout=5)
                        log_msg(f"Webhook forward result: {wh_resp.status_code}")
                        
                        # Only mark as seen if successfully forwarded (or if it's a 4xx error indicating bad payload)
                        if wh_resp.status_code < 500:
                            seen_orders.add(state_key)
                            save_seen_orders(seen_orders)
                    except Exception as we:
                        log_msg(f"Failed to forward webhook: {we}")
            else:
                log_msg(f"Dhan API Error {resp.status_code}: {resp.text[:200]}")
                
        except requests.exceptions.RequestException as e:
            log_msg(f"Network error: {e}")
        except Exception as e:
            log_msg(f"Unexpected error: {e}")
            
        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    run_poller()
