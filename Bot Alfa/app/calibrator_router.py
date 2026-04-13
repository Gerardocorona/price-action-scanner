"""
calibrator_router.py
─────────────────────
Endpoints FastAPI del Calibrador Post-Sesión SPX v1.0

Rutas:
  GET  /calibrator              → Página HTML del Diario de Sesión
  GET  /calibrator/diary        → JSON con señales del día + estadísticas
  GET  /calibrator/diary/{date} → JSON con señales de una fecha específica
  POST /calibrator/label        → Guardar etiqueta experta de una señal
  POST /calibrator/label/batch  → Guardar múltiples etiquetas de una vez
  GET  /calibrator/stats        → Estadísticas históricas de etiquetado
  POST /calibrator/flush        → Forzar persistencia del buffer (debug/test)
"""

import logging
import os
from datetime import datetime
from typing import Optional

import pytz
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .post_session_calibrator import (
    get_today_diary,
    flush_to_db,
    post_session_calibrator,
)
from .calibrator_db import (
    get_session_diary,
    save_expert_label,
    get_label_stats,
    get_unlabeled_count,
)

logger = logging.getLogger("calibrator_router")
ET = pytz.timezone("America/New_York")
router = APIRouter(prefix="/calibrator", tags=["calibrator"])


# ─────────────────────────────────────────────────────────────────────────────
# MODELOS PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────
class ExpertLabel(BaseModel):
    signal_id: int
    verdict: str                    # 'CORRECT' | 'WRONG' | 'MISSED'
    factor_macro: bool = False
    factor_sector: bool = False
    factor_timing: bool = False
    factor_volume: bool = False
    factor_spread: bool = False
    factor_other: str = ""
    notes: str = ""


class BatchLabels(BaseModel):
    labels: list[ExpertLabel]


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/diary", response_class=JSONResponse)
async def get_diary_today():
    """Retorna el Diario de Sesión del día actual."""
    try:
        diary = get_today_diary()
        return JSONResponse(content=diary)
    except Exception as e:
        logger.error(f"Error obteniendo diario: {e}")
        return JSONResponse(
            content={"error": str(e)}, status_code=500
        )


@router.get("/diary/{session_date}", response_class=JSONResponse)
async def get_diary_by_date(session_date: str):
    """
    Retorna el Diario de Sesión de una fecha específica.
    Formato de fecha: YYYY-MM-DD
    """
    try:
        signals = get_session_diary(session_date)
        unlabeled = get_unlabeled_count(session_date)

        executed = [s for s in signals if s["signal_type"] == "EXECUTED"]
        rejected = [s for s in signals if s["signal_type"] == "REJECTED"]
        labeled  = [s for s in signals if s.get("verdict") is not None]
        winners  = [s for s in executed if s.get("pnl_pct") and s["pnl_pct"] > 0]

        return JSONResponse(content={
            "session_date": session_date,
            "signals": signals,
            "stats": {
                "total_signals": len(signals),
                "executed":      len(executed),
                "rejected":      len(rejected),
                "labeled":       len(labeled),
                "unlabeled":     unlabeled,
                "winners":       len(winners),
                "win_rate":      round(len(winners) / len(executed) * 100, 1) if executed else 0,
            },
        })
    except Exception as e:
        logger.error(f"Error obteniendo diario de {session_date}: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/label", response_class=JSONResponse)
async def save_label(label: ExpertLabel):
    """Guarda la etiqueta experta de una señal individual."""
    try:
        success = save_expert_label(label.signal_id, {
            "verdict":       label.verdict,
            "factor_macro":  int(label.factor_macro),
            "factor_sector": int(label.factor_sector),
            "factor_timing": int(label.factor_timing),
            "factor_volume": int(label.factor_volume),
            "factor_spread": int(label.factor_spread),
            "factor_other":  label.factor_other,
            "notes":         label.notes,
        })
        if success:
            return JSONResponse(content={
                "status": "ok",
                "signal_id": label.signal_id,
                "verdict": label.verdict,
            })
        else:
            return JSONResponse(
                content={"status": "error", "message": "Signal no encontrada"},
                status_code=404,
            )
    except Exception as e:
        logger.error(f"Error guardando etiqueta: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/label/batch", response_class=JSONResponse)
async def save_labels_batch(batch: BatchLabels):
    """Guarda múltiples etiquetas en una sola llamada."""
    saved = 0
    errors = []
    for label in batch.labels:
        try:
            success = save_expert_label(label.signal_id, {
                "verdict":       label.verdict,
                "factor_macro":  int(label.factor_macro),
                "factor_sector": int(label.factor_sector),
                "factor_timing": int(label.factor_timing),
                "factor_volume": int(label.factor_volume),
                "factor_spread": int(label.factor_spread),
                "factor_other":  label.factor_other,
                "notes":         label.notes,
            })
            if success:
                saved += 1
            else:
                errors.append(label.signal_id)
        except Exception as e:
            errors.append(label.signal_id)
            logger.error(f"Error en batch label {label.signal_id}: {e}")

    return JSONResponse(content={
        "status": "ok",
        "saved": saved,
        "errors": errors,
        "total": len(batch.labels),
    })


@router.get("/stats", response_class=JSONResponse)
async def get_calibration_stats(days: int = 30):
    """Retorna estadísticas históricas de etiquetado de los últimos N días."""
    try:
        stats = get_label_stats(days)
        return JSONResponse(content={"days": days, **stats})
    except Exception as e:
        logger.error(f"Error obteniendo stats: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/flush", response_class=JSONResponse)
async def force_flush():
    """
    Fuerza la persistencia del buffer a la DB.
    Útil para testing o si el usuario quiere etiquetar antes del cierre oficial.
    """
    try:
        count = flush_to_db()
        return JSONResponse(content={
            "status": "ok",
            "signals_saved": count,
            "message": f"{count} señales persistidas en la base de datos",
        })
    except Exception as e:
        logger.error(f"Error en flush: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def get_calibrator_page():
    """Sirve la interfaz HTML del Calibrador Post-Sesión."""
    try:
        html_path = os.path.join(
            os.path.dirname(__file__), "static", "calibrator.html"
        )
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Calibrador no disponible</h1><p>calibrator.html no encontrado</p>",
            status_code=404,
        )
    except Exception as e:
        return HTMLResponse(
            content=f"<h1>Error</h1><p>{e}</p>", status_code=500
        )
