"""
Módulo de selección de contratos y ejecución de órdenes.

Flujo simple:
1. Llega señal (CALL/PUT) para un ticker.
2. Va al broker en VIVO, obtiene la cadena de opciones.
3. Selecciona la expiración más cercana.
4. Filtra contratos dentro del rango de precio configurado.
5. Verifica spread < 10%.
6. Envía orden de compra inmediata (Marketable Limit).
7. Adjunta orden hija: TP (+10%) y SL (-20%).
"""

import json
import math
import time
import asyncio
import logging
import datetime as dt
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple, List

from .config import Settings, get_settings

logger = logging.getLogger("ibg.contract_selector")
logger.propagate = True
if not logger.handlers:
    logger.setLevel(logging.INFO)

# ============================================================
#   INTERFAZ DEL BROKER (para que ibkr_adapter herede)
# ============================================================
class BrokerInterface:
    """Interfaz mínima que el adapter debe implementar."""
    async def get_account_balance(self) -> float: raise NotImplementedError
    async def get_option_chain(self, ticker: str) -> List[dict]: raise NotImplementedError
    async def get_option_chain_direct(self, ticker: str) -> List[dict]: raise NotImplementedError
    async def get_option_quote(self, option_symbol: str) -> Dict[str, float]: raise NotImplementedError
    async def place_bracket_order_complete(self, *, option_symbol, entry_price, tp_price, sl_price, qty, **kw): raise NotImplementedError
    async def get_open_trades(self) -> List[dict]: raise NotImplementedError
    async def get_daily_executions(self) -> List[dict]: raise NotImplementedError


# ============================================================
#   CONSTANTES
# ============================================================
OPTION_CONTRACT_MULTIPLIER = 100
DEFAULT_MAX_SPREAD_PCT = 0.10  # 10%

# Tickers de índice cuyas opciones usan tick mínimo especial en CBOE:
#   < $3.00 → $0.05 de tick,  >= $3.00 → $0.10 de tick
INDEX_OPTION_TICKERS = {"SPX", "SPXW", "NDX", "NDXW", "VIX", "RUT"}

def _round_to_tick(price: float, ticker: str) -> float:
    """Redondea al tick mínimo de precio según el contrato."""
    if ticker.upper() in INDEX_OPTION_TICKERS:
        tick = 0.05 if price < 3.00 else 0.10
    else:
        tick = 0.01  # Opciones de equity estándar
    return round(round(price / tick) * tick, 2)

# ============================================================
#   CONFIGURACIÓN DE RANGOS DE PRECIOS (desde JSON)
# ============================================================
_TICKER_CONFIG_CACHE: Optional[dict] = None
_TICKER_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ticker_ranges.json"

# SPX Contract AutoLab config
_SPX_CONFIG_CACHE: Optional[dict] = None
_SPX_CONFIG_PATH = Path(__file__).parent.parent / "config" / "spx_selector_config.json"


def _load_ticker_config() -> dict:
    global _TICKER_CONFIG_CACHE
    if _TICKER_CONFIG_CACHE is not None:
        return _TICKER_CONFIG_CACHE
    try:
        if _TICKER_CONFIG_PATH.exists():
            with open(_TICKER_CONFIG_PATH, 'r', encoding='utf-8') as f:
                _TICKER_CONFIG_CACHE = json.load(f)
                logger.info(f"✅ Loaded ticker config from {_TICKER_CONFIG_PATH}")
                return _TICKER_CONFIG_CACHE
    except Exception as e:
        logger.error(f"❌ Error loading ticker config: {e}")
    _TICKER_CONFIG_CACHE = {"tickers": {}, "etfs": []}
    return _TICKER_CONFIG_CACHE


def get_ticker_price_range(ticker: str) -> Optional[Tuple[float, float]]:
    config = _load_ticker_config()
    data = config.get("tickers", {}).get(ticker.upper())
    if data and data.get("active", True):
        return (data["min"], data["max"])
    return None


def is_etf(ticker: str) -> bool:
    config = _load_ticker_config()
    return ticker.upper() in config.get("etfs", [])


def reload_ticker_config():
    global _TICKER_CONFIG_CACHE
    _TICKER_CONFIG_CACHE = None
    logger.info("🔄 Ticker config cache cleared.")


def _load_spx_config() -> Optional[dict]:
    """Carga la configuración evolutiva del selector SPX."""
    global _SPX_CONFIG_CACHE
    if _SPX_CONFIG_CACHE is not None:
        return _SPX_CONFIG_CACHE
    try:
        if _SPX_CONFIG_PATH.exists():
            with open(_SPX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                _SPX_CONFIG_CACHE = json.load(f)
                logger.info(f"✅ SPX selector config loaded (v{_SPX_CONFIG_CACHE.get('version', '?')})")
                return _SPX_CONFIG_CACHE
    except Exception as e:
        logger.error(f"❌ Error loading SPX config: {e}")
    return None


def reload_spx_config():
    """Fuerza recarga de la configuración SPX (llamado por el AutoLab tras promoción)."""
    global _SPX_CONFIG_CACHE
    _SPX_CONFIG_CACHE = None
    logger.info("🔄 SPX selector config cache cleared.")


# Legacy compatibility
def _get_legacy_ticker_ranges() -> Dict[str, Tuple[float, float]]:
    config = _load_ticker_config()
    return {
        t: (d["min"], d["max"])
        for t, d in config.get("tickers", {}).items()
        if d.get("active", True)
    }

TICKER_PRICE_RANGES = _get_legacy_ticker_ranges()
ETFS = _load_ticker_config().get("etfs", [])


# ============================================================
#   ESTADO DEL DÍA (mínimo necesario)
# ============================================================
Side = Literal["long", "short"]

_used_capital: float = 0.0
_realized_pnl: float = 0.0
_capital_lock = asyncio.Lock()

# Estas funciones son necesarias para compatibilidad con webhook.py y otros módulos
def get_day_plan():
    """Retorna un objeto truthy para indicar que el sistema está activo."""
    return True  # Siempre activo, ya no hay "plan del día" complejo

def get_day_state():
    """Retorna estado mínimo del día."""
    class _State:
        pass
    s = _State()
    s.used_capital = _used_capital
    s.realized_pnl = _realized_pnl
    return s


# ============================================================
#   SELECCIÓN DE CONTRATO (Simple y Directa)
# ============================================================
def _score_spx_candidate(candidate: dict, champion: dict) -> float:
    """
    Calcula el score de un candidato SPX usando los parámetros del Champion.
    Score más alto = mejor contrato para valorización 0DTE.
    
    Factores:
    - Distancia al sweet_spot: contratos más cercanos al precio óptimo aprendido puntúan más.
    - Spread: menor spread = mejor ejecución = más profit neto.
    - Moneyness relativo: posición dentro del rango (normalizada 0-1).
    """
    mid = candidate["price"]
    spread = candidate.get("spread_pct", 0.0)
    
    sweet_spot = champion["sweet_spot"]
    price_min = champion["price_min"]
    price_max = champion["price_max"]
    
    # Factor 1: Distancia al sweet spot (1.0 = en el sweet spot, 0.0 = en el extremo del rango)
    range_width = price_max - price_min
    if range_width > 0:
        distance = abs(mid - sweet_spot) / range_width
        score_price = max(0.0, 1.0 - distance)
    else:
        score_price = 1.0
    
    # Factor 2: Spread (1.0 = spread cero, 0.0 = spread en el máximo permitido)
    max_spread = champion["max_spread_pct"]
    if max_spread > 0:
        score_spread = max(0.0, 1.0 - (spread / max_spread))
    else:
        score_spread = 1.0
    
    # Factor 3: Moneyness relativo (preferencia por la zona media-alta del rango)
    # Normalizado: 0.0 = price_min, 1.0 = price_max
    if range_width > 0:
        moneyness_norm = (mid - price_min) / range_width
        # Curva gaussiana centrada en 0.6 (ligeramente por encima del medio)
        score_moneyness = math.exp(-((moneyness_norm - 0.6) ** 2) / 0.18)
    else:
        score_moneyness = 0.5
    
    # Score final ponderado
    w_price = champion["w_price"]
    w_spread = champion["w_spread"]
    w_moneyness = champion["w_moneyness"]
    total_weight = w_price + w_spread + w_moneyness
    
    final_score = (
        (w_price * score_price + w_spread * score_spread + w_moneyness * score_moneyness)
        / total_weight
    )
    
    return round(final_score, 4)


def _choose_best_option(
    chain: List[dict],
    *,
    ticker: str,
    option_type: Literal["CALL", "PUT"],
) -> Optional[Tuple[str, float]]:
    """
    Selecciona el mejor contrato de la cadena de opciones.
    
    Para SPX: usa el scoring system evolutivo (SPX Contract AutoLab).
    Para otros tickers: usa el criterio original (más caro dentro del rango).
    
    Criterios base (todos los tickers):
    1. Tipo correcto (CALL o PUT).
    2. Precio (mid) dentro del rango configurado.
    3. Spread Bid-Ask dentro del umbral.
    
    Criterio de selección final:
    - SPX: score ponderado (sweet_spot + spread + moneyness) con parámetros evolutivos.
    - Otros: el más caro (mayor prima = más cercano ATM).
    """
    price_range = get_ticker_price_range(ticker)
    if not price_range:
        logger.warning(f"[{ticker}] No hay rango de precio configurado. Abortando.")
        return None

    # Para SPX, cargar configuración evolutiva
    spx_config = None
    use_scoring = False
    if ticker.upper() == "SPX":
        spx_config = _load_spx_config()
        if spx_config and "champion" in spx_config:
            use_scoring = True
            champion = spx_config["champion"]
            # Usar los rangos del champion (pueden diferir del ticker_ranges.json)
            min_p = champion["price_min"]
            max_p = champion["price_max"]
            spread_threshold = champion["max_spread_pct"]
            logger.info(
                f"[SPX] 🧬 Scoring evolutivo activo | "
                f"Rango: ${min_p:.2f}-${max_p:.2f} | "
                f"Sweet spot: ${champion['sweet_spot']:.2f} | "
                f"Pesos: price={champion['w_price']:.2f}, spread={champion['w_spread']:.2f}, moneyness={champion['w_moneyness']:.2f}"
            )
        else:
            min_p, max_p = price_range
            spread_threshold = DEFAULT_MAX_SPREAD_PCT
    else:
        min_p, max_p = price_range
        spread_threshold = DEFAULT_MAX_SPREAD_PCT

    logger.info(f"[{ticker}] Rango de precio objetivo: ${min_p:.2f} - ${max_p:.2f}")

    candidates = []
    rejected_reasons = {"wrong_type": 0, "no_price": 0, "out_of_range": 0, "high_spread": 0}

    for opt in chain:
        # 1. Filtro de tipo
        if opt.get("type") != option_type:
            rejected_reasons["wrong_type"] += 1
            continue

        # 2. Obtener precio
        bid = float(opt.get("bid", 0) or 0)
        ask = float(opt.get("ask", 0) or 0)
        last = float(opt.get("last", 0) or 0)

        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            spread = (ask - bid) / mid
        elif last > 0:
            mid = last
            spread = 0.0
        else:
            rejected_reasons["no_price"] += 1
            continue

        # 3. Filtro de rango de precio
        if not (min_p <= mid <= max_p):
            rejected_reasons["out_of_range"] += 1
            continue

        # 4. Filtro de spread
        if spread > spread_threshold:
            rejected_reasons["high_spread"] += 1
            logger.debug(f"[{ticker}] {opt['symbol']} descartado: spread {spread:.1%}")
            continue

        candidates.append({
            "symbol": opt["symbol"],
            "price": mid,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread,
            "strike": opt.get("strike"),
            "expiry": opt.get("expiry"),
        })

    if not candidates:
        logger.warning(
            f"[{ticker}] No se encontró contrato en rango ${min_p:.2f}-${max_p:.2f}. "
            f"Rechazados: {rejected_reasons}"
        )
        return None

    # --- Selección final ---
    if use_scoring and ticker.upper() == "SPX":
        # SPX: scoring evolutivo
        for c in candidates:
            c["score"] = _score_spx_candidate(c, champion)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        selected = candidates[0]
        
        # Log detallado del top 3 para observabilidad
        top_n = min(3, len(candidates))
        logger.info(f"[SPX] 🏆 Top {top_n} candidatos por score:")
        for i, c in enumerate(candidates[:top_n]):
            logger.info(
                f"  #{i+1}: {c['symbol']} @ ${c['price']:.2f} | "
                f"Score={c['score']:.4f} | Strike={c['strike']} | Spread={c['spread_pct']:.1%}"
            )
    else:
        # Otros tickers: criterio original (más caro = más cercano ATM)
        candidates.sort(key=lambda x: x["price"], reverse=True)
        selected = candidates[0]

    logger.info(
        f"[{ticker}] ✅ Seleccionado: {selected['symbol']} @ ${selected['price']:.2f} "
        f"(Bid=${selected['bid']:.2f}, Ask=${selected['ask']:.2f}, Strike={selected['strike']}) "
        f"[{len(candidates)} candidatos válidos]"
    )
    return selected


# ============================================================
#   FLUJO PRINCIPAL: SEÑAL → CONTRATO → ORDEN
# ============================================================
async def on_tradingview_alert(
    *,
    ticker: str,
    direction: Literal["long", "short"],
    broker,
    trace_id: Optional[str] = None,
    # Parámetros ignorados (compatibilidad)
    use_trailing_stop: bool = False,
    trailing_percent: float = 10.0,
    tp_percent: Optional[float] = None,
    sl_percent: Optional[float] = None,
    execution_timeout_seconds: int = 30,
) -> str:
    """
    Flujo completo al recibir una señal:
    
    1. Fetch FRESCO de la cadena de opciones (directo al broker, SIN caché).
    2. Seleccionar contrato dentro del rango y con buen spread.
    3. Enviar orden de compra inmediata (Marketable Limit).
    4. Adjuntar TP (+10%) y SL (-20%) como órdenes hijas.
    """
    global _used_capital
    settings = get_settings()
    option_type = "CALL" if direction == "long" else "PUT"

    logger.info(f"[{ticker}] 🚀 Señal {option_type} recibida. TraceID: {trace_id}")

    # --- Registrar en observabilidad ---
    # if trace_id:
    #     try:
    #         from .observability import observability
    #         observability.record_signal(ticker, direction.upper(), trace_id=trace_id)
    #     except Exception:
    #         pass

    # --- PASO 1+2: Seleccionar contrato (Heat Map instantáneo o cadena tradicional) ---
    best_option_data = None
    heat_map_hit = False

    # Si es SPX y el Heat Map está listo → lookup instantáneo (0ms)
    if ticker.upper() == "SPX":
        try:
            from .market_data_stream import spx_stream
            if spx_stream.is_ready():
                hm_entry = spx_stream.get_best_contract(direction)
                if hm_entry and hm_entry.mid > 0:
                    # Convertir HeatMapEntry al formato esperado por el flujo
                    best_option_data = {
                        "symbol": f"SPX  {hm_entry.expiry} {hm_entry.right} {hm_entry.strike:.0f}",
                        "price": hm_entry.mid,
                        "bid": hm_entry.bid,
                        "ask": hm_entry.ask,
                        "spread_pct": hm_entry.spread_pct,
                        "strike": hm_entry.strike,
                        "expiry": hm_entry.expiry,
                        "con_id": hm_entry.con_id,
                        "delta": hm_entry.delta,
                        "score": hm_entry.score,
                    }
                    heat_map_hit = True
                    logger.info(
                        f"[{ticker}] 🌡️ HeatMap HIT (0ms) → Strike={hm_entry.strike} "
                        f"Mid=${hm_entry.mid:.2f} Score={hm_entry.score:.4f} Δ={hm_entry.delta:.3f}"
                    )
        except ImportError:
            pass  # Heat map no disponible, seguir con fallback
        except Exception as e:
            logger.warning(f"[{ticker}] HeatMap lookup error: {e}. Fallback a cadena.")

    # Fallback: obtener cadena de opciones del broker (500ms)
    if not best_option_data:
        if heat_map_hit is False and ticker.upper() == "SPX":
            logger.info(f"[{ticker}] 📡 HeatMap no disponible. Fallback a cadena del broker...")
        else:
            logger.info(f"[{ticker}] 📡 Obteniendo cadena de opciones FRESCA del broker...")

        chain = await broker.get_option_chain_direct(ticker)

        if not chain:
            logger.error(f"[{ticker}] ❌ No se pudo obtener la cadena de opciones.")
            return "NO_CONTRACT_FOUND"

        logger.info(f"[{ticker}] Cadena obtenida: {len(chain)} contratos disponibles.")
        best_option_data = _choose_best_option(chain, ticker=ticker, option_type=option_type)

    if not best_option_data:
        logger.warning(f"[{ticker}] ❌ Ningún contrato cumple los criterios.")
        return "NO_CONTRACT_FOUND"

    option_symbol = best_option_data["symbol"]
    estimated_price = best_option_data["price"]
    selected_strike = best_option_data["strike"]
    selected_expiry = best_option_data.get("expiry") # Viene de la cadena si se incluyó

    # --- PASO 3: Obtener precio REAL en vivo para la orden ---
    logger.info(f"[{ticker}] 💰 Obteniendo cotización en vivo de {option_symbol}...")
    entry_price, bid, ask = await _get_live_price(broker, option_symbol)

    if entry_price <= 0:
        logger.error(f"[{ticker}] ❌ No se pudo obtener precio válido para {option_symbol}.")
        return "PRICE_FETCH_ERROR"

    # --- PASO 4: Calcular cantidad ---
    if settings.use_virtual_balance:
        balance = settings.virtual_balance
    else:
        balance = await broker.get_account_balance()

    capital_per_trade = balance * settings.capital_fraction * settings.per_trade_fraction
    qty = int(capital_per_trade / (entry_price * OPTION_CONTRACT_MULTIPLIER))

    if qty < 1:
        logger.warning(f"[{ticker}] Capital insuficiente. Precio=${entry_price:.2f}, Capital=${capital_per_trade:.2f}")
        return "INSUFFICIENT_CAPITAL"

    # --- PASO 5: Calcular precio límite y TP/SL ---
    # Marketable Limit: Ask + buffer para garantizar ejecución inmediata
    # El buffer y redondeo respetan el tick mínimo del contrato (SPX: $0.10 para ≥$3)
    raw_ask = ask if ask > 0 else entry_price
    raw_limit = raw_ask + 0.05  # buffer mínimo
    limit_price = _round_to_tick(raw_limit, ticker)
    # Asegurar que limit_price >= ask para ser marketable
    if limit_price < raw_ask:
        limit_price = _round_to_tick(raw_ask + 0.10, ticker)

    # TP y SL se calculan sobre el limit_price de entrada que enviaremos realmente
    final_tp = tp_percent if tp_percent is not None else settings.tp_percent
    final_sl = sl_percent if sl_percent is not None else settings.sl_percent

    tp_price = _round_to_tick(limit_price * (1 + final_tp), ticker)  # +TP%
    sl_price = _round_to_tick(limit_price * (1 - final_sl), ticker)  # -SL%

    logger.info(
        f"[{ticker}] 📋 Orden: {option_symbol} x{qty} | "
        f"Limit=${limit_price:.2f} (Ask=${ask:.2f}) | "
        f"TP=${tp_price:.2f} (+{final_tp:.0%}) | "
        f"SL=${sl_price:.2f} (-{final_sl:.0%})"
    )

    # --- PASO 6: Enviar orden bracket (Compra + TP + SL) ---
    actual_use_trailing = use_trailing_stop if use_trailing_stop else getattr(settings, 'use_conditional_trailing', False)
    actual_trailing_pct = trailing_percent if use_trailing_stop else getattr(settings, 'trailing_percent', 5.0)

    try:
        order_id = await broker.place_bracket_order_complete(
            option_symbol=option_symbol,
            entry_price=limit_price,
            tp_price=tp_price,
            sl_price=sl_price,
            qty=qty,
            use_trailing_stop=actual_use_trailing,
            trailing_percent=actual_trailing_pct
        )

        if order_id:
            logger.info(f"[{ticker}] ✅ ORDEN EJECUTADA. Order ID: {order_id}")

            # Registrar en observabilidad
            # try:
            #     from .observability import observability
            #     observability.record_order(order_id, trace_id=trace_id)
            # except Exception:
            #     pass

            # Registrar en historial
            try:
                from .history import history_manager
                history_manager.add_executions([{
                    "execId": f"TV_{int(time.time())}",
                    "traceId": trace_id,
                    "time": dt.datetime.now().strftime("%Y%m%d  %H:%M:%S"),
                    "symbol": ticker,
                    "side": "BUY",
                    "shares": float(qty),
                    "price": float(entry_price),
                    "bid": float(bid),
                    "ask": float(ask),
                    "contract": {
                        "symbol": ticker,
                        "localSymbol": option_symbol,
                        "strike": selected_strike,
                        "right": option_type,
                        "expiry": selected_expiry or dt.date.today().strftime("%Y%m%d"), # Fallback a hoy si no hay
                        "lastTradeDateOrContractMonth": selected_expiry or dt.date.today().strftime("%Y%m%d")
                    },
                }])
            except Exception:
                pass

            # Actualizar capital usado
            async with _capital_lock:
                _used_capital += entry_price * qty * OPTION_CONTRACT_MULTIPLIER

            return "ORDER_PLACED"
        else:
            logger.error(f"[{ticker}] ❌ Orden falló (broker retornó None).")
            return "FAILED"

    except Exception as e:
        logger.error(f"[{ticker}] ❌ Error ejecutando orden: {e}", exc_info=True)
        return "ORDER_ERROR"


# ============================================================
#   UTILIDADES
# ============================================================
async def _get_live_price(broker, option_symbol: str, retries: int = 3) -> tuple:
    """Obtiene precio en vivo con reintentos. Retorna (price, bid, ask)."""
    for attempt in range(retries):
        try:
            quote = await broker.get_option_quote(option_symbol)
            bid = float(quote.get("bid", 0) or 0)
            ask = float(quote.get("ask", 0) or 0)
            last = float(quote.get("last", 0) or 0)

            # Limpiar NaN
            if math.isnan(bid): bid = 0
            if math.isnan(ask): ask = 0
            if math.isnan(last): last = 0

            # Prioridad: Ask > Last > Bid
            price = ask if ask > 0 else (last if last > 0 else bid)

            if price > 0:
                return price, bid, ask

            logger.warning(f"[{option_symbol}] Intento {attempt+1}/{retries}: sin precio válido")
        except Exception as e:
            logger.error(f"[{option_symbol}] Intento {attempt+1}/{retries}: error: {e}")

        if attempt < retries - 1:
            await asyncio.sleep(1)

    return 0.0, 0.0, 0.0


# --- Funciones de compatibilidad (usadas por otros módulos) ---
async def setup_day_plan(broker, tickers, settings):
    """Ya no necesitamos plan del día. Solo log."""
    logger.info(f"🔮 Sistema listo. Balance: ${settings.virtual_balance if settings.use_virtual_balance else 'REAL'}")

async def restore_day_state(broker):
    """Restaura estado del día desde ejecuciones del broker."""
    global _used_capital, _realized_pnl
    logger.info("🔄 Reconstruyendo estado del día...")
    fills = await broker.get_daily_executions()
    used = 0.0
    pnl = 0.0
    today_str = dt.date.today().strftime("%Y%m%d")
    for fill in fills:
        fill_time = fill.get("time", "").replace("-", "")
        if today_str in fill_time:
            if fill.get("side") == "BOT":
                used += fill.get("price", 0) * fill.get("shares", 0) * 100
            if fill.get("side") == "SLD":
                pnl += fill.get("realizedPNL", 0)
    _used_capital = used
    _realized_pnl = pnl
    logger.info(f"✅ Estado recuperado para {today_str}. Capital usado: ${used:.2f}, PnL: ${pnl:.2f}")
