"""
Monitor de salud de conexiones para el bot.

Ejecuta chequeos periódicos sobre:
- Listener HTTP (FastAPI) y respuesta de /health.
- Puerto de IBKR (TWS/Gateway) y sesiones establecidas.

Acciones:
- Registra cualquier fallo en logs/monitor.log y consola.
- Opcionalmente puede ejecutar un comando de reinicio si el listener HTTP cae
  (controlado por la variable de entorno MONITOR_AUTORESTART=1).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Tuple

import httpx

# Permite ejecutar tanto como módulo (`python -m app.monitor`) como script (`python monitor.py`)
if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from app.config import get_settings  # type: ignore
else:
    from .config import get_settings

# Configuración básica del monitor
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "15"))
AUTO_RESTART = os.getenv("MONITOR_AUTORESTART", "0") == "1"
RESTART_CMD = os.getenv("MONITOR_RESTART_CMD", "start_bot.bat")

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("ibg.monitor")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    fh = logging.FileHandler(LOG_DIR / "monitor.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)


def _netstat_lines() -> Iterable[str]:
    """Devuelve las líneas de netstat -ano (Windows)."""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
        )
        return out.splitlines()
    except Exception as exc:
        logger.error("No se pudo ejecutar netstat: %s", exc)
        return []


def port_status(port: int) -> Tuple[bool, int, set[str]]:
    """
    Devuelve (listening, established_count, pids_established) para un puerto dado.
    """
    listening = False
    established = 0
    pids: set[str] = set()
    token = f":{port}"
    for line in _netstat_lines():
        if token not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        state = parts[-2]
        pid = parts[-1]
        if state.upper() == "LISTENING":
            listening = True
        elif state.upper() == "ESTABLISHED":
            established += 1
            pids.add(pid)
    return listening, established, pids


async def check_http_health(port: int) -> bool:
    """Consulta /health del bot."""
    url = f"http://127.0.0.1:{port}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception as exc:
        logger.error("Fallo al consultar %s: %s", url, exc)
        return False


def attempt_restart() -> None:
    """Ejecuta el comando de reinicio si está habilitado."""
    if not AUTO_RESTART:
        return
    if not RESTART_CMD:
        logger.warning("AUTO_RESTART activo pero MONITOR_RESTART_CMD vacío.")
        return
    try:
        logger.warning("Intentando reiniciar servicio: %s", RESTART_CMD)
        subprocess.Popen(
            RESTART_CMD,
            shell=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as exc:
        logger.error("No se pudo lanzar reinicio (%s): %s", RESTART_CMD, exc)


async def monitor_loop() -> None:
    """Bucle principal de monitoreo."""
    settings = get_settings()
    logger.info(
        "Monitor iniciado (APP_PORT=%s, IB_PORT=%s, intervalo=%ss, auto_restart=%s)",
        settings.app_port,
        settings.ib_port,
        MONITOR_INTERVAL,
        AUTO_RESTART,
    )

    while True:
        try:
            # Listener del bot (HTTP) + health
            app_listening, _, app_pids = port_status(settings.app_port)
            health_ok = await check_http_health(settings.app_port) if app_listening else False
            if not app_listening:
                logger.error("Bot: puerto %s no está en LISTENING.", settings.app_port)
                attempt_restart()
            elif not health_ok:
                logger.error("Bot: /health falló en puerto %s. PIDs: %s", settings.app_port, ",".join(app_pids) or "-")

            # Listener y sesiones IBKR
            ib_listening, ib_established, ib_pids = port_status(settings.ib_port)
            if not ib_listening:
                logger.error("IBKR: puerto %s no está en LISTENING (¿TWS/Gateway caído?).", settings.ib_port)
            elif ib_established == 0:
                logger.warning(
                    "IBKR: puerto %s en LISTENING pero sin sesiones ESTABLISHED. PIDs TWS/Gateway: %s",
                    settings.ib_port,
                    ",".join(ib_pids) or "-",
                )

            await asyncio.sleep(MONITOR_INTERVAL)
        except asyncio.CancelledError:
            logger.info("Monitor cancelado.")
            break
        except Exception as exc:
            logger.error("Error en monitor: %s", exc)
            await asyncio.sleep(MONITOR_INTERVAL)


def run() -> None:
    """Entry point sincrónico."""
    asyncio.run(monitor_loop())


if __name__ == "__main__":
    run()
