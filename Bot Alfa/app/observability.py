import asyncio
import logging
import time
# import psutil
# import httpx
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Literal
from dataclasses import dataclass, field, asdict
import uuid

from .ib_client import client
from .config import get_settings

logger = logging.getLogger("ibg.observability")

@dataclass
class AlertPayload:
    component: str
    failure_type: str
    timestamp: str
    severity: Literal["critical", "high", "medium", "informational"]
    potential_impact: str
    recommended_action: str
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SignalEvent:
    trace_id: str
    ticker: str
    signal: str
    timestamp: float
    status: Literal["RECEIVED", "PROCESSING", "ORDER_PLACED", "FAILED", "IGNORED", "ORPHANED"] = "RECEIVED"
    order_id: Optional[int] = None
    error: Optional[str] = None

class ObservabilitySystem:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ObservabilitySystem, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.alerts: List[AlertPayload] = []
        self.max_alerts = 100
        
        # State Tracking
        self.signals: Dict[str, SignalEvent] = {} # trace_id -> SignalEvent
        self.last_dashboard_access: float = 0.0
        self.start_time = time.time()
        
        self.metrics: Dict[str, Any] = {
            "signals_received": 0,
            "orders_placed": 0,
            "api_errors": 0,
            "latency_samples": []
        }
        
        self.component_status: Dict[str, Dict[str, Any]] = {
            "Interactive Brokers": {"status": "UNKNOWN", "details": {}},
            "TradingView": {"status": "UNKNOWN", "details": {}},
            "Trading Bot Core Engine": {"status": "RUNNING", "details": {}},
            "ngrok": {"status": "UNKNOWN", "details": {}},
            "Dashboard": {"status": "UNKNOWN", "details": {}}
        }
        
        self._stop_event = asyncio.Event()
        self._initialized = True
        logger.info("Observability System Initialized (Enhanced)")

    async def start(self):
        """Inicia el bucle de monitoreo en segundo plano."""
        logger.info("Iniciando monitor de observabilidad...")
        asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Detiene el monitor."""
        self._stop_event.set()

    async def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                await self.run_checks()
            except Exception as e:
                logger.error(f"Error en el ciclo de observabilidad: {e}", exc_info=True)
            
            await asyncio.sleep(10)  # Ejecutar cada 10 segundos

    async def run_checks(self):
        """Ejecuta todas las verificaciones de salud."""
        results = await asyncio.gather(
            self.check_ibkr(),
            # self.check_ngrok(),
            self.check_system_resources(),
            self.check_dashboard(),
            self.check_signals_integrity(),
            return_exceptions=True
        )
        
        # Procesar excepciones si las hubo
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Check failed: {res}")

    # ==========================================
    # 1. Interactive Brokers Checks
    # ==========================================
    async def check_ibkr(self):
        """Verifica conexión, autenticación, latencia y estado de órdenes."""
        status = "UP"
        details = {}
        
        try:
            # A. Connection Check
            is_connected = client.is_connected()
            if not is_connected:
                status = "DOWN"
                self._alert("critical", "Interactive Brokers", "connection_lost", 
                           "Pérdida de conexión con IBKR", 
                           "No se pueden ejecutar órdenes", 
                           "Verificar TWS/Gateway y reiniciar conexión")
            
            # B. Latency Check (Ping account balance)
            if is_connected:
                t0 = time.perf_counter()
                await client.get_account_balance()
                latency_ms = (time.perf_counter() - t0) * 1000
                details["latency_ms"] = latency_ms
                
                if latency_ms > 2000:
                    status = "DEGRADED"
                    self._alert("medium", "Interactive Brokers", "high_latency",
                               f"Latencia alta detectada: {latency_ms:.2f}ms",
                               "Posible retraso en ejecución",
                               "Monitorear red y carga de TWS")

                # C. Order State Check
                # Verificar si hay órdenes "colgadas" (Submitted pero no Filled por mucho tiempo)
                # Esto requiere que client.get_open_trades() devuelva info de tiempo, que a veces es compleja.
                # Por ahora, solo contamos órdenes abiertas.
                try:
                    open_trades = await client.get_open_trades()
                    details["open_orders_count"] = len(open_trades)
                    
                    # TODO: Implementar lógica de "stuck orders" si tenemos timestamps en los trades
                except Exception as e:
                    logger.warning(f"Error checking open trades: {e}")

            self.component_status["Interactive Brokers"] = {
                "status": status,
                "last_check": datetime.now(timezone.utc).isoformat(),
                "details": details
            }

        except Exception as e:
            self.component_status["Interactive Brokers"] = {"status": "ERROR", "details": {"error": str(e)}}
            self._alert("high", "Interactive Brokers", "check_failed", str(e), "Estado desconocido", "Revisar logs")

    # ==========================================
    # 2. ngrok Checks
    # ==========================================
    async def check_ngrok(self):
        """Monitorea ngrok (BYPASSED - httpx no instalado)"""
        pass

    # ==========================================
    # 3. System Resources Checks
    # ==========================================
    async def check_system_resources(self):
        """Monitorea CPU y Memoria (BYPASSED - psutil no instalado)"""
        cpu = 0.0 # psutil.cpu_percent(interval=None)
        mem = 0.0 # psutil.virtual_memory().percent
        
        details = {
            "cpu_percent": cpu,
            "memory_percent": mem,
            "uptime_seconds": time.time() - self.start_time
        }
        
        self.component_status["Trading Bot Core Engine"] = {
            "status": "RUNNING",
            "details": details
        }
        
        if cpu > 90:
            self._alert("medium", "Trading Bot Core Engine", "high_cpu", f"CPU al {cpu}%", "Posible lentitud", "Investigar procesos")
        
        if mem > 90:
            if mem > 95:
                self._alert("critical", "Trading Bot Core Engine", "OOM_RISK", f"CRITICAL: Memoria al {mem}%. Iniciando AUTO-RESTART de emergencia.", "Reinicio inminente", "Ninguna (Automático)")
                logger.critical(f"🚨 MEMORY CRITICAL ({mem}%). TRIGGERING RESTART IN 2s...")
                await asyncio.sleep(2)
                self._restart_application()
            else:
                self._alert("high", "Trading Bot Core Engine", "high_memory", f"Memoria al {mem}%", "Riesgo de OOM", "Reiniciar bot si persiste")

    def _restart_application(self):
        """Reinicia la aplicación actual usando el mismo comando de lanzamiento."""
        logger.critical("♻️ EJECUTANDO PROTOCOLO DE AUTO-REINICIO DE EMERGENCIA...")
        try:
            # sys.executable suele ser 'python.exe'
            # sys.argv invoca el módulo o script (ej: ['-m', 'app.main', ...])
            # Reconstruimos comando: python.exe -m app.main ...
            logger.info(f"Restarting with: {sys.executable} {sys.argv}")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            logger.error(f"❌ Falló el auto-reinicio: {e}")
            sys.exit(1) # Panic exit

    # ==========================================
    # 4. Dashboard Checks
    # ==========================================
    async def check_dashboard(self):
        """Verifica si el dashboard está activo (DUMMY - bypass httpx)"""
        self.component_status["Dashboard"] = {
            "status": "ACTIVE",
            "details": {"note": "Monitoreo de latencia bypass (httpx no instalado)"}
        }

        # 2. User Activity Tracking (Existing logic)
        time_since_access = time.time() - self.last_dashboard_access
        user_status = "ACTIVE" if time_since_access < 60 else "IDLE"
        details["user_activity"] = user_status
        details["last_access_seconds_ago"] = int(time_since_access)
        
        self.component_status["Dashboard"] = {
            "status": status,
            "details": details
        }

    # ==========================================
    # 5. Signal Integrity Checks
    # ==========================================
    async def check_signals_integrity(self):
        """Detecta señales huérfanas o fallidas."""
        now = time.time()
        
        # Limpiar señales viejas (> 1 hora)
        to_remove = [tid for tid, s in self.signals.items() if now - s.timestamp > 3600]
        for tid in to_remove:
            del self.signals[tid]
            
        # Buscar señales huérfanas (RECEIVED hace > 30s y no procesadas/fallidas)
        for tid, signal in self.signals.items():
            if signal.status == "RECEIVED" and (now - signal.timestamp) > 30:
                signal.status = "ORPHANED"
                self._alert("high", "Trading Bot Core Engine", "signal_orphaned", 
                           f"Señal {signal.ticker} {signal.signal} no procesada en 30s", 
                           "Orden no generada", 
                           "Verificar logs de contract_selector")

    # ==========================================
    # Public Methods for Instrumentation
    # ==========================================
    def record_signal(self, ticker: str, signal: str, trace_id: Optional[str] = None) -> str:
        """Registra recepción de señal."""
        self.metrics["signals_received"] += 1
        
        if not trace_id:
            trace_id = f"GEN-{uuid.uuid4().hex[:8]}"
            
        self.signals[trace_id] = SignalEvent(
            trace_id=trace_id,
            ticker=ticker,
            signal=signal,
            timestamp=time.time(),
            status="RECEIVED"
        )
        
        self.component_status["TradingView"]["status"] = "ACTIVE"
        self.component_status["TradingView"]["details"] = {
            "last_signal": f"{ticker} {signal}",
            "last_signal_time": datetime.now(timezone.utc).isoformat()
        }
        return trace_id

    def mark_signal_status(self, trace_id: str, status: str, error: Optional[str] = None):
        """Actualiza el estado de una señal."""
        if trace_id in self.signals:
            self.signals[trace_id].status = status
            if error:
                self.signals[trace_id].error = error

    def record_order(self, order_id: Any, trace_id: Optional[str] = None):
        """Registra que una orden fue colocada exitosamente."""
        self.metrics["orders_placed"] += 1
        
        if trace_id and trace_id in self.signals:
            self.signals[trace_id].status = "ORDER_PLACED"
            self.signals[trace_id].order_id = order_id
            logger.info(f"Observability: Signal {trace_id} linked to Order {order_id}")

    def record_dashboard_access(self):
        """Called by dashboard to heartbeat."""
        self.last_dashboard_access = time.time()

    def _alert(self, severity: str, component: str, failure_type: str, message: str, impact: str, action: str):
        """Genera y registra una alerta con deduplicación."""
        # Deduplicación: no repetir misma alerta (component+failure) en 5 mins
        last_alert = next((a for a in reversed(self.alerts) 
                           if a.component == component and a.failure_type == failure_type), None)
        
        if last_alert:
            last_time = datetime.fromisoformat(last_alert.timestamp)
            if (datetime.now(timezone.utc) - last_time).total_seconds() < 300:
                return

        alert = AlertPayload(
            timestamp=datetime.now(timezone.utc).isoformat(),
            severity=severity,
            component=component,
            failure_type=failure_type,
            potential_impact=impact,
            recommended_action=action,
            details={"message": message} # Message inside details or separate? Prompt says payload has specific fields.
        )
        # Prompt payload: component, failure_type, timestamp, severity, potential_impact, recommended_action.
        # Message is likely part of failure_type or implied. I'll keep it in details or log it.
        
        self.alerts.append(alert)
        if len(self.alerts) > self.max_alerts:
            self.alerts.pop(0)
            
        log_method = logger.error if severity in ["critical", "high"] else logger.warning
        log_method(f"🚨 ALERT [{severity.upper()}] {component}: {message} | Action: {action}")
        
        # Integración con Telegram
        try:
            from TradingEngine.utils.telegram_notifier import notifier
            asyncio.create_task(notifier.notify_error(f"[{component}] {message}", severity.upper()))
        except Exception:
            pass

    def get_status_report(self) -> Dict[str, Any]:
        """Devuelve el reporte completo de estado."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system_status": "OPERATIONAL" if not any(a.severity == "critical" for a in self.alerts[-5:]) else "CRITICAL",
            "components": self.component_status,
            "metrics": self.metrics,
            "active_alerts": [asdict(a) for a in self.alerts if (datetime.now(timezone.utc) - datetime.fromisoformat(a.timestamp)).total_seconds() < 3600]
        }

# Instancia global
observability = ObservabilitySystem()
