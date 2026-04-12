import sqlite3
import logging
import asyncio
import datetime as dt
from typing import List, Dict, Any, Optional
import os
from .config import get_settings
from .ibkr_adapter import ibkr_broker

logger = logging.getLogger("ibg.data_logger")

DB_PATH = "data/market_analysis.db"

class DataLogger:
    def __init__(self):
        os.makedirs("data", exist_ok=True)
        self._init_db()
        self._running = False
        self._task = None

    def _init_db(self):
        abs_path = os.path.abspath(DB_PATH)
        logger.info(f"🗄️ Inicializando base de datos en: {abs_path}")
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Tabla de snapshots del mercado (subyacente)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ticker TEXT NOT NULL,
                    underlying_price REAL,
                    event_type TEXT
                )
            """)
            
            # Tabla de snapshots de opciones
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS option_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER,
                    option_symbol TEXT,
                    strike REAL,
                    right TEXT,
                    expiry TEXT,
                    bid REAL,
                    ask REAL,
                    mid REAL,
                    delta REAL,
                    gamma REAL,
                    theta REAL,
                    vega REAL,
                    iv REAL,
                    volume INTEGER,
                    open_interest INTEGER,
                    FOREIGN KEY (snapshot_id) REFERENCES market_snapshots (id)
                )
            """)

            # Tabla de investigación de apertura (9:30 AM)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS opening_research (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ticker TEXT NOT NULL,
                    day_of_week TEXT,
                    underlying_price REAL,
                    strike REAL,
                    strike_offset INTEGER, -- 0=ATM, 1=ATM+1, -1=ATM-1, etc.
                    option_type TEXT,
                    bid REAL,
                    ask REAL,
                    mid REAL,
                    spread_pct REAL,
                    iv REAL
                )
            """)
            
            conn.commit()
            conn.close()
            logger.info("✅ Base de datos inicializada correctamente.")
        except Exception as e:
            logger.error(f"❌ Error inicializando base de datos: {e}", exc_info=True)

    async def log_market_data(self, ticker: str, event_type: str = "PERIODIC"):
        """Captura y guarda un snapshot completo del mercado para un ticker."""
        try:
            # Pequeño delay para asegurar que las órdenes críticas tengan prioridad en el bridge
            if event_type == "ALERT":
                await asyncio.sleep(2)
                
            logger.info(f"📊 Capturando datos de mercado para {ticker} ({event_type})...")
            
            # 1. Obtener precio del subyacente
            from .ib_client import client
            ticker_info = await client.get_ticker_info(ticker)
            underlying_price = ticker_info.last
            if underlying_price <= 0: underlying_price = ticker_info.close
            
            # 2. Obtener cadena de opciones
            chain = await ibkr_broker.get_option_chain(ticker)
            if not chain:
                logger.warning(f"⚠️ No se pudo obtener la cadena de opciones para {ticker}")
                return

            # 3. Guardar snapshot del mercado
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute(
                "INSERT INTO market_snapshots (ticker, underlying_price, event_type) VALUES (?, ?, ?)",
                (ticker, underlying_price, event_type)
            )
            snapshot_id = cursor.lastrowid
            
            # 4. Guardar opciones (limitamos a las más relevantes para no saturar la DB)
            # Filtramos opciones cerca del dinero (ATM +/- 10%)
            relevant_options = [
                opt for opt in chain 
                if underlying_price * 0.9 <= opt['strike'] <= underlying_price * 1.1
            ]
            
            for opt in relevant_options:
                bid = opt.get('bid', 0.0)
                ask = opt.get('ask', 0.0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else opt.get('last', 0.0)
                
                cursor.execute("""
                    INSERT INTO option_snapshots (
                        snapshot_id, option_symbol, strike, right, expiry,
                        bid, ask, mid, delta, gamma, theta, vega, iv,
                        volume, open_interest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot_id, opt.get('symbol'), opt.get('strike'), opt.get('type'), opt.get('expiry'),
                    bid, ask, mid, opt.get('delta', 0.0), opt.get('gamma', 0.0), 
                    opt.get('theta', 0.0), opt.get('vega', 0.0), opt.get('iv', 0.0),
                    opt.get('volume', 0), opt.get('open_interest', 0)
                ))
            
            # --- LÓGICA ESPECIAL PARA INVESTIGACIÓN DE APERTURA (9:30 AM) ---
            if event_type == "OPENING_RESEARCH":
                await self._process_opening_research(ticker, underlying_price, chain, cursor)

            conn.commit()
            conn.close()
            logger.info(f"✅ Snapshot guardado para {ticker}. ID: {snapshot_id}, Opciones: {len(relevant_options)}")
            
        except Exception as e:
            logger.error(f"❌ Error en log_market_data para {ticker}: {e}", exc_info=True)

    async def _process_opening_research(self, ticker: str, spot: float, chain: List[Dict], cursor):
        """Procesa y guarda los contratos que caen dentro del rango óptimo para investigación."""
        try:
            from .contract_selector import TICKER_PRICE_RANGES
            
            # 1. Obtener el rango para este ticker
            # Si el ticker no está en el dict, usamos un rango genérico o saltamos
            ticker_range = TICKER_PRICE_RANGES.get(ticker)
            if not ticker_range:
                logger.warning(f"🧪 [RESEARCH] No hay rango definido para {ticker}. Saltando investigación.")
                return

            min_p = ticker_range.get('min', 0.0)
            max_p = ticker_range.get('max', 999.0)
            
            # 2. Filtrar contratos dentro del rango (usando Mid price)
            candidates = []
            for opt in chain:
                bid = opt.get('bid', 0.0)
                ask = opt.get('ask', 0.0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else opt.get('last', 0.0)
                
                if min_p <= mid <= max_p:
                    candidates.append((opt, mid))

            if not candidates:
                logger.warning(f"🧪 [RESEARCH] {ticker}: No se encontraron contratos en el rango ${min_p}-${max_p}")
                return

            day_name = dt.datetime.now().strftime('%A')

            # 3. Guardar los candidatos encontrados
            for opt, mid in candidates:
                # Calcular distancia al spot en strikes (aproximado)
                # Nota: El offset aquí es informativo de qué tan lejos está del spot
                dist_pct = (opt['strike'] - spot) / spot * 100
                bid = opt.get('bid', 0.0)
                ask = opt.get('ask', 0.0)
                spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 0
                
                cursor.execute("""
                    INSERT INTO opening_research (
                        ticker, day_of_week, underlying_price, strike, 
                        strike_offset, option_type, bid, ask, mid, spread_pct, iv
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ticker, day_name, spot, opt['strike'], 
                    round(dist_pct, 2), opt['type'], bid, ask, mid, spread_pct, opt.get('iv', 0.0)
                ))
            
            logger.info(f"🧪 [RESEARCH] {ticker}: Guardados {len(candidates)} contratos dentro del rango óptimo (${min_p}-${max_p})")
        except Exception as e:
            logger.error(f"Error procesando opening research para {ticker}: {e}")

    async def _loop(self):
        """Bucle periódico de recolección de datos."""
        settings = get_settings()
        active_tickers_str = getattr(settings, "active_tickers", "SPY,QQQ,TSLA,NVDA,AMD")
        tickers = [t.strip().upper() for t in active_tickers_str.split(",")]
        
        research_done_today = False

        while self._running:
            try:
                now = dt.datetime.now()
                
                # Reset research flag at midnight
                if now.hour == 0: research_done_today = False

                # 1. LÓGICA DE INVESTIGACIÓN (9:30 AM EXACTA)
                if now.hour == 9 and now.minute == 30 and not research_done_today:
                    logger.info("🔔 [RESEARCH] Iniciando captura de apertura (9:30 AM)...")
                    for ticker in tickers:
                        await self.log_market_data(ticker, "OPENING_RESEARCH")
                        await asyncio.sleep(1)
                    research_done_today = True

                # 2. LÓGICA PERIÓDICA (Cada 15 min durante mercado)
                if 9 <= now.hour <= 16:
                    # Solo si no acabamos de hacer la de las 9:30
                    if not (now.hour == 9 and now.minute == 30):
                        for ticker in tickers:
                            await self.log_market_data(ticker, "PERIODIC")
                            await asyncio.sleep(2)
                
                # Esperar 1 minuto para la próxima evaluación de tiempo
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ Error en el bucle de DataLogger: {e}")
                await asyncio.sleep(60)

    async def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("🚀 DataLogger iniciado.")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("🛑 DataLogger detenido.")

    def get_analysis_summary(self) -> Dict[str, Any]:
        """Obtiene un resumen de análisis para el dashboard."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 1. Spreads promedio por ticker
            cursor.execute("""
                SELECT 
                    m.ticker,
                    AVG((o.ask - o.bid) / o.mid * 100) as avg_spread_pct,
                    MAX((o.ask - o.bid) / o.mid * 100) as max_spread_pct,
                    COUNT(*) as sample_count
                FROM option_snapshots o
                JOIN market_snapshots m ON o.snapshot_id = m.id
                WHERE o.bid > 0 AND o.ask > 0 AND o.mid > 0
                GROUP BY m.ticker
            """)
            spreads = [dict(row) for row in cursor.fetchall()]
            
            # 2. Últimos snapshots
            cursor.execute("""
                SELECT ticker, underlying_price, timestamp, event_type
                FROM market_snapshots
                ORDER BY timestamp DESC
                LIMIT 10
            """)
            recent = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            return {
                "spreads": spreads,
                "recent_snapshots": recent
            }
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                logger.warning(f"⚠️ Tablas no encontradas en DB. Intentando re-inicializar...")
                self._init_db()
            return {"spreads": [], "recent_snapshots": []}
        except Exception as e:
            logger.error(f"Error obteniendo resumen de análisis: {e}")
            return {"spreads": [], "recent_snapshots": []}

data_logger = DataLogger()
