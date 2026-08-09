import os
import json
import asyncio
from typing import List
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from . import tradejini_auth
from . import db
from .xts_interactive_ws import start_xts_websockets

try:
    from dhanhq import dhanhq, DhanContext
except ImportError:
    dhanhq = None
    DhanContext = None

router = APIRouter(tags=["copier"])

import time

LOG_FILE = "copier_logs.jsonl"

def save_log(message: str):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(message + "\n")
    except Exception:
        pass

COPIER_STATE_FILE = "copier_state.json"

def is_copier_enabled():
    try:
        if os.path.exists(COPIER_STATE_FILE):
            with open(COPIER_STATE_FILE, "r") as f:
                state = json.load(f)
                return state.get("enabled", True)
    except:
        pass
    return True

def set_copier_enabled(enabled: bool):
    try:
        with open(COPIER_STATE_FILE, "w") as f:
            json.dump({"enabled": enabled}, f)
    except:
        pass

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        save_log(message)
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

@router.get("/api/logs")
def get_logs():
    logs = []
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()
                # Get last 200 lines
                for line in lines[-200:]:
                    if line.strip():
                        logs.append(json.loads(line))
    except Exception:
        pass
    return logs

@router.get("/api/copier-status")
def get_copier_status():
    return {"enabled": is_copier_enabled()}

@router.post("/api/copier-toggle")
async def toggle_copier():
    current = is_copier_enabled()
    new_state = not current
    set_copier_enabled(new_state)
    msg = f"Copier is now {'ACTIVE' if new_state else 'PAUSED'}"
    await manager.broadcast(json.dumps({"type": "status", "message": msg}))
    return {"enabled": new_state}

ORDER_MAPPING_FILE = "copier_order_mappings.json"

def get_order_mapping(dhan_order_id: str) -> dict:
    try:
        if os.path.exists(ORDER_MAPPING_FILE):
            with open(ORDER_MAPPING_FILE, "r") as f:
                mappings = json.load(f)
                return mappings.get(str(dhan_order_id), {})
    except Exception:
        pass
    return {}

def save_order_mapping(dhan_order_id: str, client_api_key: str, tj_order_id: str):
    try:
        mappings = {}
        if os.path.exists(ORDER_MAPPING_FILE):
            with open(ORDER_MAPPING_FILE, "r") as f:
                mappings = json.load(f)
        d_id = str(dhan_order_id)
        if d_id not in mappings:
            mappings[d_id] = {}
        mappings[d_id][client_api_key] = tj_order_id
        with open(ORDER_MAPPING_FILE, "w") as f:
            json.dump(mappings, f)
    except Exception as e:
        save_log(f"Error saving order mapping: {e}")

def remove_order_mapping(dhan_order_id: str):
    try:
        if os.path.exists(ORDER_MAPPING_FILE):
            with open(ORDER_MAPPING_FILE, "r") as f:
                mappings = json.load(f)
            d_id = str(dhan_order_id)
            if d_id in mappings:
                del mappings[d_id]
                with open(ORDER_MAPPING_FILE, "w") as f:
                    json.dump(mappings, f)
    except Exception:
        pass

@router.post("/api/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    
    if not is_copier_enabled():
        await manager.broadcast(json.dumps({
            "type": "error", 
            "message": "Copier is PAUSED. Ignoring webhook."
        }))
        return {"status": "ignored", "reason": "copier disabled"}
    
    log_msg = {
        "type": "signal_received",
        "data": payload
    }
    await manager.broadcast(json.dumps(log_msg))

    is_dhan = "dhanClientId" in payload or "orderStatus" in payload

    dhan_order_id = ""
    tj_order_type = "market"
    limit_price = 0.0
    trig_price = 0.0
    do_cancel = False
    do_place = False
    do_modify = False
    tj_product = "normal"

    if is_dhan:
        dhan_order_id = str(payload.get("orderId", ""))
        order_status = payload.get("orderStatus", "").upper()
        
        dhan_order_type = payload.get("orderType", "MARKET").upper()
        if dhan_order_type == "LIMIT":
            tj_order_type = "limit"
            limit_price = float(payload.get("price") or 0.0)
        elif dhan_order_type == "STOP_LOSS":
            tj_order_type = "stoplimit"
            limit_price = float(payload.get("price") or 0.0)
            trig_price = float(payload.get("triggerPrice") or 0.0)
        elif dhan_order_type == "STOP_LOSS_MARKET":
            tj_order_type = "stopmarket"
            trig_price = float(payload.get("triggerPrice") or 0.0)

        if order_status in ("REJECTED", "CANCELLED", "FAILED", "EXPIRED"):
            err_desc = payload.get("omsErrorDescription") or payload.get("text") or "No description"
            err_code = payload.get("omsErrorCode") or "Unknown"
            await manager.broadcast(json.dumps({
                "type": "error",
                "message": f"Dhan Order {order_status} [{err_code}]: {err_desc}",
                "data": payload
            }))
            do_cancel = True
        elif order_status in ("PENDING", "TRANSFERRED", "OPEN", "MODIFIED", "UPDATED"):
            mapping = get_order_mapping(dhan_order_id)
            if mapping:
                do_modify = True
            else:
                do_place = True
        elif order_status == "TRADED":
            mapping = get_order_mapping(dhan_order_id)
            if mapping:
                return {"status": "ignored", "reason": "already placed as pending"}
            do_place = True
            tj_order_type = "market" 
        else:
            return {"status": "ignored", "reason": f"Order status is {order_status}, unhandled"}

        if not do_place and not do_cancel and not do_modify:
            return {"status": "ignored"}
            
        dhan_product = payload.get("productType", "").upper()
        tj_product = "mis" if dhan_product == "INTRADAY" else "normal"

        action = payload.get("transactionType", "").upper() 
        if action == "B":
            action = "BUY"
        elif action == "S":
            action = "SELL"
            
        quantity = int(payload.get("filledQty") or payload.get("filled_qty") or 0)
        if quantity == 0:
            quantity = int(payload.get("quantity", 1))
        
        trading_symbol = payload.get("tradingSymbol", "")
        
        is_opt = False
        opt_und = ""
        opt_expiry = payload.get("drvExpiryDate", "")
        opt_strike = float(payload.get("drvStrikePrice") or 0.0)
        opt_type = payload.get("drvOptionType", "").upper()
        if opt_type == "CALL": opt_type = "CE"
        if opt_type == "PUT": opt_type = "PE"
        
        if "-" in trading_symbol:
            parts = trading_symbol.upper().split("-")
            opt_und = parts[0]
            if len(parts) >= 3 and len(parts[1]) >= 6:
                und = parts[0]
                month = parts[1][:3]
                year = parts[1][-2:]
                if len(parts) == 4 and parts[3] in ("CE", "PE"):
                    trading_symbol = f"{und}{year}{month}{parts[2]}{parts[3]}"
                elif len(parts) == 3 and parts[2] == "FUT":
                    trading_symbol = f"{und}{year}{month}FUT"
                    
        if opt_expiry and opt_strike > 0 and opt_type in ("CE", "PE"):
            is_opt = True
            
    else:
        action = payload.get("action", "").upper()
        quantity = int(payload.get("quantity", 1))
        trading_symbol = payload.get("symbol", "")
        is_opt = False
        do_place = True
        do_cancel = False
        do_modify = False
        tj_product = "normal"
        tj_order_type = "market"
        limit_price = 0.0
        trig_price = 0.0
        dhan_order_id = ""

    if not do_cancel and not do_modify and (not action or not trading_symbol):
         await manager.broadcast(json.dumps({"type": "error", "message": "Invalid webhook payload"}))
         return {"status": "error", "message": "Invalid payload"}

    try:
        from sqlalchemy import select
        from .models import TradejiniConnection, User, Subscription, ClientOrder
        from .tradejini import TradejiniClient
        
        valid_clients = []
        with db.session_scope() as session:
            conns = session.execute(select(TradejiniConnection)).scalars().all()
            for c in conns:
                if not tradejini_auth.has_auto_creds(c) or c.status == "disconnected" or c.paused:
                    continue
                try:
                    user = session.get(User, c.user_id)
                    if not user or user.role == "archived":
                        continue
                    
                    sub = session.query(Subscription).filter_by(user_id=user.id).one_or_none()
                    token = tradejini_auth.ensure_client_token(session, c)
                    cl = TradejiniClient(token, api_key=c.api_key)
                    valid_clients.append((cl, user, sub))
                except Exception as e:
                    pass
                    
        for client, user, sub in valid_clients:
            try:
                if do_cancel:
                    mapping = get_order_mapping(dhan_order_id)
                    tj_order_id = mapping.get(client.api_key)
                    if tj_order_id:
                        client.cancel_order(tj_order_id)
                        await manager.broadcast(json.dumps({
                            "type": "info",
                            "account": client.api_key,
                            "message": f"Cancelled order {tj_order_id}"
                        }))
                    continue
                    
                if do_modify:
                    mapping = get_order_mapping(dhan_order_id)
                    tj_order_id = mapping.get(client.api_key)
                    if tj_order_id:
                        meta = None
                        if is_opt:
                            try:
                                meta = client.resolve_weekly_option(opt_und, opt_expiry, opt_strike, opt_type)
                            except Exception as e:
                                pass
                        if not meta:
                            meta = client.resolve(trading_symbol)
                        
                        sym_id = meta["sym_id"]
                        client.modify_order(
                            sym_id=sym_id,
                            order_id=tj_order_id,
                            qty=quantity,
                            order_type=tj_order_type,
                            limit_price=limit_price,
                            trig_price=trig_price
                        )
                        await manager.broadcast(json.dumps({
                            "type": "info",
                            "account": client.api_key,
                            "message": f"Modified order {tj_order_id}"
                        }))
                    continue

                if not do_place:
                    continue

                meta = None
                if is_opt:
                    try:
                        meta = client.resolve_weekly_option(opt_und, opt_expiry, opt_strike, opt_type)
                    except Exception:
                        pass
                if not meta:
                    meta = client.resolve(trading_symbol)
                
                sym_id = meta["sym_id"]
                
                # Check subscription!
                if sub is None or not sub.is_active:
                    positions = client.open_positions()
                    pos = next((p for p in positions if p["sym_id"] == sym_id), None)
                    is_close = False
                    if pos:
                        net_qty = int(pos.get("net_qty", 0))
                        if net_qty > 0 and action.lower() == "sell":
                            is_close = True
                        elif net_qty < 0 and action.lower() == "buy":
                            is_close = True
                            
                    if not is_close:
                        await manager.broadcast(json.dumps({
                            "type": "info",
                            "account": client.api_key,
                            "message": f"Subscription expired for {user.email}. Blocked ENTRY trade."
                        }))
                        continue

                resp = client.place_order(
                    sym_id=sym_id,
                    side=action.lower(),
                    qty=quantity,
                    product=tj_product,
                    order_type=tj_order_type,
                    limit_price=limit_price,
                    trig_price=trig_price
                )
                
                if dhan_order_id:
                    save_order_mapping(dhan_order_id, client.api_key, resp)
                
                # Save client order history
                with db.session_scope() as session:
                    co = ClientOrder(
                        user_id=user.id,
                        symbol=trading_symbol,
                        side=action.lower(),
                        qty=quantity,
                        exchange_order_id=resp,
                        status="filled",
                        details={"type": "dhan_copy", "dhan_id": dhan_order_id}
                    )
                    session.add(co)
                
                await manager.broadcast(json.dumps({
                    "type": "trade_placed",
                    "account": client.api_key,
                    "response": resp
                }))
            except Exception as e:
                await manager.broadcast(json.dumps({
                    "type": "error",
                    "account": client.api_key,
                    "message": str(e)
                }))
                
        if do_cancel and dhan_order_id:
            remove_order_mapping(dhan_order_id)
                
        return {"status": "success", "accounts_processed": len(valid_clients)}
        
    except Exception as e:
        await manager.broadcast(json.dumps({"type": "error", "message": f"Global Error: {str(e)}"}))
        return {"status": "error", "message": str(e)}

@router.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
