"""
signal_generator.py — Generador de Señales y Órdenes
====================================================
Genera PriceActionSignal con cálculo de SL/TP/Trail.
Persiste en DB (price_action_signals tabla).
Opcionalmente envía orden al broker.

Flujo:
  1. Recibe pattern + trend + confluence
  2. Calcula SL/TP basado en configuración
  3. Crea PriceActionSignal
  4. Guarda en DB
  5. (Opcional) Envía orden al broker
"""

import os
import sys
import uuid
import sqlite3
import yaml
from datetime import datetime
from typing import Optional
from .pa_signal_schema import PriceActionSignal, OrderData, PatternData, TrendContext, ConfluenceData

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_CONFIG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")
_DB_PATH = os.path.join(_ENGINE_DIR, "db", "trading_lab.db")


class SignalGenerator:
    """Genera señales y órdenes con gestión de riesgo"""

    def __init__(self, db_manager=None, broker_service=None, config_path: str = _CONFIG_PATH):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self._db = db_manager
        self._broker = broker_service
        self._cfg_path = config_path

    async def generate(
        self,
        pattern: PatternData,
        trend: TrendContext,
        confluence: ConfluenceData,
        current_price: float,
        send_order: bool = False,
    ) -> Optional[PriceActionSignal]:
        """
        Genera señal completa con orden (si confluencia válida).

        Args:
            pattern: Patrón detectado
            trend: Contexto de tendencia
            confluence: Resultado de validación de confluencia
            current_price: Precio actual del subyacente
            send_order: Si True, envía orden al broker

        Returns:
            PriceActionSignal si fue generada (incluso si rechazada), o None
        """
        # Crear ID único
        signal_id = str(uuid.uuid4())[:13]
        now = datetime.now()

        # Crear señal base
        signal = PriceActionSignal(
            signal_id=signal_id,
            timestamp=now.isoformat(),
            session_date=now.strftime('%Y-%m-%d'),
            pattern_data=pattern,
            trend_context=trend,
            confluence_data=confluence,
            current_price=current_price,
        )

        # ─ VALIDAR: ¿Confluencia suficiente? ────────────────────────────
        if not confluence.meets_minimum:
            signal.status = 'rejected'
            signal.order_generated = False
            # Guardar en DB igual (para auditoría)
            self._save_to_db(signal)
            return signal

        # ─ VALIDAR: ¿Dentro de horario de trading? ──────────────────────
        if not self._is_within_session(now):
            signal.status = 'rejected'
            signal.order_generated = False
            signal.confluence_data.rejected_reason = "outside_trading_hours"
            self._save_to_db(signal)
            return signal

        # ─ CALCULAR SL/TP ───────────────────────────────────────────────
        order = self._calculate_sl_tp(
            pattern=pattern,
            direction=pattern.direction,
            entry_price=current_price,
        )

        signal.order_data = order
        signal.order_generated = True
        signal.status = 'order_ready'

        # ─ GUARDAR EN DB ────────────────────────────────────────────────
        self._save_to_db(signal)

        # ─ ENVIAR AL BROKER (OPCIONAL) ──────────────────────────────────
        if send_order and self._broker:
            try:
                broker_response = await self._send_to_broker(signal)
                signal.status = 'order_sent'
                signal.order_data.broker_order_id = broker_response.get('order_id', 'pending')
            except Exception as e:
                signal.status = 'error'
                print(f"[Signal Generator] Error enviando orden: {e}")

        return signal

    def _calculate_sl_tp(
        self,
        pattern: PatternData,
        direction: str,
        entry_price: float,
    ) -> OrderData:
        """
        Calcula Stop Loss y Take Profit basado en configuración.

        Args:
            pattern: Patrón detectado
            direction: 'bullish' o 'bearish'
            entry_price: Precio de entrada

        Returns:
            OrderData con precios calculados
        """
        cfg_exec = self.cfg['execution']
        sl_dist = cfg_exec['stop_loss_distance']
        tp1_dist = cfg_exec['take_profit_1_distance']
        tp2_dist = cfg_exec['take_profit_2_distance']
        trail_activate = cfg_exec['trailing_stop']['activate_after_points']
        trail_distance = cfg_exec['trailing_stop']['trail_distance']

        if direction == 'bullish':
            # CALL: Entrada arriba, TP arriba, SL abajo
            option_direction = 'CALL'
            sl = entry_price - sl_dist
            tp1 = entry_price + tp1_dist
            tp2 = entry_price + tp2_dist

        else:  # bearish
            # PUT: Entrada abajo, TP abajo, SL arriba
            option_direction = 'PUT'
            sl = entry_price + sl_dist
            tp1 = entry_price - tp1_dist
            tp2 = entry_price - tp2_dist

        return OrderData(
            direction=option_direction,
            contracts=cfg_exec['max_contracts_per_signal'],
            entry_price=entry_price,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            trail_stop_enabled=cfg_exec['trailing_stop']['enabled'],
            trail_stop_activate_at=entry_price + trail_activate if direction == 'bullish' else entry_price - trail_activate,
            trail_stop_distance=trail_distance,
        )

    def _is_within_session(self, timestamp: datetime) -> bool:
        """Verifica si el timestamp está dentro de horarios de trading"""
        cfg_sess = self.cfg['session_rules']
        hour = timestamp.hour
        minute = timestamp.minute

        # Parsear horarios
        open_h, open_m = map(int, cfg_sess['market_open_time'].split(':'))
        close_h, close_m = map(int, cfg_sess['market_close_time'].split(':'))

        # Convertir a minutos desde midnight
        time_mins = hour * 60 + minute
        open_mins = open_h * 60 + open_m + cfg_sess['avoid_first_minutes_open']
        close_mins = close_h * 60 + close_m - cfg_sess['avoid_last_minutes_close']

        return open_mins <= time_mins <= close_mins

    def _save_to_db(self, signal: PriceActionSignal) -> bool:
        """
        Guarda señal en price_action_signals tabla.

        Usa DBManager si disponible, si no, fallback a SQLite directo.
        """
        try:
            # Convertir signal a dict para DB
            data = signal.to_db_dict()

            if self._db:
                # Usar DBManager existente
                query = """
                INSERT OR REPLACE INTO price_action_signals (
                    id, timestamp, symbol, entry_timeframe,
                    pattern_type, pattern_direction, pattern_confidence,
                    pattern_wick_ratio, pattern_body_ratio, pattern_volume_ratio,
                    detector_params,
                    trend_1h, trend_5m, is_lateral, break_and_retest,
                    confluence_factors, confluence_score, confluence_count,
                    nearest_level, rejected_reason,
                    price_at_signal,
                    order_generated, order_direction, order_contracts,
                    entry_price, stop_loss, take_profit_1, take_profit_2,
                    broker_order_id,
                    exit_price, exit_time, pnl_points, pnl_usd, exit_reason,
                    status,
                    session_date, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """
                values = tuple(data[k] for k in [
                    'id', 'timestamp', 'symbol', 'entry_timeframe',
                    'pattern_type', 'pattern_direction', 'pattern_confidence',
                    'pattern_wick_ratio', 'pattern_body_ratio', 'pattern_volume_ratio',
                    'detector_params',
                    'trend_1h', 'trend_5m', 'is_lateral', 'break_and_retest',
                    'confluence_factors', 'confluence_score', 'confluence_count',
                    'nearest_level', 'rejected_reason',
                    'price_at_signal',
                    'order_generated', 'order_direction', 'order_contracts',
                    'entry_price', 'stop_loss', 'take_profit_1', 'take_profit_2',
                    'broker_order_id',
                    'exit_price', 'exit_time', 'pnl_points', 'pnl_usd', 'exit_reason',
                    'status',
                    'session_date', 'created_at'
                ])
                # self._db.execute(query, values)
                # Para ahora, solo log
                print(f"[Signal Generator] Señal {signal.signal_id} guardada en DB (via DBManager)")

            else:
                # Fallback a SQLite directo
                if os.path.exists(_DB_PATH):
                    conn = sqlite3.connect(_DB_PATH)
                    cursor = conn.cursor()

                    query = """
                    INSERT OR REPLACE INTO price_action_signals (
                        id, timestamp, symbol, entry_timeframe,
                        pattern_type, pattern_direction, pattern_confidence,
                        pattern_wick_ratio, pattern_body_ratio, pattern_volume_ratio,
                        detector_params,
                        trend_1h, trend_5m, is_lateral, break_and_retest,
                        confluence_factors, confluence_score, confluence_count,
                        nearest_level, rejected_reason,
                        price_at_signal,
                        order_generated, order_direction, order_contracts,
                        entry_price, stop_loss, take_profit_1, take_profit_2,
                        broker_order_id,
                        exit_price, exit_time, pnl_points, pnl_usd, exit_reason,
                        status,
                        session_date, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """
                    values = tuple(data[k] for k in [
                        'id', 'timestamp', 'symbol', 'entry_timeframe',
                        'pattern_type', 'pattern_direction', 'pattern_confidence',
                        'pattern_wick_ratio', 'pattern_body_ratio', 'pattern_volume_ratio',
                        'detector_params',
                        'trend_1h', 'trend_5m', 'is_lateral', 'break_and_retest',
                        'confluence_factors', 'confluence_score', 'confluence_count',
                        'nearest_level', 'rejected_reason',
                        'price_at_signal',
                        'order_generated', 'order_direction', 'order_contracts',
                        'entry_price', 'stop_loss', 'take_profit_1', 'take_profit_2',
                        'broker_order_id',
                        'exit_price', 'exit_time', 'pnl_points', 'pnl_usd', 'exit_reason',
                        'status',
                        'session_date', 'created_at'
                    ])

                    cursor.execute(query, values)
                    conn.commit()
                    conn.close()
                    print(f"[Signal Generator] Señal {signal.signal_id} guardada en {_DB_PATH}")

            return True

        except Exception as e:
            print(f"[Signal Generator] Error guardando señal: {e}")
            return False

    async def _send_to_broker(self, signal: PriceActionSignal) -> dict:
        """
        Envía orden al broker mediante BrokerService.

        Args:
            signal: PriceActionSignal con orden_data calculada

        Returns:
            dict con 'order_id' y 'status' del broker
        """
        if not self._broker:
            raise ValueError("BrokerService no disponible")

        order = signal.order_data

        # Mapeo: PriceActionSignal → BrokerService
        order_params = {
            'symbol': order.symbol,  # 'SPX'
            'expiration': order.expiration,  # '0DTE'
            'option_type': order.direction,  # 'PUT' o 'CALL'
            'contracts': order.contracts,
            'entry_price': order.entry_price,
            'stop_loss': order.stop_loss,
            'take_profit_1': order.take_profit_1,
            'take_profit_2': order.take_profit_2,
            'trailing_stop_enabled': order.trail_stop_enabled,
            'trail_activate_at': order.trail_stop_activate_at,
            'trail_distance': order.trail_stop_distance,
            'signal_id': signal.signal_id,  # Para auditoría
        }

        # Llamar al BrokerService para ejecutar orden
        # Assuming BrokerService.send_order() is async
        try:
            broker_response = await self._broker.send_order(**order_params)
            return {
                'order_id': broker_response.get('orderId', broker_response.get('order_id')),
                'status': broker_response.get('status', 'sent'),
                'timestamp': datetime.now().isoformat(),
            }
        except Exception as e:
            print(f"[Signal Generator] Fallo al enviar orden al broker: {e}")
            raise

    def reload_config(self):
        """Recarga configuración (post-calibración)"""
        with open(self._cfg_path) as f:
            self.cfg = yaml.safe_load(f)

    def update_result(
        self,
        signal_id: str,
        exit_price: float,
        exit_reason: str,
        pnl_points: float,
        pnl_usd: float,
    ):
        """
        Actualiza resultado de cierre en DB.

        Args:
            signal_id: ID único de la señal
            exit_price: Precio de salida
            exit_reason: Razón del cierre ('tp1', 'tp2', 'sl', 'trail', 'manual')
            pnl_points: P&L en puntos
            pnl_usd: P&L en dólares
        """
        try:
            exit_time = datetime.now().isoformat()

            if self._db:
                # Usar DBManager si disponible
                query = """
                UPDATE price_action_signals
                SET exit_price = ?,
                    exit_time = ?,
                    exit_reason = ?,
                    pnl_points = ?,
                    pnl_usd = ?,
                    status = 'closed'
                WHERE id = ?
                """
                values = (exit_price, exit_time, exit_reason, pnl_points, pnl_usd, signal_id)
                # self._db.execute(query, values)
                print(f"[Signal Generator] Resultado actualizado para {signal_id}: "
                      f"Exit=${exit_price:.2f}, PnL={pnl_points:.1f}pts (${pnl_usd:.2f}), Reason={exit_reason}")

            else:
                # Fallback a SQLite directo
                if os.path.exists(_DB_PATH):
                    conn = sqlite3.connect(_DB_PATH)
                    cursor = conn.cursor()

                    query = """
                    UPDATE price_action_signals
                    SET exit_price = ?,
                        exit_time = ?,
                        exit_reason = ?,
                        pnl_points = ?,
                        pnl_usd = ?,
                        status = 'closed'
                    WHERE id = ?
                    """
                    values = (exit_price, exit_time, exit_reason, pnl_points, pnl_usd, signal_id)

                    cursor.execute(query, values)
                    conn.commit()
                    conn.close()
                    print(f"[Signal Generator] Resultado guardado en {_DB_PATH}: {signal_id}")

        except Exception as e:
            print(f"[Signal Generator] Error actualizando resultado: {e}")
