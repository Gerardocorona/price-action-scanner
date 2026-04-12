"""
calibrator_db.py
────────────────
Gestión de la base de datos del Calibrador Post-Sesión.
Crea y mantiene las tablas necesarias para el Diario de Sesión y las etiquetas expertas.

Tablas:
  - session_signals : Registro de todas las señales del día (ejecutadas + rechazadas)
  - expert_labels   : Etiquetas del experto sobre cada señal
  - calibration_log : Historial de ajustes aplicados al sistema
"""

import sqlite3
import os
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("calibrator_db")

# Ruta de la base de datos del calibrador (separada para no interferir con trading_lab.db)
BASE_DIR = Path(__file__).parent.parent
CALIBRATOR_DB = BASE_DIR / "data" / "calibrator.db"
CALIBRATOR_DB.parent.mkdir(exist_ok=True)


def get_connection():
    """Retorna una conexión a la base de datos del calibrador."""
    conn = sqlite3.connect(str(CALIBRATOR_DB))
    conn.row_factory = sqlite3.Row  # Acceso por nombre de columna
    conn.execute("PRAGMA journal_mode=WAL")  # Mejor concurrencia
    return conn


def init_db():
    """Crea las tablas si no existen. Idempotente — seguro de llamar múltiples veces."""
    conn = get_connection()
    try:
        conn.executescript("""
            -- ─────────────────────────────────────────────────────────────
            -- Señales de sesión: todo lo que el bot vio ese día
            -- ─────────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS session_signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date    TEXT    NOT NULL,          -- '2026-03-31'
                signal_time     TEXT    NOT NULL,          -- '10:23:45'
                strategy_id     TEXT    NOT NULL,          -- 'bb_reversal_soft_v4'
                symbol          TEXT    NOT NULL,          -- 'SPX'
                direction       TEXT    NOT NULL,          -- 'CALL' / 'PUT'
                signal_type     TEXT    NOT NULL,          -- 'EXECUTED' / 'REJECTED' / 'MISSED'
                -- Contexto de mercado en el momento de la señal
                spx_price       REAL,                      -- Precio SPX al momento
                rsi             REAL,                      -- RSI en ese momento
                volume_ratio    REAL,                      -- Volumen vs promedio
                bb_position     REAL,                      -- Posición en Bollinger (0-1)
                vix_level       REAL,                      -- VIX al momento
                market_regime   TEXT,                      -- 'trending_up' / 'ranging' / etc.
                -- Datos del contrato (si se ejecutó)
                option_symbol   TEXT,                      -- 'SPX  260331C05800000'
                option_strike   REAL,
                option_expiry   TEXT,
                entry_price     REAL,                      -- Precio de entrada (por acción)
                -- Resultado (si se ejecutó y cerró)
                exit_price      REAL,
                pnl_pct         REAL,                      -- % ganancia/pérdida
                exit_reason     TEXT,                      -- 'TP' / 'SL' / 'MANUAL' / 'EOD'
                -- Razón de rechazo (si fue rechazada)
                reject_reason   TEXT,                      -- 'PRICE_OUT_OF_RANGE' / 'SPREAD_TOO_HIGH' / etc.
                -- Metadatos
                raw_json        TEXT,                      -- JSON completo para debug
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_session_signals_date
                ON session_signals(session_date);

            -- ─────────────────────────────────────────────────────────────
            -- Etiquetas del experto: juicio humano sobre cada señal
            -- ─────────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS expert_labels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id       INTEGER NOT NULL REFERENCES session_signals(id),
                session_date    TEXT    NOT NULL,
                -- Veredicto principal
                verdict         TEXT    NOT NULL,          -- 'CORRECT' / 'WRONG' / 'MISSED'
                -- 'CORRECT'  = el bot actuó bien (entró cuando debía / rechazó cuando debía)
                -- 'WRONG'    = el bot entró pero no debía haber entrado
                -- 'MISSED'   = el bot rechazó pero debía haber entrado
                -- Factores del contexto que influyeron en el juicio
                factor_macro    INTEGER DEFAULT 0,         -- 1 si el macro fue determinante
                factor_sector   INTEGER DEFAULT 0,         -- 1 si el sector influyó
                factor_timing   INTEGER DEFAULT 0,         -- 1 si la hora fue el problema
                factor_volume   INTEGER DEFAULT 0,         -- 1 si el volumen fue sospechoso
                factor_spread   INTEGER DEFAULT 0,         -- 1 si el spread era demasiado alto
                factor_other    TEXT,                      -- Descripción libre
                -- Nota del experto
                notes           TEXT,
                -- Metadatos
                labeled_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(signal_id)                          -- Una etiqueta por señal
            );

            CREATE INDEX IF NOT EXISTS idx_expert_labels_date
                ON expert_labels(session_date);

            -- ─────────────────────────────────────────────────────────────
            -- Log de calibraciones aplicadas
            -- ─────────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS calibration_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date    TEXT    NOT NULL,
                strategy_id     TEXT    NOT NULL,
                parameter_name  TEXT    NOT NULL,          -- 'rsi_threshold' / 'volume_ratio_min' / etc.
                old_value       REAL,
                new_value       REAL,
                reason          TEXT,                      -- Explicación del ajuste
                labels_analyzed INTEGER,                   -- Cuántas etiquetas se usaron
                applied_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logger.info(f"✅ Calibrator DB inicializada en: {CALIBRATOR_DB}")
    except Exception as e:
        logger.error(f"Error inicializando Calibrator DB: {e}")
        raise
    finally:
        conn.close()


def insert_session_signal(signal_data: dict) -> int:
    """
    Inserta una señal de sesión. Retorna el ID insertado.
    signal_data debe contener al menos: session_date, signal_time, strategy_id,
    symbol, direction, signal_type.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO session_signals (
                session_date, signal_time, strategy_id, symbol, direction, signal_type,
                spx_price, rsi, volume_ratio, bb_position, vix_level, market_regime,
                option_symbol, option_strike, option_expiry,
                entry_price, exit_price, pnl_pct, exit_reason,
                reject_reason, raw_json
            ) VALUES (
                :session_date, :signal_time, :strategy_id, :symbol, :direction, :signal_type,
                :spx_price, :rsi, :volume_ratio, :bb_position, :vix_level, :market_regime,
                :option_symbol, :option_strike, :option_expiry,
                :entry_price, :exit_price, :pnl_pct, :exit_reason,
                :reject_reason, :raw_json
            )
        """, {
            "session_date": signal_data.get("session_date"),
            "signal_time":  signal_data.get("signal_time"),
            "strategy_id":  signal_data.get("strategy_id", "unknown"),
            "symbol":       signal_data.get("symbol", "SPX"),
            "direction":    signal_data.get("direction", ""),
            "signal_type":  signal_data.get("signal_type", "EXECUTED"),
            "spx_price":    signal_data.get("spx_price"),
            "rsi":          signal_data.get("rsi"),
            "volume_ratio": signal_data.get("volume_ratio"),
            "bb_position":  signal_data.get("bb_position"),
            "vix_level":    signal_data.get("vix_level"),
            "market_regime":signal_data.get("market_regime"),
            "option_symbol":signal_data.get("option_symbol"),
            "option_strike":signal_data.get("option_strike"),
            "option_expiry":signal_data.get("option_expiry"),
            "entry_price":  signal_data.get("entry_price"),
            "exit_price":   signal_data.get("exit_price"),
            "pnl_pct":      signal_data.get("pnl_pct"),
            "exit_reason":  signal_data.get("exit_reason"),
            "reject_reason":signal_data.get("reject_reason"),
            "raw_json":     signal_data.get("raw_json"),
        })
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_session_diary(session_date: str) -> list:
    """
    Retorna todas las señales del día con sus etiquetas (si existen).
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                s.*,
                l.verdict,
                l.factor_macro, l.factor_sector, l.factor_timing,
                l.factor_volume, l.factor_spread, l.factor_other,
                l.notes as label_notes,
                l.labeled_at
            FROM session_signals s
            LEFT JOIN expert_labels l ON l.signal_id = s.id
            WHERE s.session_date = ?
            ORDER BY s.signal_time ASC
        """, (session_date,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_expert_label(signal_id: int, label_data: dict) -> bool:
    """
    Guarda o actualiza la etiqueta experta de una señal.
    Retorna True si fue exitoso.
    """
    conn = get_connection()
    try:
        # Obtener session_date de la señal
        row = conn.execute(
            "SELECT session_date FROM session_signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if not row:
            logger.warning(f"Signal ID {signal_id} no encontrada")
            return False

        conn.execute("""
            INSERT INTO expert_labels (
                signal_id, session_date, verdict,
                factor_macro, factor_sector, factor_timing,
                factor_volume, factor_spread, factor_other, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                verdict       = excluded.verdict,
                factor_macro  = excluded.factor_macro,
                factor_sector = excluded.factor_sector,
                factor_timing = excluded.factor_timing,
                factor_volume = excluded.factor_volume,
                factor_spread = excluded.factor_spread,
                factor_other  = excluded.factor_other,
                notes         = excluded.notes,
                labeled_at    = CURRENT_TIMESTAMP
        """, (
            signal_id,
            row["session_date"],
            label_data.get("verdict", "CORRECT"),
            int(label_data.get("factor_macro", 0)),
            int(label_data.get("factor_sector", 0)),
            int(label_data.get("factor_timing", 0)),
            int(label_data.get("factor_volume", 0)),
            int(label_data.get("factor_spread", 0)),
            label_data.get("factor_other", ""),
            label_data.get("notes", ""),
        ))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error guardando etiqueta: {e}")
        return False
    finally:
        conn.close()


def get_unlabeled_count(session_date: str) -> int:
    """Retorna cuántas señales del día aún no tienen etiqueta."""
    conn = get_connection()
    try:
        result = conn.execute("""
            SELECT COUNT(*) FROM session_signals s
            LEFT JOIN expert_labels l ON l.signal_id = s.id
            WHERE s.session_date = ? AND l.id IS NULL
        """, (session_date,)).fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def get_label_stats(days: int = 30) -> dict:
    """
    Retorna estadísticas de etiquetado de los últimos N días.
    Útil para el motor de calibración.
    """
    conn = get_connection()
    try:
        stats = conn.execute("""
            SELECT
                verdict,
                COUNT(*) as count,
                AVG(CASE WHEN s.pnl_pct IS NOT NULL THEN s.pnl_pct ELSE 0 END) as avg_pnl
            FROM expert_labels l
            JOIN session_signals s ON s.id = l.signal_id
            WHERE l.session_date >= date('now', ?)
            GROUP BY verdict
        """, (f"-{days} days",)).fetchall()

        factor_counts = conn.execute("""
            SELECT
                SUM(factor_macro)  as macro,
                SUM(factor_sector) as sector,
                SUM(factor_timing) as timing,
                SUM(factor_volume) as volume,
                SUM(factor_spread) as spread
            FROM expert_labels
            WHERE session_date >= date('now', ?)
        """, (f"-{days} days",)).fetchone()

        return {
            "verdicts": {row["verdict"]: {"count": row["count"], "avg_pnl": row["avg_pnl"]}
                         for row in stats},
            "factors": dict(factor_counts) if factor_counts else {},
        }
    finally:
        conn.close()


# Inicializar la DB al importar el módulo
init_db()
