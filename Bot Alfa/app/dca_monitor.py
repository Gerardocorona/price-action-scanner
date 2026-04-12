"""
Monitor de Estrategia DCA (Dollar Cost Averaging)
"""
import asyncio
import logging
import math
import time
from typing import Set

from .config import get_settings
from .ib_client import client as ib_client

logger = logging.getLogger("ibg.dca_monitor")

class DCAMonitor:
    def __init__(self):
        self.running = False
        self._dca_applied_contracts: dict = {} # Store conIds -> attempts count
        # Tracking state: {conId: {"lowest_price": float, "monitoring": bool}}
        self._tracking_state = {} 
        self._lock = asyncio.Lock()

    async def start(self):
        """Inicia el bucle de monitoreo."""
        if self.running: return
        self.running = True
        logger.info("🚀 Iniciando Monitor DCA...")
        asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Detiene el monitoreo."""
        self.running = False
        logger.info("🛑 Deteniendo Monitor DCA...")

    async def _monitor_loop(self):
        settings = get_settings()
        if not settings.dca_enabled:
            logger.info("⚠️ DCA está deshabilitado en configuración.")
            return

        while self.running:
            try:
                if not ib_client.is_connected():
                    await asyncio.sleep(5)
                    continue

                # Obtener posiciones (ahora son dicts)
                positions = await ib_client.get_positions()
                
                for pos in positions:
                    if pos['position'] <= 0: continue
                    
                    contract_dict = pos['contract']
                    con_id = contract_dict.get('conId') or contract_dict.get('localSymbol')
                    
                    # Verificar si ya aplicamos DCA
                    if con_id in self._dca_applied_contracts:
                        continue
                        
                    # Solicitar datos de mercado usando el puente
                    try:
                        ticker = await ib_client.req_mkt_data(contract_dict)
                        current_price = ticker.last
                    except Exception as e:
                        # logger.debug(f"Error obteniendo precio para {contract_dict.get('localSymbol')}: {e}")
                        continue
                        
                    if current_price <= 0:
                        continue

                    cost_basis = pos['avgCost'] * pos['position']
                    
                    # Detectar multiplicador según tipo de contrato
                    # Opciones estándar: x100, Futuros: x1 (por ahora asumimos opciones)
                    sec_type = contract_dict.get('secType', 'OPT')
                    multiplier = int(contract_dict.get('multiplier', 100 if sec_type == 'OPT' else 1))
                    
                    market_value = current_price * pos['position'] * multiplier
                    
                    if cost_basis <= 0: continue
                    
                    unrealized_pnl_pct = (market_value - cost_basis) / cost_basis
                    
                    # --- LÓGICA TRAILING BUY ---
                    
                    # Inicializar estado si no existe
                    if con_id not in self._tracking_state:
                        self._tracking_state[con_id] = {
                            "monitoring": False,
                            "lowest_price": current_price
                        }
                    
                    state = self._tracking_state[con_id]
                    
                    # 1. Verificar Trigger de Entrada a Monitoreo
                    if not state["monitoring"]:
                        if unrealized_pnl_pct <= settings.dca_trigger_pct:
                            attempts = self._dca_applied_contracts.get(con_id, 0)
                            
                            if attempts >= settings.dca_max_attempts:
                                logger.warning(f"💀 ALERTA DE CIERRE: {contract_dict.get('symbol')} cayó a {unrealized_pnl_pct:.2%} después de {attempts} intentos DCA.")
                                logger.warning(f"   Ejecutando CIERRE FORZADO para asumir pérdidas.")
                                
                                async with self._lock:
                                    success = await ib_client.close_position(contract_dict)
                                    if success:
                                        logger.info(f"✅ Posición cerrada exitosamente (Stop Loss final).")
                                        self._dca_applied_contracts[con_id] = 999 
                                    else:
                                        logger.error(f"❌ Falló el cierre forzado de {contract_dict.get('localSymbol')}")
                                continue

                            logger.warning(f"📉 ALERTA DCA ({attempts+1}/{settings.dca_max_attempts}): {contract_dict.get('symbol')} cayó a {unrealized_pnl_pct:.2%}. Iniciando vigilancia de rebote...")
                            state["monitoring"] = True
                            state["lowest_price"] = current_price
                    
                    # 2. Si estamos monitoreando (Trailing)
                    else:
                        if current_price < state["lowest_price"]:
                            state["lowest_price"] = current_price
                        
                        bounce_target = state["lowest_price"] * (1 + settings.dca_bounce_pct)
                        
                        if current_price >= bounce_target:
                            logger.info(f"📈 REBOTE CONFIRMADO para {contract_dict.get('symbol')}: ${current_price:.2f} >= ${bounce_target:.2f} (Min: ${state['lowest_price']:.2f})")
                            
                            async with self._lock:
                                attempts = self._dca_applied_contracts.get(con_id, 0)
                                if attempts >= settings.dca_max_attempts: continue
                                
                                # Ejecutar DCA
                                success = await ib_client.place_dca_order(
                                    contract_dict=contract_dict,
                                    current_qty=pos['position'],
                                    current_avg_cost=pos['avgCost'],
                                    new_capital=cost_basis,
                                    tp_percent=settings.dca_tp_percent,
                                    sl_percent=settings.sl_percent
                                )
                                
                                if success:
                                    self._dca_applied_contracts[con_id] = attempts + 1
                                    logger.info(f"✅ DCA #{attempts+1} Aplicado exitosamente para {contract_dict.get('localSymbol')}")
                                    del self._tracking_state[con_id]
                                else:
                                    logger.error(f"❌ Falló DCA #{attempts+1} para {contract_dict.get('localSymbol')}")
                                    state["monitoring"] = False

            except Exception as e:
                logger.error(f"Error en monitor DCA: {e}")
                
            await asyncio.sleep(5)

dca_monitor = DCAMonitor()
