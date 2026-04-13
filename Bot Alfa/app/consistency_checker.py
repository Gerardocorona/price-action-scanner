import asyncio
import logging
from typing import List, Dict
from datetime import datetime
from .ib_client import client
from .observability import observability

logger = logging.getLogger("ibg.consistency")

class ConsistencyMonitor:
    """
    Monitor de consistencia de portafolio.
    Detecta posiciones cortas accidentales en opciones y las cierra de emergencia.
    """
    
    def __init__(self, interval_seconds: int = 30):
        self.interval = interval_seconds
        self._stop_event = asyncio.Event()
        self._task = None

    async def start(self):
        if self._task is not None:
            return
        logger.info(f"🚀 Iniciando Consistency Monitor (intervalo: {self.interval}s)")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        if self._task:
            self._stop_event.set()
            await self._task
            self._task = None
            logger.info("🛑 Consistency Monitor detenido.")

    async def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                if client.is_connected():
                    await self.check_portfolio_integrity()
            except Exception as e:
                logger.error(f"Error en Consistency Monitor: {e}")
            
            await asyncio.sleep(self.interval)

    async def check_portfolio_integrity(self):
        """Revisa si hay posiciones cortas en opciones (prohibido por estrategia)."""
        portfolio = await client.get_portfolio()
        
        for item in portfolio:
            pos = item.get("position", 0)
            contract = item.get("contract", {})
            symbol = contract.get("localSymbol", "Unknown")
            
            # Solo auditamos opciones (multiplier '100')
            # Si la posición es negativa, es un "Naked Short" accidental
            if pos < 0:
                logger.warning(f"🚨 INCONSISTENCIA DETECTADA: Posición corta en {symbol} ({pos} contratos)")
                
                # Reportar a Observability
                observability._alert(
                    severity="critical",
                    component="Portfolio integrity",
                    failure_type="accidental_short",
                    message=f"Detectada posición corta prohibida en {symbol}: {pos}",
                    impact="Riesgo ilimitado / Violación de estrategia",
                    action="Cerrando posición inmediatamente (KILL SWITCH)"
                )
                
                # EJECUTAR EL KILL SWITCH: COMPRAR PARA CERRAR
                try:
                    logger.info(f"☢️ KILL SWITCH: Cerrando {symbol}...")
                    # contract_dict para close_position
                    contract_dict = {
                        "conId": contract.get("conId"),
                        "symbol": contract.get("symbol"),
                        "secType": "OPT",
                        "exchange": "SMART",
                        "currency": "USD"
                    }
                    success = await client.close_position(contract_dict)
                    if success:
                        logger.info(f"✅ Kill Switch exitoso para {symbol}.")
                    else:
                        logger.error(f"❌ Falló el Kill Switch para {symbol}.")
                except Exception as e:
                    logger.error(f"Error crítico ejecutando Kill Switch: {e}")

# Instancia global
consistency_monitor = ConsistencyMonitor(interval_seconds=30)
