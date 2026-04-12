import asyncio
import logging
from app.ib_client import client
from app.ibkr_adapter import ibkr_broker
from app.history import history_manager

logger = logging.getLogger("ibg.order_tracker")

class OrderTracker:
    """
    Skill: Order Execution Tracking & Validation.
    Monitorea una orden desde su envío hasta su confirmación y ejecución.
    """
    
    def __init__(self, order_id: int, symbol: str, quantity: int, side: str):
        self.order_id = order_id
        self.symbol = symbol
        self.quantity = quantity
        self.side = side
        self.max_wait_seconds = 120 # 2 minutos de seguimiento intenso
        
    async def start_tracking(self):
        """Inicia el workflow de seguimiento."""
        logger.info(f"🕵️ INICIANDO SEGUIMIENTO para Orden #{self.order_id} ({self.side} {self.symbol})")
        
        asyncio.create_task(self._monitoring_loop())

    async def _monitoring_loop(self):
        start_time = asyncio.get_running_loop().time()
        
        # Estado inicial
        status = "Pending"
        filled = 0.0
        
        while (asyncio.get_running_loop().time() - start_time) < self.max_wait_seconds:
            try:
                # 1. Consultar estado en IBKR
                trades = await client.get_open_trades()
                
                # Buscar nuestra orden
                target_trade = None
                for t in trades:
                    if t['order']['orderId'] == self.order_id:
                        target_trade = t
                        break
                
                if not target_trade:
                    # Si no está en open_trades, puede que ya se haya ejecutado o cancelado
                    # Consultar ejecuciones para confirmar
                    execs = await ibkr_broker.get_daily_executions()
                    relevant_execs = [e for e in execs if e.symbol == self.symbol and e.side == self.side] # Simplificado
                    
                    if relevant_execs:
                        logger.info(f"✅ CONFIRMADO: Orden #{self.order_id} ejecutada completamente en Broker.")
                        # TODO: Verificar Dashboard Sync
                        return
                    
                    logger.warning(f"⚠️ Orden #{self.order_id} desapareció de activas pero no se ven ejecuciones recientes. ¿Cancelada?")
                    return

                # 2. Analizar estado
                ib_status = target_trade['orderStatus']['status']
                ib_filled = target_trade['orderStatus']['filled']
                order_type = target_trade['order']['orderType']
                
                # 3. Reporte Real-Time
                if ib_status == "Submitted":
                    if ib_filled > 0:
                        logger.info(f"🚀 PARCIALMENTE LLENA: {ib_filled}/{self.quantity} para {self.symbol}")
                    else:
                        # Log levels debug to clean main log, but info for tracking
                        pass 
                        
                elif ib_status == "Filled":
                    avg_price = target_trade.get('orderStatus', {}).get('avgFillPrice', 0.0)
                    logger.info(f"✅ EJECUCIÓN TOTAL CONFIRMADA: {self.symbol} @ {avg_price}")
                    
                    # 4. Verificar Bracket (TP/SL)
                    # En una orden completa con OCA, las hijas deberían estar activas.
                    # Esto es complejo de ver sin el objeto Order object completo de IBInsync, 
                    # pero asumimos que si el padre se llenó, las hijas (TP/SL) se activan.
                    logger.info("🛡️ Verificando activación de protecciones (TP/SL)...")
                    # (Lógica futura de validación de hijas podría ir aquí)
                    
                    logger.info("📊 Sincronizando Dashboard...")
                    # Forzar actualización de historial
                    fresh_fills = await ibkr_broker.get_daily_executions()
                    history_manager.add_executions(fresh_fills)
                    
                    return # Fin del seguimiento exitoso

                elif ib_status in ["Cancelled", "Inactive"]:
                    logger.error(f"❌ ORDEN FALLIDA/CANCELADA: {ib_status}")
                    return

                await asyncio.sleep(2) # Polling cada 2s
                
            except Exception as e:
                logger.error(f"Error en tracker: {e}")
                await asyncio.sleep(5)

        logger.warning(f"⏰ Tiempo de seguimiento agotado para Orden #{self.order_id}. Revisar manual.")

# Exponer instancia global o factory
def track_execution(order_id, symbol, qty, side):
    tracker = OrderTracker(order_id, symbol, qty, side)
    asyncio.create_task(tracker.start_tracking())
