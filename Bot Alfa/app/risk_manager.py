import asyncio
import logging
from typing import Dict, Any

from .config import get_settings
from .ib_client import client
from .circuit_breaker import order_circuit_breaker
from .observability import observability

logger = logging.getLogger("ibg.risk_manager")

class RiskManager:
    def __init__(self):
        self.settings = get_settings()
        self._running = False
        self._daily_pnl = 0.0
        self._max_loss_triggered = False

    async def start(self):
        """Inicia el monitor de riesgo."""
        self._running = True
        logger.info("🛡️ Risk Manager (Digital CFO) Iniciado")
        asyncio.create_task(self._monitor_loop())

    async def stop(self):
        self._running = False

    async def _monitor_loop(self):
        while self._running:
            try:
                await self.check_daily_risk()
            except Exception as e:
                logger.error(f"Error en monitor de riesgo: {e}")
            
            await asyncio.sleep(15)  # Verificar cada 15s

    async def check_daily_risk(self):
        """Verifica si se ha alcanzado el límite de pérdida diaria."""
        if not client.is_connected():
            return

        try:
            # Obtener PnL Realizado y No Realizado
            portfolio = await client.get_portfolio()
            
            # BUGFIX: Proteger contra portfolio None o vacío
            if not portfolio:
                return
            
            total_unrealized = sum((p.get('unrealizedPNL') or 0) for p in portfolio)
            total_realized = sum((p.get('realizedPNL') or 0) for p in portfolio)
            
            self._daily_pnl = total_realized + total_unrealized
            
            # Obtener Balance Actual (NetLiquidation)
            net_liquidation = await client.get_account_balance()
            
            # BUGFIX: Proteger contra net_liquidation None o 0
            if not net_liquidation or net_liquidation <= 0:
                logger.debug("[RISK] Balance no disponible aún. Saltando verificación.")
                return
            
            # Calcular Balance Inicial del Día (Aproximado)
            starting_balance = net_liquidation - self._daily_pnl
            
            # BUGFIX: Proteger contra starting_balance negativo o cero
            if starting_balance <= 0:
                starting_balance = net_liquidation  # Fallback seguro
            
            # Calcular Límite de Pérdida en Dólares
            loss_limit_amount = starting_balance * self.settings.max_daily_loss_pct
            limit = -abs(loss_limit_amount)
            
            if self._daily_pnl <= limit:
                if not self._max_loss_triggered:
                    logger.critical(f"🚨 MAX DAILY LOSS REACHED: ${self._daily_pnl:.2f} (Limit: ${limit:.2f} | {self.settings.max_daily_loss_pct*100}%)")
                    logger.critical("⛔ ACTIVATING HARD STOP. TRADING BLOCKED.")
                    
                    # Abrir Circuit Breaker permanentemente para el día
                    # Forzamos el estado OPEN
                    # Como pybreaker no tiene "force open", simulamos fallos o usamos una bandera global
                    # Pero pybreaker se resetea. 
                    # MEJOR: Usamos una variable en RiskManager que el ContractSelector consulte,
                    # O saturamos el circuit breaker.
                    # Por ahora, usaremos el circuit breaker y una alerta crítica.
                    
                    # Hack para abrir el breaker
                    # Simulamos fallos masivos para abrirlo
                    for _ in range(10):
                        try:
                            order_circuit_breaker.call(lambda: (_ for _ in ()).throw(Exception("Risk Stop")))
                        except: pass
                        
                    self._max_loss_triggered = True
                    
                    observability._alert(
                        "critical", "Risk Manager", "max_daily_loss",
                        f"Pérdida diaria excedida: ${self._daily_pnl:.2f} (Límite: ${limit:.2f})",
                        "Trading detenido",
                        "Revisar posiciones y reiniciar mañana"
                    )
            else:
                # Si nos recuperamos (raro, pero posible), podríamos resetear, 
                # pero por seguridad, una vez tocado el fondo, mejor quedarse quieto o requerir intervención manual.
                pass

        except Exception as e:
            logger.error(f"Error calculando riesgo: {e}")

    async def emergency_panic(self) -> Dict[str, Any]:
        """
        EJECUTA EL PROTOCOLO DE PÁNICO (DEFCON 1).
        1. Cancela todas las órdenes.
        2. Cierra todas las posiciones a mercado.
        3. Bloquea el sistema.
        """
        logger.critical("☢️ INICIANDO PROTOCOLO DE PÁNICO ☢️")
        
        results = {
            "cancelled_orders": 0,
            "closed_positions": 0,
            "errors": []
        }
        
        # 1. Bloquear sistema (Circuit Breaker)
        try:
            for _ in range(10):
                try:
                    order_circuit_breaker.call(lambda: (_ for _ in ()).throw(Exception("Panic Button")))
                except: pass
            logger.info("✅ Circuit Breaker ABIERTO (Trading Bloqueado)")
        except Exception as e:
            results["errors"].append(f"Circuit Breaker Error: {e}")

        if not client.is_connected():
            logger.error("❌ No hay conexión con IBKR para ejecutar pánico.")
            results["errors"].append("No IBKR Connection")
            return results

        # 2. Cancelar órdenes pendientes
        try:
            # Necesitamos acceso a reqGlobalCancel o similar en el bridge
            # Por ahora, iteramos open trades si es posible, o usamos el global cancel de IB
            # client._bridge.ib.reqGlobalCancel() es lo más rápido
            client._bridge.ib.reqGlobalCancel()
            logger.info("✅ Solicitud de cancelación global enviada.")
            results["cancelled_orders"] = "Global Cancel Sent"
        except Exception as e:
            logger.error(f"Error cancelando órdenes: {e}")
            results["errors"].append(f"Cancel Error: {e}")

        # 3. Cerrar todas las posiciones
        try:
            portfolio = await client.get_portfolio()
            for item in portfolio:
                contract_dict = item["contract"]
                position = item["position"]
                
                if position != 0:
                    logger.info(f"🔻 Cerrando posición en {contract_dict['localSymbol']} (Qty: {position})")
                    # Reconstruir contrato para cierre
                    from ib_insync import Contract
                    c = Contract(**contract_dict)
                    c.exchange = "SMART" # Asegurar exchange
                    
                    # Usar client.close_position que ya maneja la lógica de mercado
                    # Pero close_position espera un dict, se lo pasamos
                    # Modificamos el dict para asegurar que tenga lo necesario
                    contract_dict['exchange'] = "SMART"
                    
                    ok = await client.close_position(contract_dict)
                    if ok:
                        results["closed_positions"] += 1
                    else:
                        results["errors"].append(f"Failed to close {contract_dict['localSymbol']}")
                        
        except Exception as e:
            logger.error(f"Error cerrando posiciones: {e}")
            results["errors"].append(f"Close Positions Error: {e}")

        observability._alert(
            "critical", "Risk Manager", "panic_button",
            "Botón de Pánico Activado",
            "Todas las posiciones cerradas y órdenes canceladas",
            "Sistema detenido manualmente"
        )
        
        return results

    def get_status(self):
        return {
            "running": self._running,
            "daily_pnl": self._daily_pnl,
            "max_loss_triggered": self._max_loss_triggered,
            "limit_pct": self.settings.max_daily_loss_pct
        }

risk_manager = RiskManager()
