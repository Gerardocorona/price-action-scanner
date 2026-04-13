# models.py

from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field


@dataclass
class OptionAlert:
    """Modelo para la alerta recibida de TradingView."""

    ticker: str
    signal: str  # 'CALL' o 'PUT'
    quantity: int = 1
    tp_percent: Optional[float] = None
    sl_percent: Optional[float] = None
    trace_id: Optional[str] = None


@dataclass
class TickerInfo:
    """Modelo para la información del activo subyacente."""

    last: float
    close: float
    symbol: Optional[str] = None
    bid: Optional[float] = 0.0
    ask: Optional[float] = 0.0
    # Puedes añadir más campos si los necesitas (e.g., open, high, low)


@dataclass
class OptionContractData:
    """
    Modelo para la data detallada de un contrato de opción.
    """

    symbol: str
    local_symbol: str
    strike: float
    right: str  # 'C' o 'P'
    expiry: str

    # Datos de Mercado y Greeks (cruciales para la lógica)
    bid: Optional[float]
    ask: Optional[float]
    mark: Optional[float]  # Precio de mercado (usado como Premium de entrada)
    delta: Optional[float]
    open_interest: Optional[int] = None
    # Añadir otros Greeks o volumen si se usan en el futuro


# Modelos Pydantic usados por FastAPI (compatibilidad con app.main)
class WebhookPayload(BaseModel):
    ticker: str
    signal: str
    quantity: int = 1
    selection: Optional[dict] = None
    tp_percent: Optional[float] = None
    sl_percent: Optional[float] = None
    trace_id: Optional[str] = None

    class Config:
        populate_by_name = True


class TradingViewAlert(BaseModel):
    message: Optional[dict | str] = None
    ticker: Optional[str] = None
    signal: Optional[str] = None
    quantity: Optional[int] = 1
    
    # Nuevos campos para soportar el formato JSON personalizado
    action: Optional[str] = None
    contract: Optional[str] = None
    take_profit_percent: Optional[float] = None
    stop_loss_percent: Optional[float] = None

    def as_webhook_payload(self) -> WebhookPayload:
        """Convierte la alerta en un payload de webhook estandarizado."""
        if isinstance(self.message, str):
            # Parse string message: "TICKER SIGNAL QTY" e.g. "QQQ CALL 2"
            parts = self.message.split()
            ticker = parts[0]
            signal = parts[1]
            quantity = int(parts[2]) if len(parts) > 2 else 1
            return WebhookPayload(ticker=ticker, signal=signal, quantity=quantity, selection={})
        
        if isinstance(self.message, dict):
            return WebhookPayload(**self.message)
            
        if self.ticker and self.signal:
            return WebhookPayload(ticker=self.ticker, signal=self.signal)
            
        raise ValueError("Invalid alert format")


class ManualCloseRequest(BaseModel):
    """Modelo para solicitud de cierre manual de posición."""
    conId: int
    symbol: str
    right: str = "P" # Default Put
    strike: float = 0.0
    expiry: str = ""


class OrderResponse(BaseModel):
    status: str
    order_ids: List[int] = Field(default_factory=list)
    details: Optional[dict] = None
