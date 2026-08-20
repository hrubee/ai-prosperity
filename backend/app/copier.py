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
        if websocket not in self.active_connections:
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
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
@router.get("/logs")
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
@router.get("/copier-status")
def get_copier_status():
    return {"enabled": is_copier_enabled()}

@router.post("/api/copier-toggle")
@router.post("/copier-toggle")
async def toggle_copier():
    current = is_copier_enabled()
    new_state = not current
    set_copier_enabled(new_state)
    msg = f"Copier is now {'ACTIVE' if new_state else 'PAUSED'}"
    await manager.broadcast(json.dumps({"type": "status", "message": msg}))
    return {"enabled": new_state}

import threading

ORDER_MAPPING_FILE = "copier_order_mappings.json"
_mapping_lock = threading.Lock()
_PROCESSED_DHAN_PLACEMENTS: set[str] = set()

def get_order_mapping(dhan_order_id: str) -> dict:
    try:
        with _mapping_lock:
            if os.path.exists(ORDER_MAPPING_FILE):
                with open(ORDER_MAPPING_FILE, "r") as f:
                    mappings = json.load(f)
                    return mappings.get(str(dhan_order_id), {})
    except Exception:
        pass
    return {}

def save_order_mapping(dhan_order_id: str, client_api_key: str, tj_order_id: str):
    try:
        with _mapping_lock:
            mappings = {}
            if os.path.exists(ORDER_MAPPING_FILE):
                with open(ORDER_MAPPING_FILE, "r") as f:
                    mappings = json.load(f)
            d_id = str(dhan_order_id)
            _PROCESSED_DHAN_PLACEMENTS.add(d_id)
            if d_id not in mappings:
                mappings[d_id] = {}
            mappings[d_id][client_api_key] = tj_order_id
            with open(ORDER_MAPPING_FILE, "w") as f:
                json.dump(mappings, f)
    except Exception as e:
        save_log(f"Error saving order mapping: {e}")

def remove_order_mapping(dhan_order_id: str):
    try:
        with _mapping_lock:
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
@router.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()

    if payload.get("type") == "status":
        return {"status": "accepted", "reason": "heartbeat"}
        
    if payload.get("is_historic"):
        return {"status": "ignored", "reason": "historic order from pre-boot state"}
    
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
            if mapping or (dhan_order_id and dhan_order_id in _PROCESSED_DHAN_PLACEMENTS):
                do_modify = True
            else:
                do_place = True
        elif order_status == "TRADED":
            mapping = get_order_mapping(dhan_order_id)
            if mapping or (dhan_order_id and dhan_order_id in _PROCESSED_DHAN_PLACEMENTS):
                return {"status": "ignored", "reason": "already placed as pending/traded"}
            do_place = True
            tj_order_type = "market" 
        else:
            return {"status": "ignored", "reason": f"Order status is {order_status}, unhandled"}

        if not do_place and not do_cancel and not do_modify:
            return {"status": "ignored"}
            
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
        elif " " in trading_symbol:
            opt_und = trading_symbol.upper().split(" ")[0]
            
        if not opt_und and trading_symbol:
            opt_und = trading_symbol.upper().replace("-", " ").split(" ")[0]
                    
        if opt_expiry and opt_strike > 0 and opt_type in ("CE", "PE"):
            is_opt = True

        # Product Type Resolution: Tradejini XTS API requires "normal" (NRML) for Options / F&O contracts!
        # Passing "mis" or "intraday" on options causes Tradejini OM07: Invalid product type.
        dhan_product = payload.get("productType", "").upper()
        if is_opt or payload.get("exchangeSegment") in ("NSE_FNO", "BSE_FNO"):
            tj_product = "normal"
        else:
            tj_product = "mis" if dhan_product == "INTRADAY" else "normal"
            
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
                    valid_clients.append((cl, user, sub, c))
                except Exception as e:
                    pass
                    
        for client, user, sub, conn_obj in valid_clients:
            try:
                # Calculate lot multiplier adjusted quantity (whole integer)
                lot_mult = max(1, int(round(float(conn_obj.lot_multiplier or 1))))
                client_qty = max(1, quantity * lot_mult)

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
                        contract_lot_size = int(meta.get("lot_size") or 1)
                        if contract_lot_size > 1:
                            lots = max(1, int(round((quantity * lot_mult) / contract_lot_size)))
                            client_qty = lots * contract_lot_size
                        else:
                            client_qty = max(1, int(round(quantity * lot_mult)))

                        client.modify_order(
                            sym_id=sym_id,
                            order_id=tj_order_id,
                            qty=client_qty,
                            order_type=tj_order_type,
                            limit_price=limit_price,
                            trig_price=trig_price
                        )
                        await manager.broadcast(json.dumps({
                            "type": "info",
                            "account": client.api_key,
                            "message": f"Modified order {tj_order_id} (qty: {client_qty})"
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
                contract_lot_size = int(meta.get("lot_size") or 1)
                if contract_lot_size > 1:
                    lots = max(1, int(round((quantity * lot_mult) / contract_lot_size)))
                    client_qty = lots * contract_lot_size
                else:
                    client_qty = max(1, int(round(quantity * lot_mult)))
                
                # Check subscription (Active by default for all connected clients)
                if sub is None:
                    try:
                        from .models import Subscription
                        from datetime import timedelta, timezone
                        sub = Subscription(
                            user_id=user.id,
                            package="pro",
                            status="active",
                            current_period_end=datetime.now(timezone.utc) + timedelta(days=3650)
                        )
                        session.add(sub)
                        session.commit()
                    except Exception:
                        pass

                # ── ANTI-NAKED OPTION SHORT PROTECTION ────────────────────────────
                # If Dhan sends a SELL order for an option contract:
                # We MUST verify that the client actually holds an OPEN LONG position.
                # If client holds 0 contracts (e.g. entry BUY was rejected or failed), DROP the SELL order!
                # If client holds fewer contracts than client_qty, cap client_qty to exactly held quantity.
                if is_opt and action.lower() == "sell":
                    try:
                        open_pos_list = client.open_positions()
                        matching_pos = next(
                            (p for p in open_pos_list if p.get("sym_id") == sym_id or str(p.get("symbol", "")).upper() == str(trading_symbol).upper()),
                            None
                        )
                        if not matching_pos or matching_pos.get("side") != "buy" or matching_pos.get("size", 0) <= 0:
                            save_log(f"🚫 [NAKED PROTECTION] Blocked SELL {trading_symbol} for {user.email}: Client holds 0 long contracts (Entry was likely rejected). Aborting order to prevent opening a naked short!")
                            await manager.broadcast(json.dumps({
                                "type": "warning",
                                "account": client.api_key,
                                "message": f"🚫 [NAKED PROTECTION] Blocked SELL {trading_symbol} for {user.email}: No long position held."
                            }))
                            continue
                        
                        held_qty = int(matching_pos.get("size", 0))
                        # CRITICAL FIX: When Dhan exits an option position, scale client_qty to 100% of held_qty
                        # to guarantee that Tradejini client accounts are completely flattened with 0 leftover stranded contracts.
                        if client_qty != held_qty:
                            save_log(f"🔄 [FULL POSITION SQUARING] Adjusting SELL {trading_symbol} for {user.email} from {client_qty} to {held_qty} qty (100% of open position) to guarantee flat state.")
                            client_qty = held_qty
                    except Exception as pos_check_err:
                        save_log(f"Position check error for {user.email}: {pos_check_err}")

                resp = client.place_order(
                    sym_id=sym_id,
                    side=action.lower(),
                    qty=client_qty,
                    product=tj_product,
                    order_type=tj_order_type,
                    limit_price=limit_price,
                    trig_price=trig_price
                )
                
                if dhan_order_id:
                    save_order_mapping(dhan_order_id, client.api_key, resp)

                # Record permanent trade entry in ClientProfitLedger
                try:
                    from .models import ClientProfitLedger
                    ledger_entry = ClientProfitLedger(
                        user_id=user.id,
                        email=user.email,
                        name=user.name,
                        phone=user.phone,
                        client_id=user.client_id,
                        venue="tradejini",
                        symbol=trading_symbol or str(sym_id),
                        side=action.lower(),
                        size=float(quantity),
                        entry_price=float(limit_price or 0.0),
                        fee_inr=20.0,
                        status="filled",
                        order_id=str(resp)
                    )
                    session.add(ledger_entry)
                    session.flush()
                except Exception as le:
                    print(f"[Ledger] Note: {le}")
                
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

# ── AUTONOMOUS POSITION RECONCILIATION WATCHDOG ────────────────────────────────
async def reconcile_positions_loop():
    """Background watchdog running every 20 seconds during market hours.
    Compares Dhan master net positions against all connected Tradejini client accounts.
    If Dhan is FLAT in a strike, but a client still holds open contracts (e.g. from missed webhook),
    it immediately executes an emergency square-off on Tradejini to keep accounts 100% synchronized.
    """
    save_log("🛡️ Autonomous Position Reconciliation Watchdog initialized.")
    while True:
        try:
            await asyncio.sleep(20)
            if not is_copier_enabled():
                continue
                
            from .dhan_poller import get_dhan_headers
            dhan_headers = get_dhan_headers()
            if not dhan_headers:
                continue
                
            # Fetch live Dhan positions
            async with httpx.AsyncClient() as http_client:
                dhan_resp = await http_client.get("https://api.dhan.co/v2/positions", headers=dhan_headers, timeout=5.0)
                if dhan_resp.status_code != 200:
                    continue
                dhan_positions = dhan_resp.json()
                
            dhan_open_symbols = set()
            if isinstance(dhan_positions, list):
                for dp in dhan_positions:
                    net_qty = int(dp.get("netQty") or dp.get("net_quantity") or dp.get("quantity") or 0)
                    if net_qty != 0:
                        sym = str(dp.get("tradingSymbol") or dp.get("customSymbol") or "").upper()
                        if sym:
                            dhan_open_symbols.add(sym)

            # Query all active Tradejini client accounts
            from sqlalchemy import select
            from .models import TradejiniConnection, User
            from .tradejini import TradejiniClient
            
            with db.session_scope() as session:
                conns = session.execute(select(TradejiniConnection)).scalars().all()
                for c in conns:
                    if not tradejini_auth.has_auto_creds(c) or c.status == "disconnected" or c.paused:
                        continue
                    try:
                        user = session.get(User, c.user_id)
                        if not user or user.role == "archived":
                            continue
                            
                        token = tradejini_auth.ensure_client_token(session, c)
                        cl = TradejiniClient(token, api_key=c.api_key)
                        
                        client_open_pos = cl.open_positions()
                        if not client_open_pos:
                            continue
                            
                        for pos in client_open_pos:
                            sym_id = pos.get("sym_id")
                            sym_name = str(pos.get("symbol") or sym_id).upper()
                            size = pos.get("size", 0)
                            
                            # Check if Dhan holds this symbol (or strike match)
                            dhan_holding = any(sym_name in ds or ds in sym_name for ds in dhan_open_symbols)
                            
                            # If Dhan is FLAT in this contract, but Tradejini client holds it -> AUTO FLATTEN!
                            if not dhan_holding and size > 0:
                                save_log(f"🚨 [AUTO-RECONCILIATION] Discrepancy detected: Dhan is FLAT in {sym_name}, but {user.email} held {size} open contracts! Auto-closing on Tradejini now...")
                                res = cl.close_position(sym_id)
                                save_log(f"✅ [AUTO-RECONCILED] Squared off {sym_name} for {user.email}: {res}")
                                await manager.broadcast(json.dumps({
                                    "type": "warning",
                                    "account": cl.api_key,
                                    "message": f"🚨 [AUTO-RECONCILED] Dhan is FLAT in {sym_name}. Auto-squared {size} contracts for {user.email}."
                                }))
                    except Exception:
                        pass
        except Exception as e:
            save_log(f"Error in reconciliation loop: {e}")

@router.on_event("startup")
async def on_startup():
    asyncio.create_task(reconcile_positions_loop())
