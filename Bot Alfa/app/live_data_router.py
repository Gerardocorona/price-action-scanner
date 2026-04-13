"""
Router FastAPI para datos en tiempo real del SPX Options Heat Map.

Endpoints:
- GET /api/live/heat_map?direction=CALL|PUT  → ranking completo
- GET /api/live/best_contract/{direction}     → lookup instantáneo
- GET /api/live/spx_price                     → precio SPX actual
- GET /api/live/stream_status                 → health check del streamer
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Query

from .market_data_models import (
    BestContractResponse,
    HeatMapEntryResponse,
    HeatMapResponse,
    StreamStatusResponse,
)
from .market_data_stream import spx_stream

logger = logging.getLogger("ibg.live_data")

router = APIRouter(prefix="/api/live", tags=["Live Market Data"])


@router.get("/heat_map", response_model=HeatMapResponse)
async def get_heat_map(direction: str = Query("CALL", description="CALL o PUT")):
    """
    Retorna el mapa de calor completo para una dirección, ordenado por score.
    Incluye precios, Greeks, spread y score de cada contrato.
    """
    entries = spx_stream.get_heat_map(direction)

    entry_responses = [
        HeatMapEntryResponse(
            strike=e.strike,
            right=e.right,
            expiry=e.expiry,
            con_id=e.con_id,
            bid=round(e.bid, 2),
            ask=round(e.ask, 2),
            mid=round(e.mid, 2),
            spread_pct=round(e.spread_pct, 4),
            delta=round(e.delta, 4),
            gamma=round(e.gamma, 6),
            theta=round(e.theta, 4),
            vega=round(e.vega, 4),
            iv=round(e.iv, 4),
            volume=e.volume,
            score=round(e.score, 4),
            last_update=e.last_update,
            is_stale=e.is_stale,
        )
        for e in entries
    ]

    return HeatMapResponse(
        direction=direction.upper(),
        count=len(entry_responses),
        spx_price=round(spx_stream.get_spx_price(), 2),
        entries=entry_responses,
        stream_ready=spx_stream.is_ready(),
        timestamp=datetime.now().isoformat(),
    )


@router.get("/best_contract/{direction}", response_model=BestContractResponse)
async def get_best_contract(direction: str):
    """
    Lookup instantáneo del mejor contrato para la dirección dada.
    Este es el endpoint que contract_selector usa internamente (0ms vs 500ms).
    """
    best = spx_stream.get_best_contract(direction)

    if not best:
        return BestContractResponse(
            direction=direction.upper(),
            found=False,
            spx_price=round(spx_stream.get_spx_price(), 2),
            source="heat_map",
        )

    return BestContractResponse(
        direction=direction.upper(),
        found=True,
        strike=best.strike,
        right=best.right,
        expiry=best.expiry,
        con_id=best.con_id,
        bid=round(best.bid, 2),
        ask=round(best.ask, 2),
        mid=round(best.mid, 2),
        spread_pct=round(best.spread_pct, 4),
        delta=round(best.delta, 4),
        score=round(best.score, 4),
        spx_price=round(spx_stream.get_spx_price(), 2),
        source="heat_map",
    )


@router.get("/spx_price")
async def get_spx_price():
    """Precio actual de SPX desde el streamer."""
    return {
        "price": round(spx_stream.get_spx_price(), 2),
        "stream_ready": spx_stream.is_ready(),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/stream_status", response_model=StreamStatusResponse)
async def get_stream_status():
    """Health check completo del streamer de market data."""
    status = spx_stream.get_status()
    return StreamStatusResponse(**status)
