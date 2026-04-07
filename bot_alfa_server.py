import logging
import asyncio
import os
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from ib_insync import IB, util, Order
import config
from scanner import SPXScanner
from trading import OrderManager

# Fix asyncio loop conflicts with ib_insync
import nest_asyncio
nest_asyncio.apply()

# Setup Logging
import base64
import os
import psutil
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import FileResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IBMobileServer")

app = FastAPI(title="IB Precision Mobile API")

# Basic Auth Configuration
WEB_USER = getattr(config, "WEB_USER", "admin")
WEB_PASS = getattr(config, "WEB_PASS", "Gerardo090928#*") # Default secure pass

@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    # Let static files and health checks load freely. Only protect the API.
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="IB Precision Bot"'}, content="Ingrese sus credenciales para acceder al dashboard de trading.")
    
    try:
        encoded_credentials = auth_header.split(" ", 1)[1]
        
        # Add padding if needed
        encoded_credentials += "=" * ((4 - len(encoded_credentials) % 4) % 4)
        
        decoded = base64.b64decode(encoded_credentials).decode("utf-8")
        username, _, password = decoded.partition(":")
        
        # Check username (case-insensitive for mobile keyboards) and password
        if username.lower() != WEB_USER.lower() or password != WEB_PASS:
             logger.warning(f"[AUTH FAILED] Intentaron usar -> Usuario: '{username}' | Contraseña: '{password}'")
             return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="IB Precision Bot"'}, content="Credenciales invalidas.")
    except Exception as e:
        logger.error(f"Auth parsing error: {str(e)}")
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="IB Precision Bot"'}, content="Error de formato auth.")
         
    return await call_next(request)

# Add a public health/ping endpoint cloudflare can hit
@app.get("/ping")
async def ping():
    return {"status": "ok"}

# Enable CORS for mobile access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared State
class BotState:
    def __init__(self):
        self.ib = IB()
        self.scanner = SPXScanner(self.ib)
        self.manager = OrderManager(self.ib)
        self.watchdog_triggered = set()
        self.running = False
        self.stats = {"cash": 0, "positions": 0, "status": "OFFLINE"}

state = BotState()

@app.on_event("startup")
async def startup_event():
    logger.info("Starting IB Mobile Server Engine...")
    asyncio.create_task(engine_loop())

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down...")
    state.running = False
    if state.ib.isConnected():
        state.ib.disconnect()

async def engine_loop():
    """Main background loop for IBKR connectivity and watchdog"""
    state.running = True
    while state.running:
        try:
            if not state.ib.isConnected():
                state.stats["status"] = "OFFLINE"
                try:
                    # Attempt background reconnection (Port 4002/7497)
                    await state.ib.connectAsync(config.IB_HOST, config.IB_PORT, clientId=888, timeout=5)
                    state.ib.reqMarketDataType(3)  # Use Delayed Data to avoid CBOE 10090 Subscription errors in Paper Trading
                    logger.info("IBKR Connected successfully.")
                except:
                    pass
            else:
                state.stats["status"] = "ONLINE"
                
                # 1. Sync Account Metrics
                acc_vals = {v.tag: v.value for v in state.ib.accountValues() if v.account == state.ib.wrapper.accounts[0]}
                state.stats["cash"] = float(acc_vals.get('CashBalance', 0))
                
                positions = state.ib.positions()
                state.stats["positions"] = sum(1 for p in positions if p.contract.symbol == 'SPX')
                
                # 2. Watchdog Logic (Profit > 10% -> 5% Trail)
                for p in positions:
                    if p.contract.symbol == 'SPX':
                        tickers = state.ib.reqTickers(p.contract)
                        if not tickers: continue
                        curr = tickers[0].mark if tickers[0].mark > 0 else tickers[0].last
                        if curr <= 0: continue
                        
                        profit = (curr / p.avgCost - 1) * 100 if p.avgCost > 0 else 0
                        if profit >= config.TT_PERCENT and p.contract.conId not in state.watchdog_triggered:
                            logger.info(f"WATCHDOG TRIGGER: {p.contract.localSymbol} at {profit:.1f}% profit.")
                            # Cancel old SL
                            for t in state.ib.trades():
                                if t.contract.conId == p.contract.conId and t.order.orderType == 'STP':
                                    state.ib.cancelOrder(t.order)
                            # New Trail
                            t_amt = round(curr * (config.TRAIL_PERCENT / 100), 2)
                            state.ib.placeOrder(p.contract, Order(action='SELL', totalQuantity=p.position, orderType='TRAIL', auxPrice=t_amt))
                            state.watchdog_triggered.add(p.contract.conId)
        except Exception as e:
            logger.error(f"Engine Error: {e}")
        
        await asyncio.sleep(2)

# --- API ENDPOINTS ---

@app.get("/api/status")
async def get_status():
    res = dict(state.stats)
    res["min_price"] = config.MIN_PRICE
    res["max_price"] = config.MAX_PRICE
    res["default_qty"] = config.DEFAULT_QUANTITY
    return res

@app.get("/api/scan")
async def get_scan():
    if not state.ib.isConnected():
        raise HTTPException(status_code=503, detail="IBKR Disconnected")
    # Wrap synchronous scanner calls if necessary, but ib_insync likes its own loop
    res = state.scanner.get_best_contracts()
    return res

@app.post("/api/execute/{side}")
async def execute_trade(side: str, qty: int = config.DEFAULT_QUANTITY):
    if not state.ib.isConnected():
        raise HTTPException(status_code=503, detail="IBKR Disconnected")
    
    side = side.upper()
    if side not in ["CALL", "PUT"]:
        raise HTTPException(status_code=400, detail="Invalid side")
    
    # Reset watchdog for new trades on this side
    # (Simplified: clearing since we usually trade one side at a time)
    state.watchdog_triggered.clear()
    
    # 1. Scan
    contracts = state.scanner.get_best_contracts()
    best = contracts.get(side)
    if not best:
        raise HTTPException(status_code=404, detail="No matching contract found in range")
    
    # 2. Place Order
    success, msg = state.manager.place_spx_order(best['contract'], qty, best['ask'])
    if not success:
        raise HTTPException(status_code=500, detail=msg)
    
    return {"status": "success", "message": msg, "contract": best['description']}

# --- TRADINGVIEW WEBHOOK (no requiere Basic Auth — ruta /webhook/) ---

@app.post("/webhook/tv-alert")
async def tradingview_alert(request: Request):
    """
    Recibe webhooks de alertas de TradingView.
    Payload esperado:
    {
        "symbol": "SPX",
        "direction": "CALL" o "PUT",
        "price": 4713.75,
        "timestamp": "2026-04-07T14:27:30Z",
        "secret": "pa-scanner-2026"
    }
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    # Validar token secreto
    if data.get("secret") != getattr(config, "WEBHOOK_SECRET", ""):
        logger.warning(f"[WEBHOOK] Token inválido recibido: {data.get('secret', 'NONE')}")
        raise HTTPException(status_code=403, detail="Token inválido")

    direction = str(data.get("direction", "")).upper()
    if direction not in ("CALL", "PUT"):
        raise HTTPException(status_code=400, detail="direction debe ser CALL o PUT")

    if not state.ib.isConnected():
        raise HTTPException(status_code=503, detail="IBKR desconectado")

    # 1. Obtener balance de cuenta
    acc_vals = {v.tag: v.value for v in state.ib.accountValues()
                if v.account == state.ib.wrapper.accounts[0]}
    cash = float(acc_vals.get("CashBalance", 0))

    # 2. Obtener mejor contrato
    contracts = state.scanner.get_best_contracts()
    best = contracts.get(direction)
    if not best:
        raise HTTPException(status_code=404, detail=f"No hay contrato {direction} en rango de precio")

    ask_price = best["ask"]

    # 3. Calcular cantidad de contratos (20% del balance)
    risk_pct = getattr(config, "RISK_PERCENT", 0.20)
    import math
    qty = math.floor(cash * risk_pct / (ask_price * 100))
    if qty < 1:
        raise HTTPException(status_code=400, detail=f"Balance insuficiente: ${cash:.2f} para {direction} @ ${ask_price}")

    # 4. Ejecutar orden
    state.watchdog_triggered.clear()
    success, msg = state.manager.place_spx_order(best["contract"], qty, ask_price)
    if not success:
        raise HTTPException(status_code=500, detail=msg)

    result = {
        "status": "success",
        "source": "TradingView PA-Scanner",
        "direction": direction,
        "contract": best["description"],
        "qty": qty,
        "ask": ask_price,
        "capital_risked": round(qty * ask_price * 100, 2),
        "cash_balance": round(cash, 2),
        "message": msg,
    }
    logger.info(f"[WEBHOOK] ✅ ORDEN EJECUTADA: {direction} {qty}c @ ${ask_price:.2f} | {best['description']}")
    return result


# Serve frontend using explicit route for root, and absolute path for static assets
BASE_DIR = Path(__file__).resolve().parent

@app.get("/")
async def serve_index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))

app.mount("/", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    import socket
    
    TARGET_PORT = 8001
    
    # --- AUTO PORT CLEANUP ---
    print("\n--- INICIALIZANDO ENTORNO ---")
    print("Verificando conflictos de puertos y procesos 'zombies'...")
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for conn in proc.connections(kind='inet'):
                if conn.laddr.port == TARGET_PORT and conn.status == 'LISTEN':
                    print(f"[KILL] Cerrando proceso viejo {proc.info['name']} (PID: {proc.info['pid']}) que ocupaba el puerto {TARGET_PORT}")
                    proc.terminate()
                    proc.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    print("Puerto limpio ok.")
    
    # helper to find local IP
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"\n{'='*50}")
    print(f"IB PRECISION MOBILE SERVER ACTIVO")
    print(f"URL LOCAL: http://localhost:8001")
    print(f"URL MOVIL: http://{local_ip}:8001")
    print(f"{'='*50}\n")
    
    # Use 0.0.0.0 to allow access from local network (mobile)
    uvicorn.run(app, host="0.0.0.0", port=8001)
