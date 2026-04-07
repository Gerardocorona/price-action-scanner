"""
signal_router.py — Puente PA Scanner → Bot IBKR (Bot Alfa)
===========================================================
Recibe señales del Price Action Scanner y las ejecuta en IBKR
mediante el servidor REST del Bot Alfa (localhost:8001).

Flujo completo (< 200ms de latencia end-to-end):
  1. PA Scanner detecta patrón + confluencia → PriceActionSignal
  2. signal_router.route_signal(signal) es llamado
  3. GET /api/status  → obtener balance de cuenta
  4. GET /api/scan    → obtener precio ask del contrato (CALL o PUT)
  5. Calcular qty = floor(cash * RISK_PCT / (ask * 100))
  6. POST /api/execute/{CALL|PUT}?qty={qty} → orden enviada a IBKR
  7. Log del resultado

Configuración:
  - BOT_BASE_URL: URL del servidor Bot Alfa (default: http://localhost:8001)
  - RISK_PCT: % del balance a arriesgar por trade (default: 0.20 = 20%)
  - MAX_CONTRACTS: Límite de contratos por trade (default: 20)
  - MIN_CONTRACTS: Mínimo de contratos para ejecutar (default: 1)

Uso desde pa_scanner.py:
    from .signal_router import SignalRouter
    router = SignalRouter()
    result = await router.route_signal(signal)

Uso standalone (test):
    python -m price_action_scanner.signal_router
"""

import asyncio
import logging
import math
import os
from datetime import datetime
from typing import Optional

import aiohttp

from .pa_signal_schema import PriceActionSignal

logger = logging.getLogger("ibg.price_action.signal_router")

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
BOT_BASE_URL = os.environ.get("BOT_BASE_URL", "http://localhost:8001")
RISK_PCT = float(os.environ.get("PA_RISK_PCT", "0.20"))       # 20% del balance
MAX_CONTRACTS = int(os.environ.get("PA_MAX_CONTRACTS", "20"))  # Límite por seguridad
MIN_CONTRACTS = int(os.environ.get("PA_MIN_CONTRACTS", "1"))   # Mínimo para operar
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)               # 10s timeout por request

# Credenciales del Bot Alfa (Basic Auth — igual que server.py)
BOT_USER = os.environ.get("BOT_USER", "admin")
BOT_PASS = os.environ.get("BOT_PASS", "Gerardo090928#*")


class SignalRouterError(Exception):
    """Error al enrutar señal hacia IBKR"""
    pass


class SignalRouter:
    """
    Puente entre el Price Action Scanner y el Bot Alfa (IBKR).

    Thread-safe: usa aiohttp session por instancia.
    Compatible con el event loop del pa_scanner.py.

    Ejemplo:
        router = SignalRouter()
        result = await router.route_signal(signal)
        # result = {'success': True, 'qty': 3, 'message': '...', 'contract': 'SPXW...'}
    """

    def __init__(
        self,
        base_url: str = BOT_BASE_URL,
        risk_pct: float = RISK_PCT,
        max_contracts: int = MAX_CONTRACTS,
        min_contracts: int = MIN_CONTRACTS,
        dry_run: bool = False,
    ):
        """
        Args:
            base_url: URL del servidor Bot Alfa (default: http://localhost:8001)
            risk_pct: Fracción del balance a arriesgar (0.20 = 20%)
            max_contracts: Límite máximo de contratos por trade
            min_contracts: Mínimo de contratos para ejecutar (si < min → no operar)
            dry_run: Si True, calcula qty pero NO envía orden (modo simulación)
        """
        self.base_url = base_url.rstrip("/")
        self.risk_pct = risk_pct
        self.max_contracts = max_contracts
        self.min_contracts = min_contracts
        self.dry_run = dry_run
        self._session: Optional[aiohttp.ClientSession] = None

        logger.info(
            f"[SignalRouter] Inicializado → {self.base_url} | "
            f"Risk={self.risk_pct*100:.0f}% | "
            f"Max={self.max_contracts}c | "
            f"{'DRY-RUN' if self.dry_run else 'LIVE'}"
        )

    # ── SESSION MANAGEMENT ────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        """Obtiene o crea session HTTP (lazy init)"""
        if self._session is None or self._session.closed:
            auth = aiohttp.BasicAuth(BOT_USER, BOT_PASS)
            self._session = aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT,
                auth=auth,
            )
        return self._session

    async def close(self):
        """Cierra la sesión HTTP limpiamente"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("[SignalRouter] HTTP session cerrada")

    # ── API CALLS ─────────────────────────────────────────────────────────────

    async def _get_account_status(self) -> dict:
        """
        GET /api/status → balance de cuenta y estado del bot.

        Returns:
            {'cash': 5000.00, 'positions': 0, 'status': 'ONLINE', ...}
        """
        session = await self._get_session()
        async with session.get(f"{self.base_url}/api/status") as resp:
            if resp.status != 200:
                raise SignalRouterError(f"Bot offline o error. HTTP {resp.status}")
            data = await resp.json()
            if data.get("status") != "ONLINE":
                raise SignalRouterError(
                    f"Bot IBKR no conectado. Estado: {data.get('status', 'UNKNOWN')}"
                )
            return data

    async def _get_best_contracts(self) -> dict:
        """
        GET /api/scan → mejor contrato CALL y PUT disponibles.

        Returns:
            {'CALL': {'ask': 4.50, 'description': 'SPXW...'}, 'PUT': {...}}
        """
        session = await self._get_session()
        async with session.get(f"{self.base_url}/api/scan") as resp:
            if resp.status != 200:
                raise SignalRouterError(f"Error escaneando contratos. HTTP {resp.status}")
            return await resp.json()

    async def _execute_trade(self, side: str, qty: int) -> dict:
        """
        POST /api/execute/{side}?qty={qty} → coloca orden en IBKR.

        Args:
            side: 'CALL' o 'PUT'
            qty: Número de contratos

        Returns:
            {'status': 'success', 'message': '...', 'contract': 'SPXW...'}
        """
        session = await self._get_session()
        url = f"{self.base_url}/api/execute/{side}"
        params = {"qty": qty}

        async with session.post(url, params=params) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise SignalRouterError(
                    f"Error ejecutando {side}. HTTP {resp.status}: {data.get('detail', 'Unknown error')}"
                )
            return data

    # ── POSITION SIZING ───────────────────────────────────────────────────────

    def _calculate_contracts(self, cash: float, ask_price: float) -> int:
        """
        Calcula número de contratos basado en plan de riesgo.

        Fórmula:
            qty = floor(cash * risk_pct / (ask_price * 100))

        Explicación:
            - cash * risk_pct = capital a arriesgar ($1,000 en cuenta de $5,000 con 20%)
            - ask_price * 100 = costo total de 1 contrato (opciones = precio × 100 acciones)
            - floor() = redondear hacia abajo para no exceder el riesgo

        Args:
            cash: Balance disponible en cuenta
            ask_price: Precio ask del contrato de opción

        Returns:
            Número de contratos (mínimo 0)
        """
        if ask_price <= 0:
            logger.warning("[SignalRouter] Ask price inválido (≤ 0)")
            return 0

        capital_to_risk = cash * self.risk_pct
        cost_per_contract = ask_price * 100  # Multiplicador de opciones

        qty = math.floor(capital_to_risk / cost_per_contract)

        # Aplicar límites
        qty = max(0, min(qty, self.max_contracts))

        logger.info(
            f"[SignalRouter] Sizing: Cash=${cash:,.2f} | "
            f"Risk={self.risk_pct*100:.0f}% (${capital_to_risk:,.2f}) | "
            f"Ask=${ask_price:.2f}/c (${cost_per_contract:.2f} total/c) | "
            f"Qty={qty}c"
        )

        return qty

    # ── MAIN ROUTING LOGIC ────────────────────────────────────────────────────

    async def route_signal(self, signal: PriceActionSignal) -> dict:
        """
        Punto de entrada principal. Recibe señal y ejecuta la orden.

        Args:
            signal: PriceActionSignal con order_data.direction = 'CALL' o 'PUT'

        Returns:
            dict con resultado:
            {
                'success': True/False,
                'signal_id': '...',
                'side': 'CALL'/'PUT',
                'qty': 3,
                'cash': 5000.00,
                'ask': 4.50,
                'capital_risked': 1350.00,
                'message': '...',
                'contract': 'SPXW...',
                'timestamp': '...',
                'dry_run': False,
            }
        """
        ts = datetime.now().isoformat()

        # Validaciones previas
        if not signal.order_generated:
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "message": "Señal no tiene orden generada (rechazada por confluencia)",
                "timestamp": ts,
            }

        if not signal.order_data:
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "message": "order_data es None — error interno del generador",
                "timestamp": ts,
            }

        side = signal.order_data.direction  # 'CALL' o 'PUT'

        if side not in ("CALL", "PUT"):
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "message": f"Dirección inválida: '{side}'. Debe ser CALL o PUT.",
                "timestamp": ts,
            }

        logger.info(
            f"[SignalRouter] ▶ Procesando señal {signal.signal_id} → {side} "
            f"@ {signal.current_price:.2f} | "
            f"Patrón: {signal.pattern_data.pattern_type}({signal.pattern_data.direction})"
        )

        try:
            # ── PASO 1: Verificar estado del bot ──────────────────────────────
            logger.debug("[SignalRouter] Consultando estado del bot...")
            status = await self._get_account_status()
            cash = float(status.get("cash", 0))

            if cash <= 0:
                raise SignalRouterError(f"Balance insuficiente: ${cash:.2f}")

            logger.info(f"[SignalRouter] Bot ONLINE. Balance: ${cash:,.2f}")

            # ── PASO 2: Escanear contrato para obtener precio ask ─────────────
            logger.debug(f"[SignalRouter] Escaneando mejor contrato {side}...")
            contracts = await self._get_best_contracts()
            best = contracts.get(side)

            if not best:
                raise SignalRouterError(
                    f"No hay contrato {side} disponible en el rango "
                    f"${status.get('min_price', 3.80)}-${status.get('max_price', 5.50)}"
                )

            ask = float(best.get("ask", 0))
            description = best.get("description", f"SPXW {side}")

            if ask <= 0:
                raise SignalRouterError(f"Precio ask inválido: ${ask}")

            # ── PASO 3: Calcular cantidad (position sizing) ───────────────────
            qty = self._calculate_contracts(cash, ask)

            if qty < self.min_contracts:
                raise SignalRouterError(
                    f"Contratos calculados ({qty}) < mínimo permitido ({self.min_contracts}). "
                    f"Balance ${cash:,.2f} insuficiente para {side} @ ${ask:.2f}/c"
                )

            capital_risked = qty * ask * 100

            # ── PASO 4: Ejecutar orden (o simular en dry_run) ─────────────────
            if self.dry_run:
                logger.warning(
                    f"[SignalRouter] 🟡 DRY-RUN — No se envió orden. "
                    f"Hubiera ejecutado: {side} {qty}c @ ${ask:.2f} "
                    f"(${capital_risked:,.2f} arriesgados)"
                )
                return {
                    "success": True,
                    "signal_id": signal.signal_id,
                    "side": side,
                    "qty": qty,
                    "cash": cash,
                    "ask": ask,
                    "capital_risked": capital_risked,
                    "message": f"DRY-RUN: {side} {qty}c @ ${ask:.2f}",
                    "contract": description,
                    "timestamp": ts,
                    "dry_run": True,
                }

            logger.info(
                f"[SignalRouter] 🚀 Enviando orden: {side} {qty}c @ ${ask:.2f} "
                f"→ ${capital_risked:,.2f} de ${cash:,.2f} ({self.risk_pct*100:.0f}%)"
            )

            result = await self._execute_trade(side, qty)

            logger.info(
                f"[SignalRouter] ✅ Orden ejecutada exitosamente: "
                f"{side} {qty}c | Contrato: {description} | "
                f"Capital arriesgado: ${capital_risked:,.2f}"
            )

            return {
                "success": True,
                "signal_id": signal.signal_id,
                "side": side,
                "qty": qty,
                "cash": cash,
                "ask": ask,
                "capital_risked": capital_risked,
                "message": result.get("message", "Orden enviada"),
                "contract": result.get("contract", description),
                "broker_response": result,
                "timestamp": ts,
                "dry_run": False,
            }

        except SignalRouterError as e:
            logger.error(f"[SignalRouter] ❌ Error de negocio: {e}")
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "side": side,
                "message": str(e),
                "timestamp": ts,
            }

        except aiohttp.ClientConnectorError:
            msg = (
                f"No se puede conectar con Bot Alfa en {self.base_url}. "
                "¿Está corriendo server.py?"
            )
            logger.error(f"[SignalRouter] ❌ {msg}")
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "side": side,
                "message": msg,
                "timestamp": ts,
            }

        except asyncio.TimeoutError:
            msg = f"Timeout al conectar con Bot Alfa ({REQUEST_TIMEOUT.total}s)"
            logger.error(f"[SignalRouter] ❌ {msg}")
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "side": side,
                "message": msg,
                "timestamp": ts,
            }

        except Exception as e:
            logger.error(f"[SignalRouter] ❌ Error inesperado: {e}", exc_info=True)
            return {
                "success": False,
                "signal_id": signal.signal_id,
                "side": getattr(signal.order_data, "direction", "UNKNOWN"),
                "message": f"Error inesperado: {str(e)}",
                "timestamp": ts,
            }

    # ── HEALTH CHECK ──────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """
        Verifica que el Bot Alfa está corriendo y conectado a IBKR.

        Returns:
            True si el bot está ONLINE y listo para recibir órdenes.
        """
        try:
            status = await self._get_account_status()
            is_online = status.get("status") == "ONLINE"
            cash = float(status.get("cash", 0))
            logger.info(
                f"[SignalRouter] Health Check: {'✅ ONLINE' if is_online else '❌ OFFLINE'} "
                f"| Balance: ${cash:,.2f}"
            )
            return is_online
        except Exception as e:
            logger.warning(f"[SignalRouter] Health Check fallido: {e}")
            return False


# ── INTEGRACIÓN CON PA_SCANNER ────────────────────────────────────────────────
# Para usar en pa_scanner.py, agregar en __init__:
#
#   from .signal_router import SignalRouter
#   self.router = SignalRouter(dry_run=False)
#
# Y en analyze(), después del generator.generate():
#
#   if signal and signal.order_generated:
#       router_result = await self.router.route_signal(signal)
#       if router_result['success']:
#           signal.order_data.broker_order_id = router_result.get('contract', 'pending')
#           signal.status = 'order_sent'
#       else:
#           logger.error(f"[PA-Scanner] Router falló: {router_result['message']}")


# ── ENTRY POINT STANDALONE (TEST) ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import date

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Para correr este test: python -m price_action_scanner.signal_router
    # Requiere que server.py esté corriendo en localhost:8001

    async def test_health():
        router = SignalRouter(dry_run=True)
        print("\n" + "="*60)
        print("  SIGNAL ROUTER — TEST DE CONECTIVIDAD")
        print("="*60)

        # Test 1: Health check
        print("\n[TEST 1] Health Check del Bot Alfa...")
        is_healthy = await router.health_check()
        print(f"  → Bot Alfa: {'✅ ONLINE' if is_healthy else '❌ OFFLINE (¿server.py corriendo?)'}")

        if not is_healthy:
            print("\n  ⚠️  Asegúrate de que el Bot Alfa esté corriendo:")
            print("     cd 'C:\\TV-BOT-TWS - Confirmar orden hija - Criterios contratos\\Bot Alfa\\AppTWS'")
            print("     python server.py")
            await router.close()
            return

        # Test 2: Simular señal CALL
        print("\n[TEST 2] Simulando señal CALL (dry_run=True)...")
        from price_action_scanner.pa_signal_schema import (
            PriceActionSignal, PatternData, TrendContext, ConfluenceData, OrderData
        )

        mock_signal = PriceActionSignal(
            signal_id="test-001",
            timestamp=datetime.now().isoformat(),
            session_date=date.today().isoformat(),
            pattern_data=PatternData(
                pattern_type="engulfing",
                direction="bullish",
                confidence=0.82,
                wick_ratio=0.15,
                body_ratio=0.70,
                volume_ratio=1.35,
                open=6583.0,
                high=6590.0,
                low=6580.0,
                close=6589.0,
                volume=1250,
            ),
            trend_context=TrendContext(
                trend_1h="bullish",
                trend_5m="bullish",
                trend_2m="bullish",
                is_lateral_market=False,
                lateral_range_points=0.0,
                price_vs_ma20="above",
                price_vs_ma200="above",
                break_and_retest_detected=True,
                break_direction="up",
            ),
            confluence_data=ConfluenceData(
                factors=["nivel_en_zona", "trend_1h_bullish", "break_and_retest"],
                factors_count=3,
                score=7.5,
                meets_minimum=True,
            ),
            current_price=6589.0,
            order_generated=True,
            order_data=OrderData(
                direction="CALL",
                contracts=2,
                entry_price=6589.0,
                stop_loss=6577.0,
                take_profit_1=6609.0,
                take_profit_2=6624.0,
            ),
            status="order_ready",
        )

        result = await router.route_signal(mock_signal)

        print(f"\n  Resultado:")
        print(f"  ├─ Success:  {result['success']}")
        print(f"  ├─ Side:     {result.get('side', 'N/A')}")
        print(f"  ├─ Qty:      {result.get('qty', 'N/A')} contratos")
        print(f"  ├─ Cash:     ${result.get('cash', 0):,.2f}")
        print(f"  ├─ Ask:      ${result.get('ask', 0):.2f}/c")
        print(f"  ├─ Risked:   ${result.get('capital_risked', 0):,.2f}")
        print(f"  ├─ Contract: {result.get('contract', 'N/A')}")
        print(f"  └─ Message:  {result.get('message', 'N/A')}")

        print("\n" + "="*60)
        print("  TEST COMPLETO ✅")
        print("="*60)

        await router.close()

    asyncio.run(test_health())
