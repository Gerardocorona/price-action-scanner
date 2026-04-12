import asyncio
import logging
from datetime import datetime, time, timedelta
import zoneinfo
from typing import Dict, List

from .ib_client import client
from .observability import observability
from .risk_manager import risk_manager
from .config import get_settings

logger = logging.getLogger("ibg.post_session")
NY_TZ = zoneinfo.ZoneInfo("America/New_York")

class PostSessionAnalyst:
    def __init__(self):
        self.settings = get_settings()
        self._running = False
        self._report_time = time(16, 15) # 4:15 PM ET (15 mins after close)
        self._reported_today = False

    async def start(self):
        """Inicia el planificador del reporte post-sesión."""
        self._running = True
        logger.info("📊 Post-Session Analyst Scheduled for 04:15 PM ET")
        asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        self._running = False

    async def _scheduler_loop(self):
        while self._running:
            try:
                now_ny = datetime.now(NY_TZ)
                
                # Reset flag at midnight
                if now_ny.hour == 0 and now_ny.minute == 0:
                    self._reported_today = False

                # Check if it's time
                if (now_ny.time() >= self._report_time and 
                    now_ny.time() < time(16, 30) and 
                    not self._reported_today):
                    
                    logger.info("📝 Generando Reporte Post-Sesión...")
                    await self.generate_report()
                    self._reported_today = True
                
            except Exception as e:
                logger.error(f"Error en scheduler post-sesión: {e}")
            
            await asyncio.sleep(60)

    async def generate_report(self):
        """Recopila datos y genera el análisis del día."""
        try:
            # 1. Datos Financieros
            pnl = risk_manager._daily_pnl
            balance = await client.get_account_balance()
            
            # 2. Datos Operativos (Ejecuciones)
            executions = await client.get_daily_executions()
            
            # Agrupar por Ticker
            ticker_stats: Dict[str, float] = {}
            total_trades = 0
            winning_trades = 0
            
            # Analizar ejecuciones (simplificado, asumiendo que podemos deducir PnL por ticker)
            # Como get_daily_executions devuelve fills individuales, necesitamos agrupar.
            # Una forma más fácil es usar el PnL realizado reportado por el portfolio o risk_manager
            # Pero para "Best Ticker" necesitamos desglose.
            # Intentaremos deducir del historial de fills si tienen realizedPNL
            
            for exc in executions:
                # exc is a dict from bridge
                if exc.get('realizedPNL', 0.0) != 0:
                    sym = exc.get('symbol', 'UNKNOWN')
                    val = exc.get('realizedPNL', 0.0)
                    ticker_stats[sym] = ticker_stats.get(sym, 0.0) + val
                    total_trades += 1 # Contamos cierres como trades completados
                    if val > 0: winning_trades += 1

            best_ticker = max(ticker_stats.items(), key=lambda x: x[1])[0] if ticker_stats else "N/A"
            worst_ticker = min(ticker_stats.items(), key=lambda x: x[1])[0] if ticker_stats else "N/A"
            
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

            # 3. Datos de Salud (Observability)
            obs_report = observability.get_status_report()
            signals_rx = obs_report["metrics"]["signals_received"]
            alerts_count = len(obs_report["active_alerts"])
            
            # 4. Construir Reporte
            report_lines = [
                "📊 REPORTE DIARIO DE OPERACIONES",
                f"📅 Fecha: {datetime.now(NY_TZ).strftime('%Y-%m-%d')}",
                "----------------------------------------",
                f"💰 PnL del Día:      ${pnl:,.2f}",
                f"🏦 Balance Final:    ${balance:,.2f}",
                "----------------------------------------",
                f"📈 Trades Cerrados:  {total_trades}",
                f"🏆 Win Rate:         {win_rate:.1f}%",
                f"🌟 Mejor Ticker:     {best_ticker} (${ticker_stats.get(best_ticker, 0):,.2f})",
                f"💀 Peor Ticker:      {worst_ticker} (${ticker_stats.get(worst_ticker, 0):,.2f})",
                "----------------------------------------",
                f"📡 Señales TV:       {signals_rx}",
                f"🚨 Alertas Sistema:  {alerts_count}",
                "----------------------------------------",
                "💡 CONCLUSIÓN:",
                self._generate_conclusion(pnl, win_rate, alerts_count)
            ]
            
            full_report = "\n".join(report_lines)
            logger.info(f"\n{full_report}\n")
            
            # Enviar como alerta informativa para que quede registrada
            observability._alert(
                "informational", "Post-Session Analyst", "daily_report",
                f"Resumen del día: PnL ${pnl:.2f}",
                "Análisis completado",
                "Ver logs para detalle"
            )
            
            # 5. Si es viernes, generar el resumen de investigación semanal
            if datetime.now(NY_TZ).weekday() == 4: # 4 = Friday
                logger.info("📅 Es viernes. Generando resumen semanal de investigación...")
                from .research_summary import generate_weekly_research_report
                generate_weekly_research_report()
            
        except Exception as e:
            logger.error(f"Error generando reporte: {e}", exc_info=True)

    def _generate_conclusion(self, pnl: float, win_rate: float, alerts: int) -> str:
        if pnl > 0:
            mood = "Excelente trabajo." if pnl > 100 else "Día positivo."
            if alerts > 0: mood += " Revisar alertas técnicas."
            return mood
        elif pnl < 0:
            return "Día de pérdidas. Analizar entradas y respetar Stop Loss."
        else:
            return "Día plano (Break-even)."

post_session_analyst = PostSessionAnalyst()
