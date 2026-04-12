# Ensure log directory exists
import os
os.makedirs("logs", exist_ok=True)

import logging
from fastapi import FastAPI, Header, Depends

from .config import get_settings
from .ib_client import client
from .models import OrderResponse, TradingViewAlert, WebhookPayload
from .webhook import handle_tradingview_alert, handle_webhook
from .dashboard import router as dashboard_router
from .data_logger import data_logger

from logging.handlers import RotatingFileHandler
import os
import traceback
import asyncio

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        RotatingFileHandler("logs/bot_new.log", maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)

app = FastAPI(
    title="IBG Options Bracket Bot",
    description=(
        "Servicio que recibe señales vía webhook y coloca órdenes bracket "
        "de opciones en Interactive Brokers Gateway."
    ),
    version="0.1.0",
)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import JSONResponse
from fastapi import Request

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Error no manejado en {request.url}: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal Server Error", "detail": str(exc)},
    )

from fastapi.staticfiles import StaticFiles
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard_router)

# ── Calibrador Post-Sesión ──────────────────────────────────────────────────
from .calibrator_router import router as calibrator_router
app.include_router(calibrator_router)


from .contract_selector import setup_day_plan, restore_day_state, TICKER_PRICE_RANGES
from .ibkr_adapter import ibkr_broker

from .dca_monitor import dca_monitor
from .consistency_checker import consistency_monitor

@app.on_event("startup")
async def startup() -> None:
    """
    Inicia el DASHBOARD y conecta con IBKR.
    
    REFACTOR V2: El dashboard es ahora una interfaz de lectura y control manual.
    Los procesos autónomos (DCA, Risk Manager, Consistency Monitor, etc.) 
    se han movido al core_orchestrator.py para evitar que un reinicio del 
    dashboard deje las posiciones sin supervisión.
    """
    settings = get_settings()
    logger = logging.getLogger("ibg.bot")
    
    if settings.webhook_token:
        logger.info("Webhook protegido con token")
    
    # DataLogger: necesario para el dashboard (historial de movimientos)
    await data_logger.start()
    
    # Inicializar Cache Persistente (Carga desde disco)
    import app.option_chain_cache
    
    # ============================================================
    # PROCESOS AUTÓNOMOS DESACOPLADOS DEL DASHBOARD (REFACTOR V2)
    # Estos procesos ahora corren en core_orchestrator.py:
    #   - dca_monitor
    #   - consistency_monitor  
    #   - risk_manager
    #   - pre_market_auto
    #   - post_session_analyst
    #   - post_session_calibrator
    # ============================================================
    
    # Tarea de fondo para conectar e inicializar con RECONEXIÓN ROBUSTA
    async def _initialize_system_loop():
        retry_count = 0
        while True:
            try:
                logger.info(f"⚡ Intentando conectar con IBKR (Intento {retry_count + 1})...")
                await client.connect()
                
                if client.is_connected():
                    logger.info("✅ Conectado. Iniciando secuencia de arranque del Dashboard...")
                    # 1. Recuperar estado (Crash Recovery)
                    await restore_day_state(ibkr_broker)
                    
                    # 2. Generar Plan del Día
                    active_tickers_str = getattr(settings, "active_tickers", "SPY,QQQ,GOOG,AMZN,TSLA,NVDA")
                    active_list = [t.strip().upper() for t in active_tickers_str.split(",")]
                    logger.info(f"🚀 Escaneando tickers activos: {active_list}")
                    await setup_day_plan(ibkr_broker, active_list, settings)
                    
                    # 3. Pre-calentar caché de opciones (Optimización Latencia)
                    await client.prefetch_option_chains(active_list)
                    
                    logger.info("🚀 DASHBOARD TOTALMENTE OPERATIVO.")
                    break
                else:
                    logger.error("❌ Falló la conexión inicial con IBKR. Reintentando en 5s...")
            
            except Exception as e:
                logger.error(f"❌ Error crítico en el arranque: {e}. Reintentando en 5s...", exc_info=True)
            
            await asyncio.sleep(5)
            retry_count += 1

    asyncio.create_task(_initialize_system_loop())

    # Tarea de fondo para sincronizar estado periódicamente (cada 5 minutos)
    # NOTA: Este bucle se mantiene en el dashboard porque alimenta la interfaz web
    async def _sync_state_loop():
        while True:
            try:
                if client.is_connected():
                    logger.info("🔄 Sincronizando estado del día con IBKR...")
                    await restore_day_state(ibkr_broker)
                    
                    # Actualizar historial para el dashboard
                    fresh_fills = await ibkr_broker.get_daily_executions()
                    if fresh_fills:
                        from .history import history_manager
                        history_manager.add_executions(fresh_fills)
                    
                    # SPX Contract AutoLab: evaluar si hay suficientes trades para evolucionar
                    try:
                        from .spx_contract_autolab import spx_autolab
                        from .history import history_manager as hm
                        spx_autolab.check_and_evolve(hm.get_all_movements())
                    except Exception as autolab_err:
                        logger.debug(f"[SPX_AUTOLAB] Check: {autolab_err}")
                        
            except Exception as e:
                logger.error(f"❌ Error en bucle de sincronización: {e}")
            
            await asyncio.sleep(300)

    asyncio.create_task(_sync_state_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    """Detiene el gestor de conexión y desconecta limpiamente."""
    # from .observability import observability
    # await observability.stop()
    from .risk_manager import risk_manager
    await risk_manager.stop()
    from .pre_market_auto import pre_market_auto
    await pre_market_auto.stop()
    from .post_session import post_session_analyst
    await post_session_analyst.stop()

    # Detener calibrador
    from .post_session_calibrator import post_session_calibrator
    await post_session_calibrator.stop()
    
    await dca_monitor.stop()
    await consistency_monitor.stop()
    await data_logger.stop()
    await client.disconnect()


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}

    return {"status": "observability_disabled"}
    # from .observability import observability
    # return observability.get_status_report()


@app.post("/webhook", response_model=OrderResponse)
async def webhook(payload: WebhookPayload, x_auth_token: str | None = Header(default=None)) -> OrderResponse:
    return await handle_webhook(payload, x_auth_token)


@app.post("/tv-alert", response_model=OrderResponse)
async def tv_alert(alert: TradingViewAlert, x_auth_token: str | None = Header(default=None)) -> OrderResponse:
    """Ruta específica para alertas de TradingView."""
    return await handle_tradingview_alert(alert, x_auth_token)


@app.post("/emergency/panic")
async def panic_button(x_auth_token: str | None = Header(default=None)):
    """
    ☢️ BOTÓN DE PÁNICO (DEFCON 1) ☢️
    Cancela todo, cierra todo, bloquea todo.
    """
    # Verificar token (seguridad crítica)
    from .webhook import verify_token
    await verify_token(x_auth_token)
    
    from .risk_manager import risk_manager
    result = await risk_manager.emergency_panic()
    return {"status": "PANIC_EXECUTED", "details": result}
