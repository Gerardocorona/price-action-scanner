import uvicorn
import asyncio
import sys
import logging
import signal
from ib_insync import util

from .config import get_settings

# Configuración crítica para Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _ignore_sigint(signum, frame):
    logging.getLogger("ibg.bot").info(
        "Señal SIGINT ignorada. Usa stop_bot.bat o Ctrl+Break para detener el bot."
    )


def main() -> None:
    settings = get_settings()
    server: uvicorn.Server | None = None

    def handle_sigbreak(signum, frame) -> None:
        logger = logging.getLogger("ibg.bot")
        if server is None:
            logger.warning("Señal SIGBREAK recibida antes de inicializar el servidor.")
            return
        logger.info("Señal SIGBREAK recibida. Deteniendo el bot...")
        server.should_exit = True

    signal.signal(signal.SIGINT, _ignore_sigint)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_sigbreak)

    logger = logging.getLogger("ibg.bot")
    logger.info(
        "Iniciando servidor FastAPI en %s:%s",
        settings.app_host,
        settings.app_port,
    )
    
    # --- PORT SECURITY CHECK ---
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((settings.app_host, settings.app_port))
    sock.close()
    if result == 0:
        logger.warning(f"⚠️ EL PUERTO {settings.app_port} ESTÁ EN USO. Uvicorn intentará tomarlo, pero podría fallar.")
    else:
        logger.info(f"✅ Puerto {settings.app_port} disponible.")
    # ---------------------------

    print(f"\n{'='*50}")
    print(f"🚀 DASHBOARD DISPONIBLE EN:")
    print(f"👉 http://127.0.0.1:{settings.app_port}/dashboard")
    print(f"{'='*50}\n")

    config = uvicorn.Config(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = False
    server.run()


if __name__ == "__main__":
    main()
