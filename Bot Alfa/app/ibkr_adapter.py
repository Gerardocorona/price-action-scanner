"""
Adaptador de BrokerInterface para IBKR usando ib_insync (Async).

Este módulo implementa la interfaz requerida por contract_selector.py
usando la implementación de bracket orders existente en ib_client.py.
"""

import logging
from typing import Dict, List, Literal, Optional
import datetime as dt
import asyncio
import math

from .ib_client import client as ib_client
from .models import OptionContractData
from .contract_selector import BrokerInterface

logger = logging.getLogger("ibg.ibkr_adapter")


class IBKRBrokerAdapter(BrokerInterface):
    """
    Implementación de BrokerInterface para IBKR usando ib_client (Async).
    """

    def __init__(self):
        self.ib_client = ib_client
        # Cache para almacenar contratos seleccionados en premarket
        self._contract_cache: Dict[str, OptionContractData] = {}

    async def get_account_balance(self) -> float:
        """
        Obtiene el balance actual de la cuenta IBKR.
        """
        try:
            return await self.ib_client.get_account_balance()
        except Exception as e:
            logger.error(f"Error obteniendo balance: {e}")
            return 10000.0  # Safe fallback

    async def get_option_chain(self, ticker: str) -> List[dict]:
        """
        Obtiene la cadena de opciones para un ticker con caché inteligente.
        """
        try:
            # Importar sistema de caché
            from .option_chain_cache import get_cached_option_chain
            
            # Usar versión cacheada (esta llamará a get_option_chain_direct si hay MISS)
            return await get_cached_option_chain(self, ticker)
        
        except Exception as e:
            logger.error(f"❌ Error en sistema de caché para {ticker}: {e}")
            return await self.get_option_chain_direct(ticker)

    async def get_option_chain_direct(self, ticker: str) -> List[dict]:
        """
        Obtiene la cadena de opciones directamente del cliente IBKR (Bypass caché).
        """
        try:
            logger.debug(f"Adapter: Fetching RAW option chain for {ticker}")
            res = await self.ib_client.get_option_chain(ticker)
            return res
        except Exception as e:
            logger.error(f"❌ Error obteniendo cadena directa para {ticker}: {e}")
            return []

    async def get_fast_contract(self, ticker: str, direction: str, distance: int = 0) -> Optional[dict]:
        """
        Obtiene un contrato específico usando el método Fast Track.
        """
        try:
            return await self.ib_client.get_fast_contract(ticker, direction, distance)
        except Exception as e:
            logger.error(f"❌ Error en Fast Track Adapter para {ticker}: {e}")
            return None

    async def place_bracket_order_complete(
        self,
        option_symbol: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        qty: int,
        use_trailing_stop: bool = False,
        trailing_percent: float = 10.0
    ) -> Optional[int]:
        """
        Coloca una orden bracket completa.
        """
        return await self.ib_client.place_bracket_order_complete(
            option_symbol=option_symbol,
            entry_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            qty=qty,
            use_trailing_stop=use_trailing_stop,
            trailing_percent=trailing_percent
        )

    async def execute_trade(self, symbol: str, action: Literal["BUY", "SELL"], quantity: int, limit_price: float, tp_pct: float, sl_pct: float) -> Optional[str]:
        """
        Ejecuta una operación con bracket order.
        """
        try:
            order_id = await self.ib_client.place_bracket_order(
                symbol=symbol,
                action=action,
                quantity=quantity,
                limit_price=limit_price,
                tp_pct=tp_pct,
                sl_pct=sl_pct
            )
            return str(order_id)
        except Exception as e:
            logger.error(f"Error ejecutando trade para {symbol}: {e}")
            return None

    async def get_open_trades(self) -> List[dict]:
        """
        Obtiene los trades abiertos actuales.
        """
        try:
            return await self.ib_client.get_open_trades()
        except Exception as e:
            logger.error(f"Error obteniendo trades abiertos: {e}")
            return []

    async def get_positions(self) -> List[dict]:
        """
        Obtiene las posiciones actuales.
        """
        try:
            return await self.ib_client.get_positions()
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []

    async def get_daily_executions(self) -> List[dict]:
        """
        Obtiene las ejecuciones del día.
        """
        try:
            return await self.ib_client.get_daily_executions()
        except Exception as e:
            logger.error(f"Error obteniendo ejecuciones: {e}")
            return []

    async def get_option_quote(self, option_symbol: str) -> Dict[str, float]:
        """
        Obtiene el quote de una opción.
        """
        try:
            # Por ahora usamos req_mkt_data con un dict mínimo
            ticker = await self.ib_client.req_mkt_data({"localSymbol": option_symbol, "secType": "OPT", "exchange": "SMART", "currency": "USD"})
            return {
                "bid": ticker.bid, 
                "ask": ticker.ask,
                "last": ticker.last,
                "close": ticker.close
            }
        except Exception as e:
            logger.error(f"Error obteniendo quote para {option_symbol}: {e}")
            return {"bid": 0.0, "ask": 0.0, "last": 0.0, "close": 0.0}

# Instancia global
ibkr_broker = IBKRBrokerAdapter()
