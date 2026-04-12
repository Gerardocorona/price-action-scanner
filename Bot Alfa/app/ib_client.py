from __future__ import annotations
import asyncio
import logging
import math
import threading
import time

print(f"DEBUG: >>> LOADING ALFA_MIGRATION_CLEAN/app/ib_client.py from {__file__} <<<")
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Iterable, List, Optional, Sequence
from types import SimpleNamespace

import pandas as pd
from ib_insync import Contract, IB, Option, Order, Stock, Ticker, util

from .config import Settings, get_settings
from .models import OptionContractData, TickerInfo
from .ib_bridge import get_bridge
from .order_id_manager import id_manager

logger = logging.getLogger("ibg.bot")


@dataclass(slots=True)
class _BracketParams:
    entry_price: float
    take_profit_pct: float = 0.20  # 20% TP
    stop_loss_pct: float = 0.12  # 12% SL


class IBClient:
    """
    Cliente IBKR con ib_insync a través de un puente síncrono.
    """

    def __init__(self, settings: Settings | None = None, client_id_override: int | None = None) -> None:
        self._settings = settings or get_settings()
        self._client_id_override = client_id_override
        
        if client_id_override is not None:
            # REFACTOR V2: Crear bridge INDEPENDIENTE (no singleton) para el Core
            # Esto evita destruir el bridge del Dashboard cuando ambos corren
            from .ib_bridge import SyncIBBridge
            self._bridge = SyncIBBridge(
                self._settings.ib_host,
                self._settings.ib_port,
                client_id_override
            )
            self._bridge.start()
        else:
            # Modo normal (Dashboard): usar el singleton global
            self._bridge = get_bridge(self._settings)
        self._chain_cache = {}  # Cache para reqSecDefOptParams
        self._is_connected: bool = False
        self._connected_event = asyncio.Event()
        self._op_lock = asyncio.Lock()
    
    @property
    def ib(self) -> IB:
        """Excluye el acceso directo a la instancia de ib_insync (Uso delegado)."""
        return self._bridge.ib

    def is_connected(self) -> bool:
        return self._bridge.is_connected()

    async def get_account_balance(self) -> float:
        """Obtiene el balance de la cuenta usando el puente."""
        def _get_balance():
            try:
                tags = self._bridge.ib.accountValues()
                for tag in tags:
                    if tag.tag == "NetLiquidation" and tag.currency == "USD":
                        return float(tag.value)
            except Exception:
                pass
            return 0.0

        await self._ensure_connected()
        async def _async_wrapper():
            return _get_balance()
        result = await self._bridge.run_coroutine_async(_async_wrapper())
        return result if result is not None else 0.0

    async def get_stock_contract(self, ticker: str) -> Contract:
        """Obtiene un contrato de acción o índice calificado."""
        async def _get():
            from ib_insync import Stock, Index
            if ticker.upper() == "SPX":
                contract = Index(ticker, "CBOE", "USD")
            else:
                contract = Stock(ticker, "SMART", "USD")
            qualified = await asyncio.wait_for(self._bridge.ib.qualifyContractsAsync(contract), timeout=20)
            return qualified[0] if qualified else contract

        await self._ensure_connected()
        return await self._bridge.run_coroutine_async(_get())

    async def get_historical_candles(self, contract: Contract, duration: str, bar_size: str) -> pd.DataFrame:
        """Obtiene velas históricas usando el puente."""
        import pandas as pd
        async def _get_bars():
            bars = await asyncio.wait_for(self._bridge.ib.reqHistoricalDataAsync(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            ), timeout=30)
            return util.df(bars)

        await self._ensure_connected()
        df = await self._bridge.run_coroutine_async(_get_bars())
        try:
            if df is None or len(df) == 0:
                return pd.DataFrame()
            return df
        except Exception as e:
            logger.error(f"DEBUG: ib_client Error: {e}")
            return pd.DataFrame()

        await self._ensure_connected()
        # Usar run_coroutine_async para no bloquear el loop principal
        async def _async_wrapper():
            return _get_balance()
        return await self._bridge.run_coroutine_async(_async_wrapper())

    async def get_option_chain(self, ticker: str) -> List[dict]:
        """Obtiene la cadena de opciones optimizada (1 exp, 4 strikes) para reducir latencia."""
        start_time = time.perf_counter()
        async def _get_chain():
            from ib_insync import Stock, Index, Option, util
            import datetime as dt
            import bisect

            # 1. Obtener subyacente — SPX es un índice, requiere Index/CBOE, no Stock/SMART
            if ticker.upper() == "SPX":
                stock = Index(ticker, "CBOE", "USD")
            else:
                stock = Stock(ticker, "SMART", "USD")
            await self._bridge.ib.qualifyContractsAsync(stock)
        
            # 1. Verificar caché
            if ticker in self._chain_cache:
                logger.info(f"[CACHE] Usando cadena en caché para {ticker}")
                chains = self._chain_cache[ticker]
            else:
                # 2. Obtener parámetros de opciones (Lento, ~10-15s)
                logger.info(f"Fetching option chain parameters for {ticker}...")
                t0 = time.perf_counter()
                # stock is already qualified
                chains = await self._bridge.ib.reqSecDefOptParamsAsync(stock.symbol, '', stock.secType, stock.conId)
                t1 = time.perf_counter()
                logger.info(f"[TIMING] reqSecDefOptParams took {(t1-t0)*1000:.2f}ms")
                logger.info(f"[{ticker}] secType={stock.secType}, conId={stock.conId}")
                
                if chains:
                    self._chain_cache[ticker] = chains
                    logger.info(f"[CACHE] Cadena guardada para {ticker}")
            
            if not chains:
                return []
            
            # Log all chains for debugging
            for i, c in enumerate(chains):
                logger.info(f"[{ticker}] Chain {i}: Exchange={getattr(c, 'exchange', 'N/A')}, TradingClass={getattr(c, 'tradingClass', 'N/A')}, Multiplier={getattr(c, 'multiplier', 'N/A')}")

            # Helper to access attributes or dict keys
            def get_val(obj, key):
                val = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
                return val if val is not None else []

            # Filtrar cadenas: SMART, tradingClass == ticker, multiplier == "100"
            best_chain = None
            ticker_upper = ticker.upper()

            # Para índices 0DTE (SPX, NDX): preferir SPXW/NDXW que tienen expiraciones diarias
            DAILY_0DTE_WEEKLY_CLASS = {
                "SPX": "SPXW",
                "SPXW": "SPXW",
                "NDX": "NDXW",
                "NDXW": "NDXW",
            }
            weekly_class = DAILY_0DTE_WEEKLY_CLASS.get(ticker_upper)
            if weekly_class:
                # Primero intentar con la clase semanal (tiene expiraciones diarias)
                for c in chains:
                    if get_val(c, 'exchange') == "SMART" and get_val(c, 'tradingClass') == weekly_class:
                        best_chain = c
                        logger.info(f"[{ticker}] 0DTE: usando TradingClass={weekly_class} para expiraciones diarias")
                        break

            if not best_chain:
                for c in chains:
                    if get_val(c, 'exchange') == "SMART":
                        if get_val(c, 'tradingClass') == ticker_upper:
                            best_chain = c
                            break

            if not best_chain:
                # Fallback a la primera cadena SMART
                best_chain = next((c for c in chains if get_val(c, 'exchange') == "SMART"), chains[0])
                
            chain = best_chain
            target_trading_class = get_val(chain, 'tradingClass')
            target_multiplier = get_val(chain, 'multiplier')
            logger.info(f"[{ticker}] Selected chain: TradingClass={target_trading_class}, Multiplier={target_multiplier}")
            
            # 3. Filtrar expiraciones (SOLO LA PRIMERA VÁLIDA)
            today = dt.date.today()
            raw_exps = get_val(chain, 'expirations')
            logger.info(f"[{ticker}] Raw expirations type: {type(raw_exps)}, content: {raw_exps}")
            expirations = sorted(raw_exps)
            
            try:
                # Convertir a objetos date
                valid_dates = []
                for e in expirations:
                    d = dt.datetime.strptime(e, "%Y%m%d").date()
                    if (d - today).days >= 0:
                        valid_dates.append(d)
                
                if not valid_dates:
                    logger.warning(f"[{ticker}] No valid expirations found.")
                    return []

                # Lógica de Selección de Expiración
                ETFS = ["SPY", "QQQ", "IWM", "DIA", "TNA", "SOXL", "GLD", "SLV", "USO"]
                # Índices con expiración diaria (0DTE): preferir HOY si está disponible
                DAILY_0DTE = ["SPX", "SPXW", "NDX", "VIX"]
                is_etf = ticker.upper() in ETFS
                is_daily_0dte = ticker.upper() in DAILY_0DTE

                target_date = None

                if is_daily_0dte:
                    # Para SPX/NDX: SIEMPRE preferir expiración de HOY (0DTE)
                    today_dates = [d for d in valid_dates if d == today]
                    if today_dates:
                        target_date = today_dates[0]
                        logger.info(f"[{ticker}] 0DTE index. Expiración HOY: {target_date}")
                    else:
                        # Si no hay 0DTE hoy (ej: lunes/miércoles sin exp), usar la más cercana
                        target_date = valid_dates[0]
                        logger.info(f"[{ticker}] 0DTE index sin exp hoy. Fallback más cercana: {target_date}")
                elif is_etf:
                    # Para ETFs: La más cercana (incluso diaria)
                    target_date = valid_dates[0]
                    logger.info(f"[{ticker}] ETF detected. Selected nearest expiration: {target_date}")
                else:
                    # Para Stocks: Preferir Viernes (weekday 4)
                    # Buscar el primer viernes disponible
                    friday_dates = [d for d in valid_dates if d.weekday() == 4]
                    if friday_dates:
                        target_date = friday_dates[0]
                        logger.info(f"[{ticker}] Stock detected. Selected nearest Friday: {target_date}")
                    else:
                        # Fallback: Si no hay viernes, usar la más cercana
                        target_date = valid_dates[0]
                        logger.info(f"[{ticker}] Stock detected but no Friday found. Fallback to nearest: {target_date}")

                target_exp = target_date.strftime("%Y%m%d")
                logger.info(f"[{ticker}] Target expiration string: {target_exp}")

            except Exception as e:
                logger.error(f"[{ticker}] Error filtering expirations: {e}")
                return []
            
            # 4. Obtener precio actual
            curr_price = 0
            try:
                logger.info(f"[{ticker}] Requesting tickers for price (secType={stock.secType})...")
                tickers = await self._bridge.ib.reqTickersAsync(stock)
                logger.info(f"[{ticker}] Tickers received: {len(tickers) if tickers else 'None'}")

                if tickers:
                    # Check for NaN safely
                    last = tickers[0].last
                    close = tickers[0].close
                    curr_price = last if last is not None and not math.isnan(last) else close
                    if curr_price is None or math.isnan(curr_price):
                        curr_price = 0
                else:
                     logger.warning(f"[{ticker}] No tickers received")
            except Exception as e:
                logger.error(f"[{ticker}] Error fetching price: {e}")
                curr_price = 0

            # Fallback de precio para índices (SPX, NDX, etc.) via reqMktData snapshot
            if (curr_price <= 0 or math.isnan(curr_price)) and stock.secType == "IND":
                try:
                    logger.info(f"[{ticker}] Index price = 0, intentando reqMktData snapshot...")
                    self._bridge.ib.reqMktData(stock, '', True, False)
                    await asyncio.sleep(2)
                    snap = self._bridge.ib.ticker(stock)
                    if snap:
                        for val in [snap.last, snap.close, snap.bid, snap.ask]:
                            if val is not None and not math.isnan(val) and val > 0:
                                curr_price = val
                                logger.info(f"[{ticker}] Precio via snapshot: {curr_price}")
                                break
                    self._bridge.ib.cancelMktData(stock)
                except Exception as e:
                    logger.warning(f"[{ticker}] Snapshot fallback error: {e}")

            # Tercer fallback: para índices sin suscripción, usar ETF proxy × multiplicador
            # SPX → SPY × 10,  NDX → QQQ × 40
            if (math.isnan(curr_price) or curr_price <= 0) and stock.secType == "IND":
                PROXY_MAP = {
                    "SPX": ("SPY", 10.0),
                    "SPXW": ("SPY", 10.0),
                    "NDX": ("QQQ", 40.0),
                    "NDXW": ("QQQ", 40.0),
                }
                proxy_info = PROXY_MAP.get(ticker.upper())
                if proxy_info:
                    proxy_ticker, proxy_mult = proxy_info
                    try:
                        from ib_insync import Stock as IBStock
                        logger.info(f"[{ticker}] Intentando precio via proxy {proxy_ticker}×{proxy_mult}...")
                        proxy_stock = IBStock(proxy_ticker, "SMART", "USD")
                        proxy_tickers = await self._bridge.ib.reqTickersAsync(proxy_stock)
                        if proxy_tickers:
                            px = proxy_tickers[0].last
                            if px is None or math.isnan(px) or px <= 0:
                                px = proxy_tickers[0].close
                            if px is not None and not math.isnan(px) and px > 0:
                                curr_price = px * proxy_mult
                                logger.info(f"[{ticker}] Precio via {proxy_ticker}×{proxy_mult}: {curr_price:.2f}")
                    except Exception as e:
                        logger.warning(f"[{ticker}] Proxy price error: {e}")

            logger.info(f"[{ticker}] Current price: {curr_price}")

            if math.isnan(curr_price) or curr_price <= 0:
                # Fallback mejorado: usar strikes CENTRALES (no los primeros que son los más OTM bajos)
                all_strikes = sorted(get_val(chain, 'strikes'))
                mid = len(all_strikes) // 2
                start = max(0, mid - 20)
                end = min(len(all_strikes), start + 40)
                relevant_strikes = all_strikes[start:end]
                logger.warning(f"[{ticker}] No price — usando strikes centrales como fallback: {relevant_strikes[0]:.0f}-{relevant_strikes[-1]:.0f} ({len(relevant_strikes)} strikes)")
            else:
                # Filtrar strikes +/- 15% para encontrar opciones OTM baratas
                strikes = sorted(get_val(chain, 'strikes'))
                relevant_strikes = [s for s in strikes if curr_price * 0.85 <= s <= curr_price * 1.15]
                logger.info(f"[{ticker}] Total strikes: {len(strikes)}, Relevant (15%): {len(relevant_strikes)}")
                
                # REMOVED: Hard limit of 20 strikes. We rely on the 15% range to capture all potential candidates.
                # If too many, maybe limit to 50 to avoid timeouts, but 15% usually yields reasonable count.
                if len(relevant_strikes) > 50:
                     # Si aún son demasiados, recortar extremos pero mantener centro amplio
                     idx = bisect.bisect_left(relevant_strikes, curr_price)
                     start = max(0, idx - 25)
                     end = min(len(relevant_strikes), idx + 25)
                     relevant_strikes = relevant_strikes[start:end]
                     logger.info(f"[{ticker}] Limited to 50 strikes around money.")

            contracts = []
            for strike in relevant_strikes:
                for right in ["C", "P"]:
                    # IMPORTANTE: multiplier y tradingClass son vitales para evitar "Unknown contract"
                    contracts.append(Option(
                        ticker, target_exp, strike, right, "SMART", 
                        multiplier=str(target_multiplier) if target_multiplier else "100", 
                        currency="USD",
                        tradingClass=str(target_trading_class) if target_trading_class else None
                    ))
            
            if not contracts:
                logger.warning(f"[{ticker}] No contracts generated. Relevant strikes: {relevant_strikes}")
                return []
            
            logger.info(f"Qualifying {len(contracts)} contracts for {ticker}...")
            
            # 5. Calificar contratos (todos de una vez, son pocos)
            qualified = await self._bridge.ib.qualifyContractsAsync(*contracts)
            # logger.info(f"[{ticker}] Qualified contracts: {len(qualified)}")
            
            if not qualified:
                # logger.warning(f"[{ticker}] No contracts were qualified by IBKR")
                return []

            # 6. Obtener market data
            # try:
            #     logger.info(f"[{ticker}] Requesting market data for {len(qualified)} contracts...")
            # except:
            #     pass
            
            tickers = await self._bridge.ib.reqTickersAsync(*qualified)
            # try:
            #     logger.info(f"[{ticker}] Market data tickers received: {len(tickers)}")
            # except: pass
            
            # 7. Serializar resultados
            results = []
            for t in tickers:
                try:
                    exp_date = dt.datetime.strptime(t.contract.lastTradeDateOrContractMonth, "%Y%m%d").date()
                    results.append({
                        "symbol": t.contract.localSymbol,
                        "type": "CALL" if t.contract.right == "C" else "PUT",
                        "expiry": t.contract.lastTradeDateOrContractMonth,
                        "dte": (exp_date - today).days,
                        "strike": float(t.contract.strike),
                        "bid": float(t.bid) if not math.isnan(t.bid) else 0.0,
                        "ask": float(t.ask) if not math.isnan(t.ask) else 0.0,
                        "last": float(t.last) if not math.isnan(t.last) else 0.0
                    })
                except Exception as e:
                    logger.error(f"[{ticker}] Error serializing ticker {t.contract.localSymbol}: {e}")

            logger.info(f"[{ticker}] Final results count: {len(results)}")
            return results

        await self._ensure_connected()
        try:
            res = await self._bridge.run_coroutine_async(_get_chain())
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.info(f"[LATENCY] get_option_chain for {ticker} took {elapsed:.2f}ms")
            return res
        except Exception as e:
            logger.error(f"Error obteniendo cadena de opciones para {ticker}: {e}")
            return []

    async def get_fast_contract(self, ticker: str, direction: str, distance: int = 0) -> Optional[dict]:
        """
        Selección de contrato de Alta Velocidad (Fast Track).
        Evita descargar toda la cadena. Calcula el strike objetivo basado en el precio de la acción
        y solicita directamente ese contrato específico.
        
        Args:
            ticker: Símbolo (ej. SPY)
            direction: "CALL" o "PUT"
            distance: Distancia del ATM (0 = ATM, 1 = 1 strike OTM, etc.)
        """
        start_time = time.perf_counter()
        async def _fast_lookup():
            from ib_insync import Stock, Index, Option
            import datetime as dt
            import math

            # 1. Obtener precio del subyacente — SPX es un índice, requiere Index/CBOE
            if ticker.upper() == "SPX":
                stock = Index(ticker, "CBOE", "USD")
            else:
                stock = Stock(ticker, "SMART", "USD")
            qualified_stocks = await self._bridge.ib.qualifyContractsAsync(stock)
            if not qualified_stocks:
                logger.error(f"[{ticker}] FastTrack: No se pudo calificar el subyacente")
                return None
            stock = qualified_stocks[0]
            logger.info(f"[{ticker}] FastTrack: Subyacente calificado: {stock}")
            
            market_data = await self._bridge.ib.reqTickersAsync(stock)
            
            if not market_data:
                logger.error(f"[{ticker}] FastTrack: No market data for underlying")
                return None
                
            underlying_price = market_data[0].last if not math.isnan(market_data[0].last) else market_data[0].close
            if math.isnan(underlying_price) or underlying_price <= 0:
                # Intentar obtener el último precio de cierre si last es NaN
                underlying_price = market_data[0].close if not math.isnan(market_data[0].close) else 0.0
                
            if underlying_price <= 0:
                logger.error(f"[{ticker}] FastTrack: Invalid underlying price: {underlying_price}")
                return None
                
            logger.info(f"[{ticker}] FastTrack: Underlying Price = {underlying_price}")

            # 2. Obtener parámetros de la cadena (Strikes y Expiraciones reales)
            ticker_upper = ticker.upper()
            if ticker_upper not in self._chain_cache:
                logger.info(f"[{ticker}] FastTrack: Fetching chain parameters...")
                chains = await self._bridge.ib.reqSecDefOptParamsAsync(stock.symbol, '', stock.secType, stock.conId)
                if chains:
                    self._chain_cache[ticker_upper] = chains
            
            chains = self._chain_cache.get(ticker_upper)
            if not chains:
                logger.error(f"[{ticker}] FastTrack: No option chains found")
                return None
                
            # Seleccionar cadena SMART con el tradingClass correcto
            def get_val(obj, key):
                val = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
                return val
                
            # Filtrar cadenas: SMART, tradingClass == ticker, multiplier == "100"
            best_chain = None
            for c in chains:
                if get_val(c, 'exchange') == "SMART":
                    if get_val(c, 'tradingClass') == ticker_upper:
                        best_chain = c
                        break
            
            if not best_chain:
                # Fallback a la primera cadena SMART
                best_chain = next((c for c in chains if get_val(c, 'exchange') == "SMART"), chains[0])
                
            chain = best_chain
            all_strikes = sorted(get_val(chain, 'strikes') or [])
            all_exps = sorted(get_val(chain, 'expirations') or [])
            target_trading_class = get_val(chain, 'tradingClass')
            target_multiplier = str(get_val(chain, 'multiplier') or "100")
            
            if not all_strikes or not all_exps:
                logger.error(f"[{ticker}] FastTrack: Empty strikes or expirations")
                return None

            # 3. Calcular Expiración Objetivo
            today_str = dt.date.today().strftime("%Y%m%d")
            valid_exps = [e for e in all_exps if e >= today_str]
            
            if not valid_exps:
                logger.error(f"[{ticker}] FastTrack: No future expirations found")
                return None
                
            if ticker_upper in ["SPY", "QQQ", "IWM"]:
                target_date = valid_exps[0] # La más cercana
            else:
                # Viernes más cercano
                friday_exps = [e for e in valid_exps if dt.datetime.strptime(e, "%Y%m%d").weekday() == 4]
                target_date = friday_exps[0] if friday_exps else valid_exps[0]

            # 4. Calcular Strike Objetivo usando la lista real
            import bisect
            idx = bisect.bisect_left(all_strikes, underlying_price)
            
            # ATM Strike (el más cercano al precio actual)
            if idx == 0:
                atm_strike = all_strikes[0]
            elif idx == len(all_strikes):
                atm_strike = all_strikes[-1]
            else:
                # Comparar cuál está más cerca
                s1 = all_strikes[idx-1]
                s2 = all_strikes[idx]
                atm_strike = s1 if (underlying_price - s1) < (s2 - underlying_price) else s2

            dir_clean = direction.upper().strip()
            is_call = dir_clean in ["CALL", "LONG", "C"]
            is_put = dir_clean in ["PUT", "SHORT", "P"]
            right = "C" if is_call else "P"
            
            # Calcular target_strike basado en distance
            # CALL: OTM es Strike > Price (Subir en la lista)
            # PUT: OTM es Strike < Price (Bajar en la lista)
            try:
                atm_idx = all_strikes.index(atm_strike)
                if is_call:
                    target_idx = min(len(all_strikes) - 1, atm_idx + distance)
                elif is_put:
                    target_idx = max(0, atm_idx - distance)
                else:
                    logger.error(f"[{ticker}] FastTrack: Dirección inválida '{direction}'")
                    return None
                target_strike = all_strikes[target_idx]
            except Exception as e:
                logger.error(f"[{ticker}] FastTrack: Error indexing strikes: {e}")
                target_strike = atm_strike

            logger.info(f"[{ticker}] FastTrack: Target Strike = {target_strike} (ATM={atm_strike}, Dist={distance}, Dir={dir_clean})")
            logger.info(f"[{ticker}] FastTrack: Target Expiration = {target_date}")

            # 5. Construir y Calificar Contrato Específico
            contract = Option(
                ticker_upper, target_date, target_strike, right, "SMART", 
                multiplier=target_multiplier, 
                currency="USD",
                tradingClass=target_trading_class
            )
            
            # Intentar calificar
            qualified = await self._bridge.ib.qualifyContractsAsync(contract)
            
            if not qualified:
                logger.error(f"[{ticker}] FastTrack: No se pudo calificar contrato {contract}")
                return None

            if not qualified:
                logger.error(f"[{ticker}] FastTrack: No se pudo encontrar contrato válido tras varios fallos.")
                return None
                
            c = qualified[0]
            
            # 5. Retornar formato dict listo para usar
            return {
                "symbol": c.localSymbol,
                "conId": c.conId,
                "strike": c.strike,
                "expiry": c.lastTradeDateOrContractMonth,
                "right": c.right,
                "exchange": c.exchange,
                "currency": c.currency,
                "secType": "OPT",
                "multiplier": c.multiplier,
                "tradingClass": c.tradingClass
            }

        await self._ensure_connected()
        try:
            res = await self._bridge.run_coroutine_async(_fast_lookup())
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.info(f"[LATENCY] get_fast_contract for {ticker} took {elapsed:.2f}ms")
            return res
        except Exception as e:
            logger.error(f"Error en FastTrack para {ticker}: {e}")
            return None

    async def connect(self, client_id: int | None = None):
        """Verifica la conexión a través del puente, permitiendo override de ID."""
        if client_id is not None:
             # Forzar recreación del puente con nuevo ID
             self._bridge = get_bridge(self._settings, client_id_override=client_id)
        
        if self._bridge.is_connected():
            self._is_connected = True
            self._connected_event.set()
            logger.info(f"✅ Conectado a IBKR a través del puente síncrono (ID: {self._bridge.client_id}).")
        else:
            logger.warning("⏳ Esperando conexión del puente...")

    async def _ensure_connected(self):
        """Asegura que la conexión esté activa."""
        if not self.is_connected():
            await self.connect()
            if not self.is_connected():
                raise RuntimeError("No hay conexión con IBKR.")

    async def get_positions(self) -> List:
        await self._ensure_connected()
        return self._bridge.get_positions()

    async def get_open_trades(self) -> List:
        await self._ensure_connected()
        return self._bridge.get_open_trades()

    async def get_daily_executions(self) -> List:
        await self._ensure_connected()
        return self._bridge.get_executions()

    async def qualify_contract(self, option: OptionContractData):
        """Califica un contrato de opción usando el puente."""
        async def _qualify():
            from ib_insync import Option
            contract = Option(
                symbol=option.symbol,
                lastTradeDateOrContractMonth=option.lastTradeDateOrContractMonth,
                strike=option.strike,
                right=option.right,
                multiplier=option.multiplier,
                exchange="SMART",
                currency="USD"
            )
            qualified = await self._bridge.ib.qualifyContractsAsync(contract)
            if not qualified:
                return None
            res = qualified[0]
            return {
                "conId": res.conId,
                "symbol": res.symbol,
                "localSymbol": res.localSymbol,
                "exchange": "SMART",
                "lastTradeDateOrContractMonth": res.lastTradeDateOrContractMonth,
                "strike": res.strike,
                "right": res.right,
                "multiplier": res.multiplier
            }

        await self._ensure_connected()
        try:
            res_dict = await self._bridge.run_coroutine_async(_qualify())
            if not res_dict:
                raise RuntimeError(f"La API no devolvió un contrato calificado para {option.localSymbol}")
            return SimpleNamespace(**res_dict)
        except Exception as e:
            logger.error(f"Error calificando contrato: {e}")
            raise RuntimeError(f"No se pudo calificar el contrato: {e}")

    async def get_ticker_info(self, symbol: str) -> TickerInfo:
        """Obtiene información de precio para un ticker."""
        async def _get_ticker():
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            await self._bridge.ib.qualifyContractsAsync(contract)
            tickers = await self._bridge.ib.reqTickersAsync(contract)
            if not tickers:
                return None
            t = tickers[0]
            return {
                "symbol": symbol,
                "last": float(t.last) if not math.isnan(t.last) else float(t.close),
                "close": float(t.close) if not math.isnan(t.close) else 0.0,
                "bid": float(t.bid) if not math.isnan(t.bid) else 0.0,
                "ask": float(t.ask) if not math.isnan(t.ask) else 0.0
            }

        await self._ensure_connected()
        res = await self._bridge.run_coroutine_async(_get_ticker())
        if not res:
            raise RuntimeError(f"No se pudo obtener ticker para {symbol}")
        return TickerInfo(**res)

    async def place_bracket_order(self, symbol: str, action: str, quantity: int, limit_price: float, tp_pct: float, sl_pct: float):
        """Coloca una orden bracket usando el puente."""
        async def _place():
            from ib_insync import Stock, LimitOrder, StopOrder
            contract = Stock(symbol, "SMART", "USD")
            await self._bridge.ib.qualifyContractsAsync(contract)
            
            # Obtener ID base para vincular el bracket
            # Usamos el id_manager para persistencia
            base_id = id_manager.get_next_id(self._bridge.ib.client.getReqId())
            
            parent = LimitOrder(action, quantity, limit_price, account=self._settings.ib_account, transmit=False)
            parent.orderId = base_id
            
            exit_action = "SELL" if action == "BUY" else "BUY"
            tp_price = limit_price * (1 + tp_pct) if action == "BUY" else limit_price * (1 - tp_pct)
            sl_price = limit_price * (1 - sl_pct) if action == "BUY" else limit_price * (1 + sl_pct)
            
            tp = LimitOrder(exit_action, quantity, round(tp_price, 2), account=self._settings.ib_account, transmit=False, parentId=parent.orderId)
            tp.orderId = base_id + 1
            
            sl = StopOrder(exit_action, quantity, round(sl_price, 2), account=self._settings.ib_account, transmit=True, parentId=parent.orderId)
            sl.orderId = base_id + 2
            
            # placeOrder is non-blocking, but we are in async context, so it's fine.
            # We don't need to await it unless we want to wait for fills, which we don't here.
            for o in [parent, tp, sl]:
                self._bridge.ib.placeOrder(contract, o)
            return parent.orderId

        await self._ensure_connected()
        return await self._bridge.run_coroutine_async(_place())

    # ── helper de ventana de apertura ────────────────────────────────────────
    @staticmethod
    def _opening_window_delay_seconds() -> float:
        """
        Devuelve cuántos segundos faltan para que termine la ventana de apertura
        de mercado (09:30 - 09:33 EST).  Retorna 0.0 si ya estamos fuera.
        """
        EST = timezone(timedelta(hours=-5))
        now_est = datetime.now(EST)
        market_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
        window_end  = now_est.replace(hour=9, minute=33, second=0, microsecond=0)

        if market_open <= now_est < window_end:
            return (window_end - now_est).total_seconds()
        return 0.0

    async def place_bracket_order_complete(
        self,
        option_symbol: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        qty: int,
        use_trailing_stop: bool = False,
        trailing_percent: float = 10.0
    ) -> Optional[int]:
        """Coloca una orden bracket completa para opciones usando el puente con OCA Group.

        PROTECCIÓN APERTURA: Si la señal llega entre 09:30 y 09:33 EST, la orden
        de entrada (BUY) y la de Take Profit se envían de inmediato. El Stop Loss
        se programa para enviarse en cuanto finalice esa ventana, evitando que el
        IV Crush y la volatilidad inicial activen el stop prematuramente.
        """
        start_time = time.perf_counter()
        async def _place():
            from ib_insync import Option, LimitOrder, StopOrder, Order, PriceCondition
            import uuid

            # 1. Crear contrato de opción
            contract = Option(localSymbol=option_symbol, exchange="SMART", currency="USD")
            await self._bridge.ib.qualifyContractsAsync(contract)

            # 2. Obtener IDs válidos
            base_id = id_manager.get_next_id(self._bridge.ib.client.getReqId())

            # 3. Grupo OCA
            oca_group = f"OCA_{uuid.uuid4().hex[:8]}"

            # 4. Orden de entrada (LMT)
            parent = LimitOrder("BUY", qty, entry_price, account=self._settings.ib_account, transmit=False)
            parent.orderId = base_id

            # 5. Orden TP (Limit o Conditional Trailing)
            if use_trailing_stop:
                cond = PriceCondition(
                    price=tp_price,
                    conId=contract.conId,
                    exchange="SMART",
                    isMore=True
                )
                tp = Order(
                    action="SELL",
                    totalQuantity=qty,
                    orderType="TRAIL",
                    trailingPercent=trailing_percent,
                    account=self._settings.ib_account,
                    transmit=False,
                    parentId=parent.orderId,
                    conditions=[cond]
                )
            else:
                tp = LimitOrder("SELL", qty, tp_price, account=self._settings.ib_account, transmit=False, parentId=parent.orderId)
                
            tp.orderId = base_id + 1
            tp.ocaGroup = oca_group
            tp.ocaType = 1

            # 6. Construir la orden SL (Siempre una orden Stop estatíca inicial para soporte OCA)
            sl = StopOrder("SELL", qty, sl_price, account=self._settings.ib_account, transmit=False, parentId=parent.orderId)
            sl.orderId = base_id + 2
            sl.ocaGroup = oca_group
            sl.ocaType = 1

            # 7. Detectar si estamos en la ventana de apertura
            delay = IBClient._opening_window_delay_seconds()
            in_opening_window = delay > 0.0
            
            tp_desc = f"COND_TRAIL {trailing_percent}% @ {tp_price}" if use_trailing_stop else f"TP={tp_price}"
            sl_desc = f"SL={sl_price}"

            if in_opening_window:
                # Enviar entrada y TP ya. El SL se envía después de la ventana.
                # El último orden enviado debe tener transmit=True para que IBKR
                # procese el grupo. Forzamos transmit en el TP.
                tp.transmit = True
                self._bridge.ib.placeOrder(contract, parent)
                self._bridge.ib.placeOrder(contract, tp)
                
                logger.warning(
                    f"⏳ [APERTURA] Ventana 9:30-9:33 activa para {option_symbol}. "
                    f"Entrada + {tp_desc} enviados ya. "
                    f"Stop Loss ({sl_desc}) se enviará en {delay:.0f}s al cierre de ventana."
                )

                # Programar el envío del SL en background
                asyncio.ensure_future(
                    self._delayed_stop_loss(contract, sl, delay, option_symbol)
                )
            else:
                # Fuera de ventana: comportamiento normal, todo de golpe
                sl.transmit = True
                self._bridge.ib.placeOrder(contract, parent)
                self._bridge.ib.placeOrder(contract, tp)
                self._bridge.ib.placeOrder(contract, sl)

            logger.info(
                f"✅ Bracket Order (OCA: {oca_group}, ID: {parent.orderId}) enviada para "
                f"{option_symbol}: Qty={qty}, Entry={entry_price}, {tp_desc}, {sl_desc}"
                + (f" [SL RETRASADO {delay:.0f}s]" if in_opening_window else "")
            )
            return parent.orderId

        await self._ensure_connected()
        try:
            order_id = await self._bridge.run_coroutine_async(_place())
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.info(f"[LATENCY] place_bracket_order_complete for {option_symbol} took {elapsed:.2f}ms. OrderID: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Error colocando bracket order completa con OCA: {e}", exc_info=True)
            return None

    async def _delayed_stop_loss(
        self,
        contract,
        sl_order,
        delay_seconds: float,
        option_symbol: str
    ) -> None:
        """Espera `delay_seconds` y luego transmite la orden de Stop Loss al broker."""
        try:
            await asyncio.sleep(delay_seconds)
            # Verificar si aún existe la posición antes de enviar el SL
            sl_order.transmit = True
            async def _send_sl():
                self._bridge.ib.placeOrder(contract, sl_order)
            await self._bridge.run_coroutine_async(_send_sl())
            logger.info(
                f"✅ [APERTURA] Stop Loss enviado para {option_symbol} tras esperar "
                f"{delay_seconds:.0f}s (ventana 9:30-9:33 finalizada)."
            )
        except Exception as e:
            logger.error(f"❌ [APERTURA] Error al enviar Stop Loss retrasado para {option_symbol}: {e}", exc_info=True)

    async def prefetch_option_chains(self, tickers: List[str]):
        """
        Pre-carga la cadena de opciones para los tickers dados.
        Ejecutar esto al inicio para evitar latencia en la primera operación.
        OPTIMIZADO: Usa llamadas no bloqueantes para no congelar el servidor.
        """
        await self._ensure_connected() # Ensure connection before prefetching
        logger.info(f"🔥 Iniciando pre-calentamiento de opciones para: {tickers}")

        async def _fetch_chain(t_symbol):
            from ib_insync import Stock, Index
            # SPX es un índice, requiere Index/CBOE, no Stock/SMART
            if t_symbol.upper() == "SPX":
                stock = Index(t_symbol, 'CBOE', 'USD')
            else:
                stock = Stock(t_symbol, 'SMART', 'USD')
            await self._bridge.ib.qualifyContractsAsync(stock)
            return await self._bridge.ib.reqSecDefOptParamsAsync(stock.symbol, '', stock.secType, stock.conId)

        for ticker in tickers:
            try:
                logger.info(f"🔥 Pre-fetching {ticker}...")
                # Usamos run_coroutine_async para NO bloquear el loop principal
                chains = await self._bridge.run_coroutine_async(_fetch_chain(ticker))
                
                if chains:
                    self._chain_cache[ticker] = chains
                    logger.info(f"✅ {ticker} cached ({len(chains)} chains)")
                
                # Pequeña pausa para ceder control al loop y no saturar
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"❌ Error pre-fetching {ticker}: {e}")
        logger.info("🔥 Pre-calentamiento completado.")

    async def req_mkt_data(self, contract_dict: dict) -> TickerInfo:
        """Solicita datos de mercado para un contrato (dict) usando el puente."""
        async def _get_ticker():
            from ib_insync import Contract
            c = Contract(**contract_dict)
            tickers = await self._bridge.ib.reqTickersAsync(c)
            if not tickers: return None
            t = tickers[0]
            last_val = float(t.last) if not math.isnan(t.last) else 0.0
            close_val = float(t.close) if not math.isnan(t.close) else 0.0
            
            return {
                "symbol": c.symbol,
                "last": last_val if last_val > 0 else close_val,
                "close": close_val,
                "bid": float(t.bid) if not math.isnan(t.bid) else 0.0,
                "ask": float(t.ask) if not math.isnan(t.ask) else 0.0
            }

        await self._ensure_connected()
        res = await self._bridge.run_coroutine_async(_get_ticker())
        if not res:
            raise RuntimeError(f"No se pudo obtener datos para {contract_dict.get('symbol')}")
        return TickerInfo(**res)

    async def close_position(self, contract_dict: dict) -> bool:
        """Cierra una posición usando el puente."""
        async def _close():
            from ib_insync import Contract, MarketOrder
            c = Contract(**contract_dict)
            await self._bridge.ib.qualifyContractsAsync(c)
            positions = [p for p in self._bridge.ib.positions() if p.contract.conId == c.conId]
            if not positions:
                return False
            pos = positions[0]
            action = "SELL" if pos.position > 0 else "BUY"
            order = MarketOrder(action, abs(pos.position), account=self._settings.ib_account)
            self._bridge.ib.placeOrder(c, order)
            return True

        await self._ensure_connected()
        return await self._bridge.run_coroutine_async(_close())

    async def place_dca_order(self, contract_dict: dict, current_qty: float, current_avg_cost: float, new_capital: float, tp_percent: float, sl_percent: float) -> bool:
        """Coloca una orden DCA usando el puente."""
        async def _place_dca():
            from ib_insync import Contract, LimitOrder
            c = Contract(**contract_dict)
            await self._bridge.ib.qualifyContractsAsync(c)
            
            # Calcular precio de entrada (usar el último precio)
            tickers = await self._bridge.ib.reqTickersAsync(c)
            if not tickers: return False
            price = tickers[0].last if not math.isnan(tickers[0].last) else tickers[0].close
            if math.isnan(price) or price <= 0: return False
            
            # Calcular cantidad
            qty = int(new_capital / (price * 100))
            if qty <= 0: qty = 1
            
            # Orden principal
            order = LimitOrder("BUY", qty, price, account=self._settings.ib_account)
            self._bridge.ib.placeOrder(c, order)
            return True

        await self._ensure_connected()
        return await self._bridge.run_coroutine_async(_place_dca())

    async def get_portfolio(self) -> List[dict]:
        """Obtiene el portfolio usando el puente."""
        def _get_portfolio():
            items = self._bridge.ib.portfolio()
            return [
                {
                    "contract": {
                        "symbol": i.contract.symbol, 
                        "localSymbol": i.contract.localSymbol, 
                        "conId": i.contract.conId,
                        "strike": i.contract.strike,
                        "right": i.contract.right,
                        "lastTradeDateOrContractMonth": i.contract.lastTradeDateOrContractMonth
                    },
                    "position": float(i.position),
                    "marketPrice": float(i.marketPrice),
                    "marketValue": float(i.marketValue),
                    "averageCost": float(i.averageCost),
                    "unrealizedPNL": float(i.unrealizedPNL),
                    "realizedPNL": float(i.realizedPNL)
                } for i in items
            ]

        await self._ensure_connected()
        async def _async_wrapper():
            return _get_portfolio()
        return await self._bridge.run_coroutine_async(_async_wrapper())

    async def disconnect(self):
        """Desconecta el puente."""
        if self._bridge:
            self._bridge.disconnect()
        self._is_connected = False
        self._connected_event.clear()
        logger.info("🔌 Desconectado de IBKR.")

# Instancia global (lazy initialization)
# REFACTOR V2: No crear la instancia al importar el módulo.
# Esto evita que el Core cree una conexión IBKR innecesaria (client_id=444)
# cuando solo necesita importar la clase IBClient.
_client_instance = None

def get_client() -> 'IBClient':
    """Obtiene el singleton del IBClient. Se crea en la primera llamada."""
    global _client_instance
    if _client_instance is None:
        _client_instance = IBClient()
    return _client_instance

# Mantener compatibilidad: 'client' como propiedad lazy
# Los módulos que hacen 'from .ib_client import client' seguirán funcionando
# porque Python evalúa el nombre al momento del import, pero el bridge
# solo se crea cuando se accede por primera vez.
class _LazyClient:
    """Proxy que retrasa la creación del IBClient hasta el primer uso."""
    def __getattr__(self, name):
        return getattr(get_client(), name)
    def __repr__(self):
        return repr(get_client())

client = _LazyClient()
