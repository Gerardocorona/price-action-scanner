import logging
import math
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Literal
from dataclasses import dataclass, field

from .contract_selector import BrokerInterface
from .models import OptionContractData

logger = logging.getLogger("ibg.replay_broker")

@dataclass
class ReplayPosition:
    symbol: str
    side: str # "BUY" (Long Option)
    quantity: int
    entry_price: float
    current_price: float
    tp_price: float
    sl_price: float
    entry_time: str
    status: str = "OPEN" # OPEN, CLOSED_TP, CLOSED_SL, CLOSED_MARKET
    pnl: float = 0.0
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None

class ReplayBroker(BrokerInterface):
    """
    Broker simulado que opera sobre datos históricos (CSV).
    Simula precios de opciones usando Delta aproximado (0.5 para ATM).
    """
    def __init__(self, initial_balance: float = 10000.0):
        self.balance = initial_balance
        self.current_ticker_price: float = 0.0
        self.current_time: datetime = datetime.now()
        self.positions: List[ReplayPosition] = []
        self.orders: List[Dict] = []
        self.executions: List[Dict] = []
        
        # Configuración de simulación
        self.option_multiplier = 100
        self.atm_delta = 0.5  # Asumimos Delta 0.5 para opciones ATM
        self.time_decay_per_day = 0.05 # Theta decay simple ($5/día)

    def update_market_state(self, price: float, timestamp: datetime):
        """Actualiza el estado del mercado simulado (Tick)."""
        self.current_ticker_price = price
        self.current_time = timestamp
        self._check_positions()

    def _check_positions(self):
        """Verifica si alguna posición tocó TP o SL basado en el movimiento del subyacente."""
        for pos in self.positions:
            if pos.status != "OPEN":
                continue
            
            # Estimación de precio de opción
            # Cambio en subyacente * Delta
            # Nota: Esto es una simplificación lineal.
            # Si subyacente sube $1, Call sube $0.50.
            
            # Necesitamos saber el precio del subyacente cuando entramos
            # No lo guardamos en ReplayPosition, error de diseño leve, lo inferimos o guardamos.
            # Vamos a asumir que entry_price de la opción corresponde a un underlying_ref.
            # Para simplificar, guardaremos underlying_entry_price en la posición.
            
            pass # La lógica real está en place_bracket_order y el loop externo

    # --- IMPLEMENTACIÓN DE INTERFAZ BROKER ---

    async def get_account_balance(self) -> float:
        return self.balance

    async def get_option_chain(self, ticker: str) -> List[dict]:
        """Genera una cadena de opciones sintética centrada en el precio actual."""
        if self.current_ticker_price <= 0:
            return []
            
        center_strike = round(self.current_ticker_price)
        strikes = [center_strike - 2, center_strike - 1, center_strike, center_strike + 1, center_strike + 2]
        
        chain = []
        # Generar Calls y Puts
        expiry = self.current_time.strftime("%Y%m%d") # Expiración "hoy" para simular 0DTE o semanal
        
        for strike in strikes:
            # Call
            chain.append({
                "symbol": f"{ticker} {expiry} C {strike}",
                "type": "CALL",
                "strike": float(strike),
                "expiry": expiry,
                "dte": 0,
                "bid": self._calculate_option_price(strike, "CALL"),
                "ask": self._calculate_option_price(strike, "CALL") + 0.05, # Spread fijo
                "last": self._calculate_option_price(strike, "CALL")
            })
            # Put
            chain.append({
                "symbol": f"{ticker} {expiry} P {strike}",
                "type": "PUT",
                "strike": float(strike),
                "expiry": expiry,
                "dte": 0,
                "bid": self._calculate_option_price(strike, "PUT"),
                "ask": self._calculate_option_price(strike, "PUT") + 0.05,
                "last": self._calculate_option_price(strike, "PUT")
            })
        return chain

    async def get_option_chain_direct(self, ticker: str) -> List[dict]:
        return await self.get_option_chain(ticker)

    async def get_option_quote(self, option_symbol: str) -> Dict[str, float]:
        """
        Devuelve el precio simulado de la opción.
        Parsea el símbolo 'TICKER EXP TYPE STRIKE' para recalcular.
        """
        try:
            parts = option_symbol.split()
            # Ejemplo: MU 20260124 C 95.0
            strike = float(parts[-1])
            otype = parts[-2] # C o P
            
            price = self._calculate_option_price(strike, "CALL" if otype == "C" else "PUT")
            return {"bid": price, "ask": price + 0.05, "last": price}
        except:
            return {"bid": 1.0, "ask": 1.05, "last": 1.0}

    def _calculate_option_price(self, strike: float, otype: str) -> float:
        """
        Calculadora simple de precio de opción (Modelo Intrinseco + Extrinseco Fijo).
        """
        intrinsic = 0.0
        if otype == "CALL":
            intrinsic = max(0.0, self.current_ticker_price - strike)
        else:
            intrinsic = max(0.0, strike - self.current_ticker_price)
            
        # Valor extrínseco (Time value) simulado
        # Mayor ATM, menor OTM/ITM
        distance = abs(self.current_ticker_price - strike)
        extrinsic = max(0.10, 1.0 - (distance * 0.1)) 
        
        return round(intrinsic + extrinsic, 2)

    async def place_bracket_order_complete(
        self,
        *,
        option_symbol: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        qty: int,
        use_trailing_stop: bool = False,
        trailing_percent: float = 10.0,
    ) -> Optional[str]:
        
        order_id = str(uuid.uuid4())[:8]
        
        # Registrar posición simulada
        pos = ReplayPosition(
            symbol=option_symbol,
            side="BUY",
            quantity=qty,
            entry_price=entry_price,
            current_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            entry_time=self.current_time.isoformat()
        )
        # Hack: Guardar precio subyacente de entrada para cálculos de PnL relativos
        pos.underlying_entry_price = self.current_ticker_price
        
        self.positions.append(pos)
        
        # Registrar ejecución
        self.executions.append({
            "execId": order_id,
            "time": self.current_time.strftime("%Y%m%d  %H:%M:%S"),
            "symbol": option_symbol.split()[0],
            "side": "BOT",
            "shares": float(qty),
            "price": entry_price,
            "contract": {"localSymbol": option_symbol}
        })
        
        logger.info(f"⚡ [REPLAY] Orden Ejecutada: {option_symbol} x{qty} @ ${entry_price:.2f} (Und: ${self.current_ticker_price:.2f})")
        return order_id

    async def get_open_trades(self) -> List[dict]:
        return [{"orderId": "SIM", "contract": {"localSymbol": p.symbol}} for p in self.positions if p.status == "OPEN"]

    async def get_daily_executions(self) -> List[dict]:
        return self.executions

    # --- MÉTODOS DE GESTIÓN DE SIMULACIÓN ---
    
    def check_exit_conditions(self, current_underlying_price: float, timestamp: datetime):
        """
        Revisa si las posiciones abiertas deben cerrarse (TP/SL) basado en el movimiento del subyacente.
        """
        for pos in self.positions:
            if pos.status != "OPEN":
                continue
            
            # Calcular nuevo precio de opción estimado
            # Delta 0.5 aproximado
            price_change = current_underlying_price - pos.underlying_entry_price
            
            # Si es PUT, el cambio es inverso
            is_call = " C " in pos.symbol
            if not is_call:
                price_change = -price_change
                
            estimated_opt_price = pos.entry_price + (price_change * self.atm_delta)
            
            # Actualizar estado
            pos.current_price = estimated_opt_price
            
            # Verificar TP
            if estimated_opt_price >= pos.tp_price:
                self._close_position(pos, estimated_opt_price, "TP", timestamp)
            
            # Verificar SL
            elif estimated_opt_price <= pos.sl_price:
                self._close_position(pos, estimated_opt_price, "SL", timestamp)

    def _close_position(self, pos: ReplayPosition, exit_price: float, reason: str, timestamp: datetime):
        pos.status = f"CLOSED_{reason}"
        pos.exit_time = timestamp.isoformat()
        pos.exit_reason = reason
        pos.pnl = (exit_price - pos.entry_price) * pos.quantity * 100
        self.balance += pos.pnl
        
        logger.info(f"🛑 [REPLAY] Posición Cerrada ({reason}): {pos.symbol} PnL: ${pos.pnl:.2f}")
        
        # Registrar ejecución de venta
        self.executions.append({
            "execId": f"CL-{uuid.uuid4().hex[:4]}",
            "time": timestamp.strftime("%Y%m%d  %H:%M:%S"),
            "symbol": pos.symbol.split()[0],
            "side": "SLD",
            "shares": float(pos.quantity),
            "price": exit_price,
            "realizedPNL": pos.pnl,
            "contract": {"localSymbol": pos.symbol}
        })
