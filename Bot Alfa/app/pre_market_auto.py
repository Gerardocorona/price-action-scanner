import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
import zoneinfo

from .ib_client import client
from .observability import observability
from .config import get_settings

logger = logging.getLogger("ibg.pre_market")

# Configuración de zona horaria (New York)
NY_TZ = zoneinfo.ZoneInfo("America/New_York")

class PreMarketAuto:
    def __init__(self):
        self.settings = get_settings()
        self._running = False
        self._check_time = time(9, 0) # 9:00 AM ET
        self._checked_today = False

    async def start(self):
        """Inicia el planificador de validación pre-mercado."""
        self._running = True
        logger.info("⏰ Pre-Market Auto-Check Scheduled for 09:00 AM ET")
        asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        self._running = False

    async def _scheduler_loop(self):
        while self._running:
            try:
                now_ny = datetime.now(NY_TZ)
                
                # Reset flag at midnight
                if now_ny.hour == 0 and now_ny.minute == 0:
                    self._checked_today = False

                # Check if it's time (9:00 AM - 9:05 AM window) and not checked yet
                # Also run if we just started and it's between 9:00 and 9:30 (Market Open)
                is_pre_market_window = (
                    now_ny.time() >= self._check_time and 
                    now_ny.time() < time(9, 30)
                )
                
                if is_pre_market_window and not self._checked_today:
                    logger.info("🚀 Iniciando Validación Pre-Mercado Automática...")
                    await self.run_checks()
                    self._checked_today = True
                
            except Exception as e:
                logger.error(f"Error en scheduler pre-mercado: {e}")
            
            await asyncio.sleep(60) # Check every minute

    async def run_checks(self):
        """Ejecuta la validación profunda de sistemas."""
        logger.info("🔍 Ejecutando Lista de Verificación de Vuelo (Pre-Market)...")
        
        issues = []
        
        # 1. Verificar Conexión IBKR
        if not client.is_connected():
            issues.append("❌ IBKR Desconectado")
        else:
            logger.info("✅ IBKR Conectado")
            
            # 2. Verificar Datos de Mercado (Heartbeat)
            try:
                spy_ticker = await client.get_ticker_info("SPY")
                if spy_ticker and spy_ticker.last > 0:
                    logger.info(f"✅ Datos de Mercado Vivos (SPY: ${spy_ticker.last})")
                else:
                    issues.append("⚠️ Datos de Mercado Congelados (SPY sin precio)")
            except Exception as e:
                issues.append(f"❌ Error verificando Datos de Mercado: {e}")

            # 3. Verificar Cuenta y Permisos
            try:
                balance = await client.get_account_balance()
                logger.info(f"✅ Balance Verificado: ${balance:,.2f}")
            except Exception as e:
                issues.append(f"❌ Error verificando Balance: {e}")

        # 4. Verificar Ngrok (Webhook)
        # Usamos la lógica de observability que ya chequea esto
        await observability.check_ngrok()
        ngrok_status = observability.component_status.get("ngrok", {}).get("status")
        if ngrok_status != "active":
            issues.append(f"❌ Ngrok no está activo (Estado: {ngrok_status})")
        else:
            logger.info("✅ Ngrok Activo y Público")

        # REPORTE FINAL
        if issues:
            error_msg = "\n".join(issues)
            logger.error(f"🛑 FALLO EN VALIDACIÓN PRE-MERCADO:\n{error_msg}")
            observability._alert(
                "critical", "Pre-Market Check", "system_check_failed",
                "Fallo en validación automática pre-mercado",
                "El bot no está listo para operar",
                "Revisar logs y reiniciar componentes afectados"
            )
        else:
            logger.info("✨ TODOS LOS SISTEMAS OPERATIVOS. LISTO PARA LA APERTURA. ✨")
            observability._alert(
                "informational", "Pre-Market Check", "system_ready",
                "Validación Pre-Mercado Exitosa",
                "Sistema listo para operar",
                "Monitoreo activo"
            )

pre_market_auto = PreMarketAuto()
