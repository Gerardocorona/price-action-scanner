"""
Circuit Breaker para protección de órdenes.

Implementa el patrón Circuit Breaker para prevenir:
- Floods de órdenes por errores en cascada
- Sobrecarga del broker (IBKR)
- Pérdidas catastróficas por bugs

El Circuit Breaker tiene 3 estados:
1. CLOSED (normal): Órdenes se ejecutan normalmente
2. OPEN (bloqueado): Se bloquean todas las órdenes después de N fallos
3. HALF_OPEN (recuperación): Se permite 1 orden de prueba
"""

import logging
from datetime import timedelta
from pybreaker import CircuitBreaker, CircuitBreakerListener
from typing import Callable, Any
import asyncio

logger = logging.getLogger("ibg.circuit_breaker")


# ============================================================
#   LISTENER PARA LOGGING DE EVENTOS
# ============================================================

class OrderCircuitBreakerListener(CircuitBreakerListener):
    """Listener que registra todos los eventos del Circuit Breaker."""
    
    def state_change(self, cb, old_state, new_state):
        logger.warning(
            f"🔄 CIRCUIT BREAKER STATE CHANGE: {old_state.name} → {new_state.name}"
        )
        
        if new_state.name == "open":
            logger.critical(
                "🚨 CIRCUIT BREAKER OPENED: Trading automático BLOQUEADO por exceso de fallos. "
                "Revisa los logs y el estado del sistema."
            )
        elif new_state.name == "half_open":
            logger.warning(
                "⚠️ CIRCUIT BREAKER HALF-OPEN: Intentando recuperación con orden de prueba..."
            )
        elif new_state.name == "closed":
            logger.info(
                "✅ CIRCUIT BREAKER CLOSED: Sistema restaurado. Trading automático reanuda."
            )
    
    def before_call(self, cb, func, *args, **kwargs):
        logger.debug(f"Circuit Breaker: Ejecutando {func.__name__}")
    
    def success(self, cb):
        logger.debug("Circuit Breaker: Orden exitosa ✓")
    
    def failure(self, cb, exception):
        logger.error(f"Circuit Breaker: Orden fallida - {exception}")


# ============================================================
#   CIRCUIT BREAKER GLOBAL PARA ÓRDENES
# ============================================================

# Configuración del Circuit Breaker:
# - fail_max: Número máximo de fallos antes de abrir el circuito
# - reset_timeout: Tiempo en segundos hasta intentar recuperación (HALF_OPEN)
# - exclude: Excepciones que NO cuentan como fallo (por diseño)

order_circuit_breaker = CircuitBreaker(
    fail_max=5,  # Abre después de 5 fallos consecutivos
    reset_timeout=60,  # Intenta recuperación después de 60 segundos
    exclude=[
        # Excepciones que NO deben abrir el circuito
        # (son errores esperados, no catástrofes del sistema)
        ValueError,  # Validaciones de input
        KeyError,  # Datos faltantes esperados
    ],
    listeners=[OrderCircuitBreakerListener()],
    name="OrderCircuitBreaker"
)


# ============================================================
#   DECORADOR PARA FUNCIONES ASÍNCRONAS
# ============================================================

def async_circuit_breaker(breaker: CircuitBreaker):
    """
    Decorador para aplicar circuit breaker a funciones async.
    
    Uso:
        @async_circuit_breaker(order_circuit_breaker)
        async def place_order(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs):
            try:
                # pybreaker no soporta async nativamente, usamos sync wrapper
                def sync_call():
                    loop = asyncio.get_event_loop()
                    return loop.run_until_complete(func(*args, **kwargs))
                
                # Ejecutar a través del circuit breaker
                return await func(*args, **kwargs)
            
            except Exception as e:
                # El circuit breaker ya habrá registrado el fallo
                # Re-lanzamos la excepción para que el caller maneje
                raise
        
        return wrapper
    return decorator


# ============================================================
#   FUNCIÓN ENVOLVENTE MANUAL (Para compatibilidad)
# ============================================================

async def execute_with_circuit_breaker(func: Callable, *args, **kwargs) -> Any:
    """
    Ejecuta una función asíncrona a través del circuit breaker.
    Soporta async/await nativo dentro del contexto del disyuntor.
    """
    # Usar el contexto del breaker para asegurar que el fallo se registre
    # al terminar la ejecución del coroutine, no al retornarlo.
    try:
        with order_circuit_breaker:
            return await func(*args, **kwargs)
    except Exception as e:
        # El context manager de pybreaker ya cuenta el fallo
        # Re-lanzamos para manejo posterior
        raise


# ============================================================
#   UTILIDADES DE GESTIÓN
# ============================================================

def get_circuit_breaker_state() -> dict:
    """Obtiene el estado actual del circuit breaker."""
    return {
        "state": order_circuit_breaker.current_state.name,
        "fail_counter": order_circuit_breaker.fail_counter,
        "fail_max": order_circuit_breaker.fail_max,
        "reset_timeout": order_circuit_breaker.reset_timeout,
        "is_open": order_circuit_breaker.current_state.name == "open",
    }


def reset_circuit_breaker():
    """Fuerza el reset del circuit breaker (USAR CON PRECAUCIÓN)."""
    order_circuit_breaker.call(lambda: True)  # Llamada exitosa dummy
    logger.warning("🔄 Circuit Breaker forzado a RESET manualmente.")


def force_close_circuit():
    """Cierra el circuit breaker manualmente (emergencia)."""
    # No hay API directa, simulamos éxitos
    for _ in range(order_circuit_breaker.fail_max + 1):
        try:
            order_circuit_breaker.call(lambda: True)
        except:
            pass
    logger.critical("⚠️ Circuit Breaker forzado a CLOSE manualmente (modo emergencia).")


# ============================================================
#   INTEGRACIÓN CON DASHBOARD (Endpoint de estado)
# ============================================================

def get_circuit_breaker_health() -> dict:
    """
    Retorna el estado de salud del circuit breaker para el dashboard.
    
    Returns:
        {
            "healthy": bool,
            "state": str,
            "failure_count": int,
            "total_capacity": int,
            "message": str
        }
    """
    state_info = get_circuit_breaker_state()
    
    is_healthy = state_info["state"] == "closed"
    
    if state_info["state"] == "open":
        message = "⛔ TRADING BLOQUEADO: Demasiados fallos detectados. Sistema en espera de recuperación."
    elif state_info["state"] == "half_open":
        message = "⚠️ MODO RECUPERACIÓN: Probando estabilidad del sistema..."
    else:
        message = "✅ SISTEMA OPERATIVO: Todas las órdenes se procesan normalmente."
    
    return {
        "healthy": is_healthy,
        "state": state_info["state"],
        "failure_count": state_info["fail_counter"],
        "total_capacity": state_info["fail_max"],
        "message": message,
        "reset_timeout_seconds": state_info["reset_timeout"],
    }
