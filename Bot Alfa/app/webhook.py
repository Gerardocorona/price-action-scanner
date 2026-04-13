# webhook.py
"""
Punto de entrada para las señales de TradingView.

Flujo simple:
1. Recibe alerta (ticker + CALL/PUT).
2. Verifica que no sea duplicada.
3. Pasa al contract_selector para seleccionar y ejecutar.
"""

import json
import logging
import re
import time
import asyncio
from datetime import datetime
from typing import Dict, Optional

from fastapi import HTTPException, Header

from .models import (
    OptionAlert,
    OrderResponse,
    TradingViewAlert,
    WebhookPayload,
)

from .config import get_settings
from .contract_selector import on_tradingview_alert, Side
from .ibkr_adapter import ibkr_broker

# Logging
logger = logging.getLogger("ibg.webhook")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    import os
    os.makedirs("logs", exist_ok=True)
    fh = logging.FileHandler("logs/bot.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)


# --- IDEMPOTENCY CACHE ---
_PROCESSED_ALERTS_CACHE: Dict[str, float] = {}
_CACHE_TTL_SECONDS = 120  # 2 minutos


def _is_duplicate_alert(ticker: str, signal: str) -> bool:
    """Evita procesar la misma alerta dos veces en 2 minutos."""
    global _PROCESSED_ALERTS_CACHE
    now = time.time()

    # Limpieza lazy
    expired = [k for k, v in _PROCESSED_ALERTS_CACHE.items() if now - v > _CACHE_TTL_SECONDS]
    for k in expired:
        del _PROCESSED_ALERTS_CACHE[k]

    alert_id = f"{ticker}_{signal}_{int(now / 60)}"
    if alert_id in _PROCESSED_ALERTS_CACHE:
        logger.warning(f"🚫 ALERTA DUPLICADA: {alert_id}. Ignorando.")
        return True

    _PROCESSED_ALERTS_CACHE[alert_id] = now
    return False


# --- SEGURIDAD ---
async def verify_token(x_auth_token: Optional[str] = Header(None)):
    settings = get_settings()
    expected = settings.webhook_token
    if not expected:
        return
    if x_auth_token != expected:
        logger.error(f"⛔ ACCESO NO AUTORIZADO. Token inválido.")
        raise HTTPException(status_code=401, detail="Invalid Token")


# --- PROCESAMIENTO DE ALERTAS ---
async def process_webhook_alert(data: dict) -> Optional[int]:
    """
    [MÓDULO REACTIVADO]
    Toma los datos parseados, verifica la regla de idempotencia anticonflictos
    (no duplicados) y ejecuta la alerta contra el motor de IBKR.
    """
    try:
        ticker = data.get("ticker", "UNKNOWN")
        signal = data.get("signal", "UNKNOWN")
    except Exception:
        ticker, signal = "RAW_DATA", str(data)

    # Bloquear si la misma señal de este ticker acaba de entrar
    if _is_duplicate_alert(ticker, signal):
        return None

    logger.info(f"✅ [WEBHOOK] Alerta de TradingView Aceptada: {ticker} {signal}")
    logger.debug(f"Payload procesado: {data}")
    
    # Mapeo de señal a dirección (long/short)
    sig_upper = signal.upper()
    direction = "long" if sig_upper in ["CALL", "C", "LONG", "BUY"] else "short"
    
    try:
        result = await on_tradingview_alert(
            ticker=ticker,
            direction=direction,
            broker=ibkr_broker
        )
        
        # Normalizar el retorno: on_tradingview_alert devuelve strings ahora
        if isinstance(result, str):
            status_ok = (result == "ORDER_PLACED")
        else:
            status_ok = (result and result.get("status") == "ok")
            
        return 1 if status_ok else None
    except Exception as e:
        logger.error(f"Error procesando alerta TV: {e}")
        return None


# --- ENDPOINTS ---
async def handle_webhook(payload: WebhookPayload, x_auth_token: Optional[str] = Header(None)) -> OrderResponse:
    """Endpoint principal /webhook."""
    await verify_token(x_auth_token)
    logger.info(f"Inbound /webhook payload: {payload.model_dump_json(indent=2)}")
    order_id = await process_webhook_alert(payload.model_dump())
    status = "ok" if order_id else "failed"
    return OrderResponse(status=status, order_ids=[order_id] if order_id else [])


async def handle_tradingview_alert(alert: TradingViewAlert, x_auth_token: Optional[str] = Header(None)) -> OrderResponse:
    """Endpoint /tv-alert para señales de TradingView."""
    await verify_token(x_auth_token)
    logger.info(f"Inbound /tv-alert payload: {alert.model_dump_json(indent=2)}")

    data = {}

    # Intentar parsear 'message' como JSON
    if alert.message:
        try:
            data = json.loads(alert.message)
        except json.JSONDecodeError:
            # Intentar parseo simple: "NVDA CALL" o "NVDA C 2"
            pattern = re.compile(
                r"^\s*(?P<ticker>[A-Z]{2,5})\s+(?P<signal>CALL|PUT|C|P)\s*(?P<quantity>\d+)?\s*$",
                re.IGNORECASE,
            )
            match = pattern.search(alert.message)
            if match:
                gd = match.groupdict()
                data["ticker"] = gd["ticker"].upper()
                sig = gd["signal"].upper()
                data["signal"] = "CALL" if sig == "C" else ("PUT" if sig == "P" else sig)
                if gd.get("quantity"):
                    data["quantity"] = int(gd["quantity"])
                logger.info(f"Parseado: {data}")

    # Fallback a campos del payload directamente
    if not data:
        if alert.ticker and alert.signal:
            data = {"ticker": alert.ticker, "signal": alert.signal, "quantity": alert.quantity}
        elif alert.ticker and alert.contract:
            data = {"ticker": alert.ticker, "signal": alert.contract, "quantity": alert.quantity or 1}

    order_id = await process_webhook_alert(data)
    status = "ok" if order_id else "failed"
    return OrderResponse(status=status, order_ids=[order_id] if order_id else [])
