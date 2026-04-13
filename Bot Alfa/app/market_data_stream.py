"""
SPX Market Data Stream — Mapa de calor de opciones SPX 0DTE en tiempo real.

Mantiene suscripciones streaming a ~100 contratos SPX (SPXW) con precios y Greeks
actualizados tick-a-tick. Cuando llega una señal, el mejor contrato ya está
pre-computado (lookup de 0ms vs 500ms del snapshot actual).

Arquitectura:
- Crea su propio SyncIBBridge con client_id=102 (separado del trading=101)
- Suscribe reqMktData() con streaming continuo para cada contrato
- Callback _on_ticker_update() actualiza el heat map en cada tick
- Re-centra strikes cuando SPX se mueve >5 pts
- Thread-safe con threading.RLock para acceso desde FastAPI

Gestión de suscripciones (~100 total):
- Tier 1: 10 strikes más cercanos × 2 (C+P) = 40
- Tier 2: 15 strikes siguientes × 2 (C+P) = 60
- Re-centrado dinámico cuando SPX se mueve > threshold
"""

import asyncio
import json
import logging
import math
import threading
import time
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ib_insync import IB, Option, Index, util, Ticker

from .market_data_models import HeatMapEntry
from .config import get_settings

logger = logging.getLogger("ibg.market_data_stream")


def _load_spx_champion() -> dict:
    """Carga los parámetros champion desde spx_selector_config.json."""
    config_path = Path("config/spx_selector_config.json")
    default = {
        "price_min": 2.5, "price_max": 7.5, "sweet_spot": 5.0,
        "max_spread_pct": 0.1, "w_price": 0.5, "w_spread": 0.3, "w_moneyness": 0.2,
    }
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return data.get("champion", default)
    except Exception as e:
        logger.warning(f"Error loading SPX champion config: {e}")
    return default


def _score_entry(entry: HeatMapEntry, champion: dict) -> float:
    """
    Calcula score de un HeatMapEntry usando los parámetros champion.
    Réplica exacta de contract_selector._score_spx_candidate().
    """
    mid = entry.mid
    if mid <= 0:
        return 0.0

    sweet_spot = champion["sweet_spot"]
    price_min = champion["price_min"]
    price_max = champion["price_max"]

    # Factor 1: Distancia al sweet spot
    range_width = price_max - price_min
    if range_width > 0:
        distance = abs(mid - sweet_spot) / range_width
        score_price = max(0.0, 1.0 - distance)
    else:
        score_price = 1.0

    # Factor 2: Spread
    max_spread = champion["max_spread_pct"]
    if max_spread > 0:
        score_spread = max(0.0, 1.0 - (entry.spread_pct / max_spread))
    else:
        score_spread = 1.0

    # Factor 3: Moneyness relativo
    if range_width > 0:
        moneyness_norm = (mid - price_min) / range_width
        score_moneyness = math.exp(-((moneyness_norm - 0.6) ** 2) / 0.18)
    else:
        score_moneyness = 0.5

    w_price = champion["w_price"]
    w_spread = champion["w_spread"]
    w_moneyness = champion["w_moneyness"]
    total_weight = w_price + w_spread + w_moneyness

    final_score = (
        (w_price * score_price + w_spread * score_spread + w_moneyness * score_moneyness)
        / total_weight
    )
    return round(final_score, 4)


class SPXMarketDataStream:
    """
    Streamer de datos en tiempo real para opciones SPX 0DTE.

    Lifecycle:
    - start() → conecta bridge, detecta expiración, suscribe contratos
    - stop()  → cancela suscripciones, desconecta

    Acceso thread-safe:
    - get_best_contract(direction) → HeatMapEntry más alto score
    - get_heat_map(direction) → lista rankeada
    - get_spx_price() → precio actual SPX
    - is_ready() → True cuando hay datos suficientes
    """

    def __init__(self):
        self._settings = get_settings()
        self._ib: Optional[IB] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()

        # Heat map: key = (strike, right) → HeatMapEntry
        self._heat_map: Dict[Tuple[float, str], HeatMapEntry] = {}
        self._lock = threading.RLock()

        # Estado
        self._spx_price: float = 0.0
        self._last_center_strike: float = 0.0
        self._target_expiry: str = ""
        self._champion: dict = {}
        self._subscribed_tickers: Dict[Tuple[float, str], Ticker] = {}
        self._spx_ticker: Optional[Ticker] = None
        self._start_time: float = 0.0
        self._last_recenter_time: Optional[str] = None

        # Config
        self._client_id = getattr(self._settings, "ib_stream_client_id", 102)
        self._enabled = getattr(self._settings, "spx_stream_enabled", True)
        self._strike_range_pct = getattr(self._settings, "spx_stream_strike_range_pct", 0.05)
        self._recenter_threshold = getattr(self._settings, "spx_stream_recenter_threshold", 5.0)
        self._max_subs = getattr(self._settings, "spx_stream_max_subscriptions", 100)

    # ── Public API (thread-safe) ────────────────────────────────────────────

    def is_ready(self) -> bool:
        """True cuando hay al menos 10 entries no-stale."""
        if not self._ready.is_set():
            return False
        with self._lock:
            fresh = sum(1 for e in self._heat_map.values() if not e.is_stale)
            return fresh >= 10

    def get_spx_price(self) -> float:
        return self._spx_price

    def get_best_contract(self, direction: str) -> Optional[HeatMapEntry]:
        """
        Retorna el HeatMapEntry con el score más alto para la dirección dada.
        direction: "long"/"CALL" → right="C", "short"/"PUT" → right="P"
        """
        right = "C" if direction.upper() in ("LONG", "CALL", "C") else "P"
        champion = self._champion or _load_spx_champion()

        with self._lock:
            candidates = []
            for (strike, r), entry in self._heat_map.items():
                if r != right or entry.is_stale or entry.mid <= 0:
                    continue
                # Filtro de rango de precio
                if not (champion["price_min"] <= entry.mid <= champion["price_max"]):
                    continue
                # Filtro de spread
                if entry.spread_pct > champion["max_spread_pct"]:
                    continue
                # Re-score con datos actuales
                entry.score = _score_entry(entry, champion)
                candidates.append(entry)

            if not candidates:
                return None

            candidates.sort(key=lambda e: e.score, reverse=True)
            best = candidates[0]
            logger.info(
                f"[HeatMap] 🏆 Best {right}: Strike={best.strike} "
                f"Mid=${best.mid:.2f} Score={best.score:.4f} "
                f"Δ={best.delta:.3f} Spread={best.spread_pct:.1%}"
            )
            return best

    def get_heat_map(self, direction: str = "CALL") -> List[HeatMapEntry]:
        """Retorna todos los entries para una dirección, ordenados por score."""
        right = "C" if direction.upper() in ("LONG", "CALL", "C") else "P"
        champion = self._champion or _load_spx_champion()

        with self._lock:
            entries = []
            for (strike, r), entry in self._heat_map.items():
                if r != right:
                    continue
                if not entry.is_stale and entry.mid > 0:
                    entry.score = _score_entry(entry, champion)
                entries.append(entry)

            entries.sort(key=lambda e: e.score, reverse=True)
            return entries

    def get_status(self) -> dict:
        """Retorna estado del streamer para health check."""
        with self._lock:
            total = len(self._subscribed_tickers)
            calls = sum(1 for (s, r) in self._subscribed_tickers if r == "C")
            puts = total - calls
            stale = sum(1 for e in self._heat_map.values() if e.is_stale)

        connected = self._ib.isConnected() if self._ib else False
        uptime = time.time() - self._start_time if self._start_time else 0.0

        return {
            "connected": connected,
            "ready": self.is_ready(),
            "client_id": self._client_id,
            "spx_price": self._spx_price,
            "total_subscriptions": total,
            "active_calls": calls,
            "active_puts": puts,
            "stale_entries": stale,
            "last_recenter": self._last_recenter_time,
            "uptime_seconds": round(uptime, 1),
        }

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self):
        """Inicia el streamer en un thread dedicado."""
        if not self._enabled:
            logger.info("[HeatMap] SPX Stream DESHABILITADO por config.")
            return

        logger.info(f"[HeatMap] Iniciando SPX Market Data Stream (client_id={self._client_id})...")
        self._champion = _load_spx_champion()
        self._start_time = time.time()
        self._stop_event.clear()

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="SPXStream")
        self._thread.start()

    def stop(self):
        """Detiene el streamer y desconecta."""
        logger.info("[HeatMap] Deteniendo SPX Market Data Stream...")
        self._stop_event.set()

        if self._loop and self._ib:
            try:
                asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop).result(timeout=10)
            except Exception as e:
                logger.error(f"[HeatMap] Error en cleanup: {e}")

        if self._ib and self._ib.isConnected():
            try:
                self._loop.call_soon_threadsafe(self._ib.disconnect)
            except Exception:
                pass

        logger.info("[HeatMap] Stream detenido.")

    # ── Internal: Thread + Event Loop ───────────────────────────────────────

    def _run_loop(self):
        """Entry point del thread dedicado."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        util.patchAsyncio()
        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            logger.error(f"[HeatMap] Loop crashed: {e}", exc_info=True)

    async def _main_loop(self):
        """Loop principal: conectar, suscribir, mantener."""
        self._ib = IB()
        backoff = 5

        while not self._stop_event.is_set():
            try:
                if not self._ib.isConnected():
                    logger.info(f"[HeatMap] Conectando a IBKR (client_id={self._client_id})...")
                    await self._ib.connectAsync(
                        self._settings.ib_host,
                        self._settings.ib_port,
                        self._client_id,
                    )
                    if self._ib.isConnected():
                        logger.info("[HeatMap] ✅ Conectado a IBKR.")
                        backoff = 5
                        await self._initialize_subscriptions()
                    else:
                        logger.warning("[HeatMap] Conexión falló.")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff + 5, 30)
                        continue
                else:
                    # Check de re-centrado
                    await self._check_recenter()
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"[HeatMap] Error en main loop: {e}", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff + 5, 30)

    async def _initialize_subscriptions(self):
        """Detecta expiración 0DTE, obtiene precio SPX, suscribe contratos."""
        # Solicitar delayed data si no hay suscripción real-time (Error 354/10090)
        # Type 3 = delayed, Type 4 = delayed-frozen (fuera de horario)
        self._ib.reqMarketDataType(3)

        # 1. Suscribir al underlying SPX para precio en tiempo real (o delayed)
        spx = Index("SPX", "CBOE")
        qualified = await self._ib.qualifyContractsAsync(spx)
        if not qualified:
            logger.error("[HeatMap] No se pudo calificar el contrato SPX.")
            return

        self._spx_ticker = self._ib.reqMktData(spx, "", snapshot=False, regulatorySnapshot=False)
        self._spx_ticker.updateEvent += self._on_underlying_update

        # Esperar a que llegue el primer precio
        for _ in range(50):  # 5 segundos max
            await asyncio.sleep(0.1)
            if self._spx_ticker.last and self._spx_ticker.last > 0:
                self._spx_price = self._spx_ticker.last
                break
            elif self._spx_ticker.close and self._spx_ticker.close > 0:
                self._spx_price = self._spx_ticker.close
                break

        if self._spx_price <= 0:
            logger.warning("[HeatMap] No se obtuvo precio SPX. Usando 5800 como fallback.")
            self._spx_price = 5800.0

        logger.info(f"[HeatMap] SPX Price: {self._spx_price}")

        # 2. Detectar expiración 0DTE
        self._target_expiry = self._detect_0dte_expiry()
        if not self._target_expiry:
            logger.error("[HeatMap] No se pudo detectar expiración 0DTE.")
            return

        logger.info(f"[HeatMap] Expiración 0DTE: {self._target_expiry}")

        # 3. Calcular strikes y suscribir
        await self._subscribe_strikes()
        self._ready.set()
        logger.info(f"[HeatMap] ✅ Stream LISTO. {len(self._subscribed_tickers)} suscripciones activas.")

    def _detect_0dte_expiry(self) -> str:
        """Detecta la expiración 0DTE de hoy para SPXW."""
        today = dt.date.today()
        # SPXW tiene expiración todos los días hábiles
        # Formato YYYYMMDD
        return today.strftime("%Y%m%d")

    async def _subscribe_strikes(self):
        """Suscribe a los strikes más cercanos al precio actual de SPX."""
        center = round(self._spx_price)
        self._last_center_strike = center

        # SPX strikes van de 5 en 5
        strike_step = 5

        # Tier 1: 10 strikes más cercanos (±50 pts) × C+P = 40 subs
        tier1_range = 10
        # Tier 2: 15 strikes siguientes (±75 pts más) × C+P = 60 subs
        tier2_range = 15

        all_strikes = set()

        # Tier 1
        for i in range(-tier1_range, tier1_range + 1):
            strike = center + (i * strike_step)
            # Redondear al múltiplo de 5 más cercano
            strike = round(strike / strike_step) * strike_step
            all_strikes.add(strike)

        # Tier 2 (strikes más lejanos)
        for i in range(tier1_range + 1, tier1_range + tier2_range + 1):
            for sign in [-1, 1]:
                strike = center + (sign * i * strike_step)
                strike = round(strike / strike_step) * strike_step
                all_strikes.add(strike)

        # Limitar al máximo de suscripciones (cada strike = 2 subs: C+P)
        sorted_strikes = sorted(all_strikes, key=lambda s: abs(s - center))
        max_strikes = self._max_subs // 2  # 50 strikes × 2 = 100 subs
        selected_strikes = sorted_strikes[:max_strikes]

        logger.info(
            f"[HeatMap] Suscribiendo {len(selected_strikes)} strikes × 2 (C+P) = "
            f"{len(selected_strikes) * 2} suscripciones | "
            f"Centro: {center} | Rango: {min(selected_strikes)}-{max(selected_strikes)}"
        )

        # Suscribir en batches para no saturar
        batch_size = 20
        all_contracts = []
        for strike in selected_strikes:
            for right in ["C", "P"]:
                all_contracts.append((strike, right))

        for i in range(0, len(all_contracts), batch_size):
            batch = all_contracts[i:i + batch_size]
            tasks = [self._subscribe_one(strike, right) for strike, right in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
            # Pausa entre batches para no saturar la API
            await asyncio.sleep(0.5)

    async def _subscribe_one(self, strike: float, right: str):
        """Suscribe a un contrato individual."""
        try:
            contract = Option(
                "SPX",
                self._target_expiry,
                strike,
                right,
                "SMART",
                tradingClass="SPXW",
            )
            qualified = await self._ib.qualifyContractsAsync(contract)
            if not qualified:
                return

            contract = qualified[0]

            # Crear entry en el heat map
            with self._lock:
                key = (strike, right)
                self._heat_map[key] = HeatMapEntry(
                    strike=strike,
                    right=right,
                    expiry=self._target_expiry,
                    con_id=contract.conId,
                )

            # Suscribir con Greeks (genericTickList: 100=Option Volume, 101=Open Interest,
            # 104=Historical Volatility, 106=Implied Volatility)
            ticker = self._ib.reqMktData(
                contract,
                genericTickList="100,101,104,106",
                snapshot=False,
                regulatorySnapshot=False,
            )
            ticker.updateEvent += lambda t, s=strike, r=right: self._on_ticker_update(t, s, r)

            with self._lock:
                self._subscribed_tickers[(strike, right)] = ticker

        except Exception as e:
            logger.debug(f"[HeatMap] Error suscribiendo {strike}{right}: {e}")

    # ── Callbacks (ejecutan en el thread IB) ────────────────────────────────

    def _on_ticker_update(self, ticker: Ticker, strike: float, right: str):
        """Callback en cada tick de un contrato de opciones."""
        with self._lock:
            key = (strike, right)
            entry = self._heat_map.get(key)
            if not entry:
                return

            # Actualizar precios
            if ticker.bid and ticker.bid > 0:
                entry.bid = float(ticker.bid)
            if ticker.ask and ticker.ask > 0:
                entry.ask = float(ticker.ask)
            if ticker.last and ticker.last > 0:
                entry.last = float(ticker.last)

            entry.update_spread()

            # Actualizar Greeks
            greeks = ticker.modelGreeks or ticker.lastGreeks
            if greeks:
                if greeks.delta is not None:
                    entry.delta = float(greeks.delta)
                if greeks.gamma is not None:
                    entry.gamma = float(greeks.gamma)
                if greeks.theta is not None:
                    entry.theta = float(greeks.theta)
                if greeks.vega is not None:
                    entry.vega = float(greeks.vega)
                if greeks.impliedVol is not None:
                    entry.iv = float(greeks.impliedVol)

            # Volumen
            if ticker.volume and ticker.volume > 0:
                entry.volume = int(ticker.volume)

            entry.last_update = time.time()
            entry.is_stale = False

    def _on_underlying_update(self, ticker: Ticker):
        """Callback en cada tick del SPX underlying."""
        if ticker.last and ticker.last > 0:
            self._spx_price = float(ticker.last)
        elif ticker.close and ticker.close > 0:
            self._spx_price = float(ticker.close)

    # ── Re-centrado dinámico ────────────────────────────────────────────────

    async def _check_recenter(self):
        """Re-centra suscripciones si SPX se movió más del threshold."""
        if self._spx_price <= 0 or self._last_center_strike <= 0:
            return

        distance = abs(self._spx_price - self._last_center_strike)
        if distance < self._recenter_threshold:
            return

        logger.info(
            f"[HeatMap] 🔄 Re-centrando: SPX={self._spx_price:.1f}, "
            f"Centro anterior={self._last_center_strike}, Distancia={distance:.1f} pts"
        )

        # Cancelar todas las suscripciones actuales
        await self._cleanup_subscriptions()

        # Re-suscribir con nuevo centro
        await self._subscribe_strikes()

        self._last_recenter_time = dt.datetime.now().isoformat()
        logger.info(f"[HeatMap] ✅ Re-centrado completado. Nuevo centro: {self._last_center_strike}")

    async def _cleanup_subscriptions(self):
        """Cancela todas las suscripciones de market data."""
        with self._lock:
            for (strike, right), ticker in self._subscribed_tickers.items():
                try:
                    self._ib.cancelMktData(ticker.contract)
                except Exception:
                    pass
            self._subscribed_tickers.clear()
            self._heat_map.clear()

    async def _cleanup(self):
        """Cleanup completo: cancela suscripciones + SPX underlying."""
        await self._cleanup_subscriptions()
        if self._spx_ticker:
            try:
                self._ib.cancelMktData(self._spx_ticker.contract)
            except Exception:
                pass


# ── Singleton global ────────────────────────────────────────────────────────
spx_stream = SPXMarketDataStream()
