"""
pa_scanner.py — Orquestador del Price Action Scanner (SPX 0DTE)
===============================================================
Módulo ADITIVO. No modifica ningún archivo existente del Bot Alfa.

Este es el módulo principal que integra todos los componentes:
  1. PriceActionDetector   — detecta patrones de velas
  2. ConfluenceChecker     — verifica alineación de factores
  3. SignalGenerator       — genera la señal y orden final

Metodología (Eduardo / PRN-Million plus):
  - 1H: Determinar dirección/contexto macro
  - 5m: Identificar estructura y niveles S/R
  - 2m: Esperar patrón + confluencia → entrar

Flujo de operación:
  1. Recibe barras de 1H, 5m y 2m desde IBClient (o datos simulados)
  2. En cada nueva vela de 2m CERRADA: analiza patrón
  3. Si encuentra patrón → verifica confluencia
  4. Si confluencia válida → genera señal → (opcional) envía orden a IBKR

Uso como módulo standalone (backtesting):
    scanner = PriceActionScanner()
    result = scanner.analyze(bars_1h, bars_5m, bars_2m, current_price)

Uso en producción (loop continuo con IBClient):
    scanner = PriceActionScanner(ib_client=ib_client, broker_service=broker)
    await scanner.start()   # loop cada 2 minutos
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import yaml

from .confluence_checker import ConfluenceChecker
from .pa_detector import PriceActionDetector
from .pa_signal_schema import PriceActionSignal
from .signal_generator import SignalGenerator

logger = logging.getLogger("ibg.price_action.scanner")

_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pa_config.yaml")


class PriceActionScanner:
    """
    Scanner de Price Action para SPX/0DTE Options.

    Compatible con la arquitectura del Bot Alfa:
      - Implementa start() / stop() como los demás servicios
      - Usa DBManager existente para persistencia
      - Se integra con BrokerService para ejecución
      - Obtiene datos de IBClient

    Ejemplo de integración en core_orchestrator.py:
        pa_scanner = PriceActionScanner(
            ib_client=ib_client,
            db_manager=db_manager,
            broker_service=broker
        )
        self.services.append(pa_scanner)
    """

    def __init__(
        self,
        ib_client=None,
        db_manager=None,
        broker_service=None,
        config_path: str = _CFG_PATH,
    ):
        self._ib = ib_client
        self._db = db_manager
        self._broker = broker_service
        self._cfg_path = config_path

        # Cargar configuración
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        # Componentes
        self.detector = PriceActionDetector(config_path=config_path)
        self.checker = ConfluenceChecker(config_path=config_path)
        self.generator = SignalGenerator(
            db_manager=db_manager,
            broker_service=broker_service,
            config_path=config_path,
        )

        # Estadísticas de sesión
        self._running = False
        self._signals_detected = 0
        self._signals_sent = 0
        self._signals_rejected = 0
        self._session_start = None

        logger.info("[PA-Scanner] Price Action Scanner inicializado")

    async def start(self):
        """
        Inicia el loop principal del scanner.
        Obtiene barras de IBClient cada 2 minutos (o configurado).
        """
        if self._running:
            return

        self._running = True
        self._session_start = datetime.now()
        logger.info(
            "[PA-Scanner] 🚀 Scanner iniciado. "
            "Metodología: Eduardo (PRN-Million plus). SPX 0DTE."
        )

        while self._running:
            try:
                await self._scan_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PA-Scanner] Error en ciclo de escaneo: {e}", exc_info=True)

            # Esperar ~2 minutos entre ciclos (se sincroniza con cierre de vela)
            await asyncio.sleep(120)

    async def stop(self):
        """Detiene el scanner limpiamente"""
        self._running = False
        logger.info(
            f"[PA-Scanner] Detenido. Sesión: "
            f"{self._signals_detected} detectadas, "
            f"{self._signals_sent} enviadas, "
            f"{self._signals_rejected} rechazadas"
        )

    async def _scan_cycle(self):
        """
        Un ciclo de análisis completo.
        Obtiene barras de IBClient y ejecuta análisis.
        """
        if not self._ib:
            logger.debug("[PA-Scanner] Sin IBClient - modo demo desactivado")
            return

        try:
            # ─ OBTENER BARRAS DE IBKR ───────────────────────────────────
            logger.debug("[PA-Scanner] Obteniendo barras de IBClient...")

            # IBClient debe proporcionar métodos async para obtener datos
            # Estructura esperada: bars = [{'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}, ...]
            bars_1h = await self._ib.get_historical_bars(
                symbol="SPX",
                timeframe="1h",
                count=30
            )
            bars_5m = await self._ib.get_historical_bars(
                symbol="SPX",
                timeframe="5m",
                count=50
            )
            bars_2m = await self._ib.get_historical_bars(
                symbol="SPX",
                timeframe="2m",
                count=30
            )

            # ─ OBTENER PRECIO ACTUAL ────────────────────────────────────
            quote = await self._ib.get_quote(symbol="SPX")
            current_price = quote.get('last', quote.get('close', None))

            if current_price is None:
                logger.warning("[PA-Scanner] No se pudo obtener precio actual")
                return

            # ─ EJECUTAR ANÁLISIS ────────────────────────────────────────
            signal = await self.analyze(
                bars_1h=bars_1h,
                bars_5m=bars_5m,
                bars_2m=bars_2m,
                current_price=current_price,
                send_order=True  # En producción, enviar orden al broker
            )

            if signal:
                logger.info(f"[PA-Scanner] Análisis completado: {signal.summary()}")

        except AttributeError as e:
            logger.debug(f"[PA-Scanner] IBClient no tiene método esperado: {e}")
        except Exception as e:
            logger.error(f"[PA-Scanner] Error en scan_cycle: {e}", exc_info=True)

    async def analyze(
        self,
        bars_1h: List[Dict],
        bars_5m: List[Dict],
        bars_2m: List[Dict],
        current_price: float,
        send_order: bool = False,
    ) -> Optional[PriceActionSignal]:
        """
        Análisis completo: patrón + confluencia + señal.

        Este método es el PUNTO DE ENTRADA principal para análisis.
        Se puede llamar directamente en backtesting o testing.

        Args:
            bars_1h: Barras de 1 hora (mínimo 5, recomendado 20+)
            bars_5m: Barras de 5 minutos (mínimo 5, recomendado 20+)
            bars_2m: Barras de 2 minutos (mínimo 2, recomendado 20+)
                     IMPORTANTE: La última barra debe estar CERRADA
            current_price: Precio actual del subyacente (SPX)
            send_order: Si True y señal válida → enviar al broker

        Returns:
            PriceActionSignal o None si no hay setup
        """
        # ── PASO 1: Detectar patrón en 2m ────────────────────────────────────
        pattern = self.detector.detect_latest(bars_2m)

        if not pattern:
            logger.debug(f"[PA-Scanner] Sin patrón en 2m. Precio={current_price:.2f}")
            return None

        logger.info(
            f"[PA-Scanner] Patrón encontrado: {pattern.pattern_type}({pattern.direction}) "
            f"conf={pattern.confidence:.2f} @ {current_price:.2f}"
        )

        # ── PASO 2: Construir contexto de tendencia ───────────────────────────
        trend = self.checker.build_trend_context(
            bars_1h=bars_1h,
            bars_5m=bars_5m,
            bars_2m=bars_2m,
        )

        # Verificar mercado lateral en 5m (filtro principal)
        if trend.is_lateral_market:
            logger.info(
                f"[PA-Scanner] ⚠️ LATERAL detectado en 5m "
                f"(rango={trend.lateral_range_points:.1f}pts). No operar."
            )
            self._signals_rejected += 1
            return None

        # ── PASO 3: Verificar confluencia ─────────────────────────────────────
        confluence = self.checker.check(
            pattern=pattern,
            trend=trend,
            current_price=current_price,
            bars_5m=bars_5m,
        )

        self._signals_detected += 1

        if not confluence.meets_minimum:
            self._signals_rejected += 1
            logger.debug(
                f"[PA-Scanner] Confluencia insuficiente: "
                f"{confluence.factors_count} factores | {confluence.rejected_reason}"
            )

        # ── PASO 4: Generar señal (se guarda en DB independientemente) ────────
        signal = await self.generator.generate(
            pattern=pattern,
            trend=trend,
            confluence=confluence,
            current_price=current_price,
            send_order=send_order,
        )

        if signal and signal.order_generated:
            self._signals_sent += 1
            logger.info(f"[PA-Scanner] ✅ {signal.summary()}")

        return signal

    def reload_config(self):
        """
        Recarga la configuración de todos los componentes.
        Llamar después de una calibración para aplicar nuevos parámetros.
        """
        self.detector.reload_config()
        self.checker.reload_config()
        self.generator.reload_config()
        logger.info("[PA-Scanner] 🔄 Configuración recargada (post-calibración)")

    def get_session_stats(self) -> Dict:
        """Retorna estadísticas de la sesión actual"""
        elapsed = (datetime.now() - self._session_start).seconds if self._session_start else 0
        return {
            "session_start": self._session_start.isoformat() if self._session_start else None,
            "elapsed_minutes": elapsed // 60,
            "signals_detected": self._signals_detected,
            "signals_sent": self._signals_sent,
            "signals_rejected": self._signals_rejected,
            "conversion_rate": (
                self._signals_sent / self._signals_detected
                if self._signals_detected > 0
                else 0
            ),
        }


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT (para ejecutar como módulo: python -m ...pa_scanner)
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import signal
    import sys

    # Path setup para ejecución standalone
    _SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
    _ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
    _ROOT_DIR = os.path.abspath(os.path.join(_ENGINE_DIR, ".."))
    for p in [_ROOT_DIR, _ENGINE_DIR]:
        if p not in sys.path:
            sys.path.insert(0, p)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Intentar cargar DB
    db = None
    try:
        from TradingEngine.db.db_manager import DBManager

        db = DBManager()
        logger.info("[PA-Scanner] DBManager conectado")
    except Exception as e:
        logger.warning(f"[PA-Scanner] DBManager no disponible: {e}. Corriendo sin persistencia.")

    # Intentar cargar IBClient
    ib = None
    try:
        # Importar desde la arquitectura del Bot Alfa
        from TradingEngine.ib.ib_client import IBClient

        ib = IBClient()
        logger.info("[PA-Scanner] IBClient inicializado")
    except Exception as e:
        logger.warning(f"[PA-Scanner] IBClient no disponible: {e}. Modo demo.")

    scanner = PriceActionScanner(db_manager=db, ib_client=ib)

    loop = asyncio.get_event_loop()

    def handle_exit(sig, frame):
        logger.info("[PA-Scanner] Señal de detención recibida. Apagando...")
        loop.create_task(scanner.stop())

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    logger.info("[PA-Scanner] 🚀 Iniciando en modo producción...")
    if not ib:
        logger.info("[PA-Scanner] ⚠️  IBClient necesario para datos en vivo.")

    try:
        loop.run_until_complete(scanner.start())
    except KeyboardInterrupt:
        pass
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    stats = scanner.get_session_stats()
    logger.info(
        f"[PA-Scanner] Sesión finalizada: "
        f"{stats['signals_detected']} detectadas, "
        f"{stats['signals_sent']} enviadas"
    )
