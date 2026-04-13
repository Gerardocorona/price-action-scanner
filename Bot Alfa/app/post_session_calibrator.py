"""
post_session_calibrator.py
──────────────────────────
Calibrador Post-Sesión SPX v1.0

Responsabilidades:
  1. Recopilar todas las señales del día al cierre del mercado (4:00 PM ET)
  2. Generar el Diario de Sesión con contexto completo de cada señal
  3. Recibir etiquetas del experto (✅ CORRECT / ❌ WRONG / ➕ MISSED)
  4. Aplicar las correcciones como ajustes de parámetros al sistema

Integración:
  - Se inicia desde app/main.py en el evento startup
  - Expone métodos para los endpoints de la API
  - Escribe en calibrator.db (no toca trading_lab.db)
"""

import asyncio
import logging
import json
import os
import pytz
from datetime import datetime, date, time as dtime
from typing import Optional
from pathlib import Path

from .calibrator_db import (
    init_db,
    insert_session_signal,
    get_session_diary,
    save_expert_label,
    get_unlabeled_count,
    get_label_stats,
)

logger = logging.getLogger("post_session_calibrator")
ET = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# BUFFER EN MEMORIA: Señales del día actual
# El bot registra aquí cada señal mientras opera. Al cierre, se persisten todas.
# ─────────────────────────────────────────────────────────────────────────────
_signal_buffer: list = []          # Lista de dicts con señales del día
_session_closed_today: bool = False
_last_session_date: Optional[str] = None


def register_signal(
    strategy_id: str,
    symbol: str,
    direction: str,
    signal_type: str,          # 'EXECUTED' | 'REJECTED' | 'MISSED'
    context: dict = None,      # Contexto de mercado en ese momento
    trade_result: dict = None, # Resultado si ya cerró (pnl_pct, exit_reason, etc.)
    reject_reason: str = None,
):
    """
    Registra una señal en el buffer del día.
    Llamar desde el execution service y el scanner service.

    Parámetros de context esperados:
        spx_price, rsi, volume_ratio, bb_position, vix_level, market_regime,
        option_symbol, option_strike, option_expiry, entry_price
    """
    now_et = datetime.now(ET)
    ctx = context or {}
    result = trade_result or {}

    signal = {
        "session_date":  now_et.strftime("%Y-%m-%d"),
        "signal_time":   now_et.strftime("%H:%M:%S"),
        "strategy_id":   strategy_id,
        "symbol":        symbol,
        "direction":     direction,
        "signal_type":   signal_type,
        # Contexto
        "spx_price":     ctx.get("spx_price"),
        "rsi":           ctx.get("rsi"),
        "volume_ratio":  ctx.get("volume_ratio"),
        "bb_position":   ctx.get("bb_position"),
        "vix_level":     ctx.get("vix_level"),
        "market_regime": ctx.get("market_regime"),
        # Contrato
        "option_symbol": ctx.get("option_symbol"),
        "option_strike": ctx.get("option_strike"),
        "option_expiry": ctx.get("option_expiry"),
        "entry_price":   ctx.get("entry_price"),
        # Resultado
        "exit_price":    result.get("exit_price"),
        "pnl_pct":       result.get("pnl_pct"),
        "exit_reason":   result.get("exit_reason"),
        # Rechazo
        "reject_reason": reject_reason,
        # JSON completo para debug
        "raw_json": json.dumps({
            "context": ctx,
            "result": result,
            "reject_reason": reject_reason,
        }, default=str),
    }

    _signal_buffer.append(signal)
    logger.debug(
        f"[CALIBRATOR] Señal registrada: {symbol} {direction} "
        f"[{signal_type}] @ {signal['signal_time']}"
    )


def update_signal_result(
    symbol: str,
    strategy_id: str,
    exit_price: float,
    pnl_pct: float,
    exit_reason: str,
):
    """
    Actualiza el resultado de una señal EXECUTED cuando el trade cierra.
    Busca la última señal EXECUTED del símbolo y estrategia en el buffer.
    """
    for signal in reversed(_signal_buffer):
        if (signal["symbol"] == symbol
                and signal["strategy_id"] == strategy_id
                and signal["signal_type"] == "EXECUTED"
                and signal.get("exit_price") is None):
            signal["exit_price"] = exit_price
            signal["pnl_pct"] = pnl_pct
            signal["exit_reason"] = exit_reason
            logger.debug(
                f"[CALIBRATOR] Resultado actualizado: {symbol} "
                f"pnl={pnl_pct:.1f}% exit={exit_reason}"
            )
            return
    logger.debug(f"[CALIBRATOR] No se encontró señal abierta para {symbol} {strategy_id}")


def flush_to_db() -> int:
    """
    Persiste todas las señales del buffer a la base de datos.
    Retorna el número de señales guardadas.
    """
    global _signal_buffer, _session_closed_today, _last_session_date

    if not _signal_buffer:
        logger.info("[CALIBRATOR] Buffer vacío — nada que persistir")
        return 0

    saved = 0
    for signal in _signal_buffer:
        try:
            insert_session_signal(signal)
            saved += 1
        except Exception as e:
            logger.error(f"[CALIBRATOR] Error persistiendo señal: {e}")

    session_date = _signal_buffer[0]["session_date"] if _signal_buffer else "unknown"
    logger.info(
        f"[CALIBRATOR] ✅ {saved}/{len(_signal_buffer)} señales persistidas "
        f"para la sesión {session_date}"
    )

    _last_session_date = session_date
    _signal_buffer = []
    _session_closed_today = True
    return saved


def get_today_diary() -> dict:
    """
    Retorna el Diario de Sesión del día actual (o el último día disponible).
    Incluye señales del buffer en memoria + las ya persistidas en DB.
    """
    now_et = datetime.now(ET)
    today = now_et.strftime("%Y-%m-%d")

    # Señales ya en DB
    db_signals = get_session_diary(today)

    # Señales en buffer (aún no persistidas)
    buffer_signals = [
        {**s, "verdict": None, "label_notes": None, "labeled_at": None}
        for s in _signal_buffer
        if s["session_date"] == today
    ]

    all_signals = db_signals + buffer_signals

    # Estadísticas del día
    executed  = [s for s in all_signals if s["signal_type"] == "EXECUTED"]
    rejected  = [s for s in all_signals if s["signal_type"] == "REJECTED"]
    labeled   = [s for s in all_signals if s.get("verdict") is not None]
    unlabeled = len(all_signals) - len(labeled)

    winners = [s for s in executed if s.get("pnl_pct") and s["pnl_pct"] > 0]
    losers  = [s for s in executed if s.get("pnl_pct") and s["pnl_pct"] < 0]
    total_pnl_pct = sum(s["pnl_pct"] for s in executed if s.get("pnl_pct")) if executed else 0

    # Estado del mercado
    hhmm = now_et.strftime("%H:%M")
    if "09:30" <= hhmm <= "16:00":
        market_status = "OPEN"
    elif hhmm > "16:00":
        market_status = "CLOSED"
    else:
        market_status = "PRE_MARKET"

    return {
        "session_date": today,
        "market_status": market_status,
        "current_time": now_et.strftime("%H:%M ET"),
        "signals": all_signals,
        "stats": {
            "total_signals":    len(all_signals),
            "executed":         len(executed),
            "rejected":         len(rejected),
            "winners":          len(winners),
            "losers":           len(losers),
            "win_rate":         round(len(winners) / len(executed) * 100, 1) if executed else 0,
            "total_pnl_pct":    round(total_pnl_pct, 2),
            "labeled":          len(labeled),
            "unlabeled":        unlabeled,
            "labeling_pct":     round(len(labeled) / len(all_signals) * 100, 1) if all_signals else 0,
        },
        "calibration_insights": _compute_insights(all_signals),
    }


def _compute_insights(signals: list) -> dict:
    """
    Analiza las etiquetas del experto y genera insights de calibración.
    Solo se activa cuando hay suficientes etiquetas (mínimo 3).
    """
    labeled = [s for s in signals if s.get("verdict") is not None]
    if len(labeled) < 3:
        return {"status": "INSUFFICIENT_DATA", "min_required": 3, "current": len(labeled)}

    wrong_entries  = [s for s in labeled if s["verdict"] == "WRONG"]
    missed_entries = [s for s in labeled if s["verdict"] == "MISSED"]
    correct        = [s for s in labeled if s["verdict"] == "CORRECT"]

    insights = {
        "status": "READY",
        "total_labeled": len(labeled),
        "correct_pct": round(len(correct) / len(labeled) * 100, 1),
        "wrong_pct":   round(len(wrong_entries) / len(labeled) * 100, 1),
        "missed_pct":  round(len(missed_entries) / len(labeled) * 100, 1),
        "suggestions": [],
    }

    # Sugerencia: demasiadas entradas incorrectas → filtros más estrictos
    if len(wrong_entries) / len(labeled) > 0.3:
        insights["suggestions"].append({
            "type": "TIGHTEN_FILTER",
            "message": f"{len(wrong_entries)} entradas incorrectas ({insights['wrong_pct']}%). "
                       "Considera aumentar el umbral de RSI o reducir el rango de precio.",
            "priority": "HIGH",
        })

    # Sugerencia: muchas entradas perdidas → filtros demasiado estrictos
    if len(missed_entries) >= 2:
        insights["suggestions"].append({
            "type": "RELAX_FILTER",
            "message": f"{len(missed_entries)} oportunidades perdidas. "
                       "El sistema puede estar siendo demasiado conservador.",
            "priority": "MEDIUM",
        })

    # Análisis de factores más frecuentes en entradas incorrectas
    if wrong_entries:
        factor_counts = {}
        for s in wrong_entries:
            for factor in ["factor_macro", "factor_sector", "factor_timing",
                           "factor_volume", "factor_spread"]:
                if s.get(factor):
                    factor_counts[factor] = factor_counts.get(factor, 0) + 1

        if factor_counts:
            top_factor = max(factor_counts, key=factor_counts.get)
            factor_names = {
                "factor_macro":  "contexto macro adverso",
                "factor_sector": "sector débil",
                "factor_timing": "hora del día inapropiada",
                "factor_volume": "volumen sospechoso",
                "factor_spread": "spread demasiado alto",
            }
            insights["top_wrong_factor"] = {
                "factor": top_factor,
                "name": factor_names.get(top_factor, top_factor),
                "count": factor_counts[top_factor],
            }
            insights["suggestions"].append({
                "type": "FACTOR_INSIGHT",
                "message": f"El factor más común en entradas incorrectas: "
                           f"'{factor_names.get(top_factor, top_factor)}' "
                           f"({factor_counts[top_factor]} veces).",
                "priority": "MEDIUM",
            })

    return insights


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER: Cierre automático de sesión a las 4:00 PM ET
# ─────────────────────────────────────────────────────────────────────────────
class PostSessionCalibrator:
    """
    Agente que corre en background y gestiona el ciclo de vida del Diario de Sesión.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._flushed_today = False

    async def start(self):
        """Inicia el scheduler en background."""
        init_db()  # Asegurar que las tablas existen
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("✅ [CALIBRATOR] Post-Session Calibrator iniciado")

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _scheduler_loop(self):
        """
        Loop que verifica cada minuto si es hora de cerrar la sesión.
        Cierre de sesión: 4:05 PM ET (5 minutos después del cierre oficial).
        Reset del flag: 12:00 AM ET del día siguiente.
        """
        while True:
            try:
                now_et = datetime.now(ET)
                hhmm = now_et.strftime("%H:%M")
                today = now_et.strftime("%Y-%m-%d")

                # Reset diario a medianoche
                if hhmm == "00:00":
                    self._flushed_today = False
                    logger.info("[CALIBRATOR] 🔄 Reset diario — nueva sesión lista")

                # Flush al cierre del mercado (4:05 PM ET)
                if hhmm >= "16:05" and not self._flushed_today:
                    logger.info("[CALIBRATOR] 🔔 Mercado cerrado — persistiendo señales del día...")
                    count = flush_to_db()
                    self._flushed_today = True
                    if count > 0:
                        logger.info(
                            f"[CALIBRATOR] 📋 Diario de Sesión listo: {count} señales. "
                            f"Visita /calibrator para etiquetar."
                        )

            except Exception as e:
                logger.error(f"[CALIBRATOR] Error en scheduler: {e}")

            await asyncio.sleep(60)  # Verificar cada minuto


# Instancia singleton
post_session_calibrator = PostSessionCalibrator()
