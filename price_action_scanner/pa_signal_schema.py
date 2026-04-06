"""
pa_signal_schema.py — Esquemas de datos para Price Action Scanner
==================================================================
Define todas las estructuras de datos para el ciclo completo de señales:
  1. PatternData      → Patrón detectado en 2m
  2. TrendContext     → Contexto de tendencia (1H/5M/2M)
  3. ConfluenceData   → Verificación de confluencia
  4. OrderData        → Orden a enviar (SL/TP/Trail)
  5. PriceActionSignal→ Señal completa (para DB)
  6. CalibrationLabel → Etiqueta manual de usuario (ground truth)
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from datetime import datetime


@dataclass
class PatternData:
    """Patrón detectado en vela 2m"""
    pattern_type: str           # 'pin_bar', 'engulfing', 'inside_bar', 'shooting_star', 'hammer'
    direction: str              # 'bearish' o 'bullish'
    confidence: float           # 0.0 a 1.0

    # Ratios de la vela
    wick_ratio: float          # Wick / Range
    body_ratio: float          # Body / Range
    volume_ratio: float        # Volume / Average

    # Precio de la vela
    open: float
    high: float
    low: float
    close: float
    volume: float

    # Parámetros usados en detección (para auditoría)
    params_used: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrendContext:
    """Contexto de tendencia en 3 timeframes"""
    trend_1h: str                   # 'bullish', 'bearish', 'lateral'
    trend_5m: str
    trend_2m: str

    # Mercado lateral
    is_lateral_market: bool
    lateral_range_points: float     # Rango de oscilación

    # Posicionamiento de precio vs promedios móviles
    price_vs_ma20: str              # 'above', 'below'
    price_vs_ma200: str             # 'above', 'below'

    # Patrón de ruptura + retroceso
    break_and_retest_detected: bool
    break_direction: Optional[str] = None  # 'up', 'down', 'both'

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConfluenceData:
    """Resultado de verificación de confluencia"""
    # Factores detectados
    factors: List[str] = field(default_factory=list)  # ["nivel_6583.89_en_zona", "trend_1h_bearish", ...]
    factors_count: int = 0

    # Scoring
    score: float = 0.0                              # Suma de pesos
    weights_applied: Dict[str, float] = field(default_factory=dict)  # {"nivel_en_zona": 2.0, ...}

    # Validación
    meets_minimum: bool = False
    min_factors_required: int = 3

    # Si fue rechazado
    rejected_reason: Optional[str] = None  # "lateral_market", "price_not_in_zone", etc.

    # Nivel más cercano
    nearest_level: Optional[float] = None
    distance_to_level: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderData:
    """Datos de orden a enviar al broker"""
    direction: str                  # 'PUT' o 'CALL'
    contracts: int                  # Número de contratos

    # Precios de ejecución
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float

    # Trailing stop
    trail_stop_enabled: bool = False
    trail_stop_activate_at: float = 0.0
    trail_stop_distance: float = 0.0

    # Metadata
    symbol: str = "SPX"
    expiration: str = "0DTE"         # Same-day expiration
    option_type: str = "SPX"         # Type of options

    # Broker reference
    broker_order_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PriceActionSignal:
    """Señal completa para persistencia en DB"""
    # Identificación
    signal_id: str                  # UUID
    timestamp: str                  # ISO format
    session_date: str               # YYYY-MM-DD

    # Datos de entrada
    pattern_data: PatternData
    trend_context: TrendContext
    confluence_data: ConfluenceData
    current_price: float

    # Orden (si fue generada)
    order_generated: bool = False
    order_data: Optional[OrderData] = None

    # Resultados (se llenan después del cierre)
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    pnl_points: Optional[float] = None
    pnl_usd: Optional[float] = None
    exit_reason: Optional[str] = None  # 'tp1', 'tp2', 'sl', 'trail', 'manual'

    # Estado
    status: str = "detected"  # 'detected', 'order_ready', 'order_sent', 'filled', 'closed', 'rejected'

    def summary(self) -> str:
        """Resumen legible de la señal"""
        pattern = f"{self.pattern_data.pattern_type}({self.pattern_data.direction})"
        confluence = f"{self.confluence_data.factors_count} factores"
        if self.order_generated:
            return f"✅ {pattern} | {confluence} | {self.order_data.direction} @ {self.current_price:.2f}"
        else:
            return f"⚠️  {pattern} | {confluence} | Rechazada: {self.confluence_data.rejected_reason}"

    def to_db_dict(self) -> dict:
        """Convierte a diccionario para persistencia en DB"""
        return {
            # Identificación
            'id': self.signal_id,
            'timestamp': self.timestamp,
            'symbol': 'SPX',
            'entry_timeframe': '2m',
            'session_date': self.session_date,

            # Patrón
            'pattern_type': self.pattern_data.pattern_type,
            'pattern_direction': self.pattern_data.direction,
            'pattern_confidence': self.pattern_data.confidence,
            'pattern_wick_ratio': self.pattern_data.wick_ratio,
            'pattern_body_ratio': self.pattern_data.body_ratio,
            'pattern_volume_ratio': self.pattern_data.volume_ratio,
            'detector_params': str(self.pattern_data.params_used),

            # Tendencia
            'trend_1h': self.trend_context.trend_1h,
            'trend_5m': self.trend_context.trend_5m,
            'is_lateral': 1 if self.trend_context.is_lateral_market else 0,
            'break_and_retest': 1 if self.trend_context.break_and_retest_detected else 0,

            # Confluencia
            'confluence_factors': str(self.confluence_data.factors),
            'confluence_score': self.confluence_data.score,
            'confluence_count': self.confluence_data.factors_count,
            'nearest_level': self.confluence_data.nearest_level,
            'rejected_reason': self.confluence_data.rejected_reason,

            # Precio
            'price_at_signal': self.current_price,

            # Orden
            'order_generated': 1 if self.order_generated else 0,
            'order_direction': self.order_data.direction if self.order_data else None,
            'order_contracts': self.order_data.contracts if self.order_data else None,
            'entry_price': self.order_data.entry_price if self.order_data else None,
            'stop_loss': self.order_data.stop_loss if self.order_data else None,
            'take_profit_1': self.order_data.take_profit_1 if self.order_data else None,
            'take_profit_2': self.order_data.take_profit_2 if self.order_data else None,
            'broker_order_id': self.order_data.broker_order_id if self.order_data else None,

            # Resultados
            'exit_price': self.exit_price,
            'exit_time': self.exit_time,
            'pnl_points': self.pnl_points,
            'pnl_usd': self.pnl_usd,
            'exit_reason': self.exit_reason,
            'status': self.status,

            'created_at': datetime.now().isoformat(),
        }


@dataclass
class CalibrationLabel:
    """Etiqueta manual del usuario (ground truth para calibración)"""
    signal_id: str
    session_date: str

    # Ground truth
    setup_valid: int            # 1 = era setup correcto, 0 = falso positivo
    pattern_correct: int        # 1 = patrón detectado correctamente
    confluencia_correct: int    # 1 = confluencia era real

    # Análisis cualitativo
    notes: str = ""             # Observaciones del usuario
    confidence_level: str = "media"  # 'alta', 'media', 'baja'

    # Metadata
    labeled_at: str = field(default_factory=lambda: datetime.now().isoformat())
    labeled_by: str = "usuario"

    def to_db_dict(self) -> dict:
        return asdict(self)
