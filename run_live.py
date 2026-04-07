"""
run_live.py — Lanzador del Price Action Scanner en modo producción
=================================================================
Ejecutado por INICIAR_SISTEMA.bat

Activa el scanner con auto_execute=True para que las señales válidas
se envíen automáticamente al Bot Alfa (IBKR via localhost:8001).

Para cambiar entre DRY-RUN y PRODUCCIÓN:
  dry_run=True  → Calcula qty y loguea pero NO envía orden (seguro para probar)
  dry_run=False → Envía órdenes reales a IBKR (PRODUCCIÓN)

Variables de entorno opcionales (para sobreescribir sin editar código):
  PA_DRY_RUN=0          → Activa producción (0=False, 1=True)
  PA_RISK_PCT=0.20      → Riesgo por trade (20%)
  BOT_BASE_URL=http://localhost:8001
"""

import asyncio
import logging
import os
import sys

# ── CONFIGURACIÓN DESDE VARIABLES DE ENTORNO ──────────────────────────────────
DRY_RUN  = os.environ.get("PA_DRY_RUN", "1") != "0"   # default: DRY-RUN activado
RISK_PCT = float(os.environ.get("PA_RISK_PCT", "0.20"))
BOT_URL  = os.environ.get("BOT_BASE_URL", "http://localhost:8001")

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scanner_output.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_live")

# ── BANNER ────────────────────────────────────────────────────────────────────
print()
print("=" * 62)
print("  PRICE ACTION SCANNER — SPX 0DTE")
print("  Metodología: Eduardo (PRN-Million plus)")
print(f"  Modo:        {'⚠️  DRY-RUN (sin órdenes reales)' if DRY_RUN else '🚀 PRODUCCIÓN — Enviando a IBKR'}")
print(f"  Riesgo/trade: {RISK_PCT*100:.0f}% del balance")
print(f"  Bot Alfa:    {BOT_URL}")
print("=" * 62)
print()

if not DRY_RUN:
    print("  ⚡ MODO PRODUCCIÓN ACTIVADO — Las órdenes se enviarán a IBKR")
    print("  Para volver a DRY-RUN cierra y edita: set PA_DRY_RUN=1")
    print()


# ── IMPORTAR SCANNER ──────────────────────────────────────────────────────────
try:
    from price_action_scanner.pa_scanner import PriceActionScanner
except ImportError as e:
    logger.error(f"No se pudo importar PriceActionScanner: {e}")
    logger.error("Asegúrate de correr desde la carpeta price-action-scanner/")
    sys.exit(1)

# ── INTENTAR CARGAR IBCLIENT ──────────────────────────────────────────────
ib_client = None
try:
    from TradingEngine.ib.ib_client import IBClient
    ib_client = IBClient()
    logger.info("✅ IBClient conectado a IBKR (puerto 7497)")
except Exception as e:
    logger.warning(f"⚠️  IBClient no disponible: {e}")
    logger.warning("El scanner correrá sin datos de barras (modo demo)")
    logger.info("Asegúrate de que TWS/IB Gateway está corriendo en puerto 7497")


# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────
async def main():
    import signal

    scanner = PriceActionScanner(
        auto_execute=True,
        bot_url=BOT_URL,
        risk_pct=RISK_PCT,
        dry_run=DRY_RUN,
        ib_client=ib_client,
    )

    loop = asyncio.get_event_loop()

    def handle_exit(sig, frame):
        logger.info("Señal de detención recibida. Cerrando scanner...")
        loop.create_task(scanner.stop())

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    logger.info("Scanner iniciado. Esperando barras de 2m cerradas...")
    logger.info("Presiona Ctrl+C para detener.")

    try:
        await scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()
        stats = scanner.get_session_stats()
        print()
        print("=" * 62)
        print(f"  SESIÓN FINALIZADA")
        print(f"  Duración:   {stats['elapsed_minutes']} minutos")
        print(f"  Detectadas: {stats['signals_detected']} señales")
        print(f"  Enviadas:   {stats['signals_sent']} órdenes")
        print(f"  Rechazadas: {stats['signals_rejected']}")
        print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
