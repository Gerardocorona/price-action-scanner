"""
Modelos de datos para el SPX Options Heat Map.

HeatMapEntry: dataclass con toda la información de un contrato SPX en tiempo real.
Pydantic models: respuestas de la API REST.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


@dataclass
class HeatMapEntry:
    """Un contrato de la cadena SPX con datos en tiempo real."""
    strike: float = 0.0
    right: str = ""            # "C" o "P"
    expiry: str = ""           # "20260412" (YYYYMMDD)
    con_id: int = 0

    # Precios
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    last: float = 0.0
    spread: float = 0.0       # ask - bid
    spread_pct: float = 0.0   # spread / mid

    # Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0

    # Volumen
    volume: int = 0
    open_interest: int = 0

    # Scoring (calculado por la fórmula del Champion)
    score: float = 0.0

    # Metadata
    last_update: float = 0.0  # time.time()
    is_stale: bool = True     # True hasta que llega el primer tick

    def update_spread(self):
        """Recalcula spread y mid."""
        if self.bid > 0 and self.ask > 0:
            self.mid = (self.bid + self.ask) / 2.0
            self.spread = self.ask - self.bid
            self.spread_pct = self.spread / self.mid if self.mid > 0 else 999.0
        elif self.last > 0:
            self.mid = self.last
            self.spread = 0.0
            self.spread_pct = 0.0


# ── Pydantic Models (API responses) ──────────────────────────────────────────

class HeatMapEntryResponse(BaseModel):
    """Respuesta JSON para un contrato del heat map."""
    strike: float
    right: str
    expiry: str
    con_id: int
    bid: float
    ask: float
    mid: float
    spread_pct: float
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    volume: int
    score: float
    last_update: float
    is_stale: bool


class HeatMapResponse(BaseModel):
    """Respuesta del endpoint /api/live/heat_map."""
    direction: str
    count: int
    spx_price: float
    entries: List[HeatMapEntryResponse]
    stream_ready: bool
    timestamp: str


class BestContractResponse(BaseModel):
    """Respuesta del endpoint /api/live/best_contract."""
    direction: str
    found: bool
    strike: Optional[float] = None
    right: Optional[str] = None
    expiry: Optional[str] = None
    con_id: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread_pct: Optional[float] = None
    delta: Optional[float] = None
    score: Optional[float] = None
    spx_price: float = 0.0
    source: str = "heat_map"  # "heat_map" o "chain_fallback"


class StreamStatusResponse(BaseModel):
    """Respuesta del endpoint /api/live/stream_status."""
    connected: bool
    ready: bool
    client_id: int
    spx_price: float
    total_subscriptions: int
    active_calls: int
    active_puts: int
    stale_entries: int
    last_recenter: Optional[str] = None
    uptime_seconds: float = 0.0
