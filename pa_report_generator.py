"""
pa_report_generator.py — Generador de Reportes Visuales
=======================================================
Genera reportes HTML + tablas de rendimiento del sistema:
  • Señales por sesión
  • PnL acumulado
  • Tasa de acierto (accuracy)
  • Resultados de calibración
  • Evolución de parámetros
"""

import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional


_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_DB_PATH = os.path.join(_ENGINE_DIR, "db", "trading_lab.db")
_REPORTS_DIR = os.path.join(_ENGINE_DIR, "reports")


class PriceActionReportGenerator:
    """
    Genera reportes visuales del performance del sistema.
    """

    def __init__(self):
        if not os.path.exists(_REPORTS_DIR):
            os.makedirs(_REPORTS_DIR)
            print(f"[Report] Creado directorio: {_REPORTS_DIR}")

    def generate_session_report(self, session_date: str) -> str:
        """
        Genera reporte HTML para una sesión.

        Args:
            session_date: Formato YYYY-MM-DD

        Returns:
            Ruta del archivo HTML generado
        """
        try:
            # Obtener datos de sesión
            signals = self._get_session_signals(session_date)
            labels = self._get_session_labels(session_date)
            stats = self._calculate_session_stats(signals, labels)

            # Generar HTML
            html = self._build_session_html(session_date, signals, stats)

            # Guardar archivo
            report_path = os.path.join(_REPORTS_DIR, f"session_{session_date}.html")
            with open(report_path, 'w') as f:
                f.write(html)

            print(f"[Report] ✅ Reporte generado: {report_path}")
            return report_path

        except Exception as e:
            print(f"[Report] Error generando reporte: {e}")
            return ""

    def generate_summary_report(self, days: int = 7) -> str:
        """
        Genera reporte resumen de los últimos N días.

        Args:
            days: Número de días a incluir

        Returns:
            Ruta del archivo HTML
        """
        try:
            # Obtener resumen por sesión
            sessions = self._get_sessions_summary(days)

            # Generar HTML
            html = self._build_summary_html(sessions)

            # Guardar
            report_path = os.path.join(_REPORTS_DIR, f"summary_{days}days.html")
            with open(report_path, 'w') as f:
                f.write(html)

            print(f"[Report] ✅ Reporte sumario generado: {report_path}")
            return report_path

        except Exception as e:
            print(f"[Report] Error generando sumario: {e}")
            return ""

    def generate_calibration_report(self, run_id: str) -> str:
        """
        Genera reporte de un run de calibración.

        Args:
            run_id: ID del calibration run

        Returns:
            Ruta del archivo HTML
        """
        try:
            run = self._get_calibration_run(run_id)
            if not run:
                print(f"[Report] Run no encontrado: {run_id}")
                return ""

            html = self._build_calibration_html(run)

            report_path = os.path.join(_REPORTS_DIR, f"calibration_{run_id}.html")
            with open(report_path, 'w') as f:
                f.write(html)

            print(f"[Report] ✅ Reporte calibración: {report_path}")
            return report_path

        except Exception as e:
            print(f"[Report] Error generando calibración: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # DATA RETRIEVAL
    # ─────────────────────────────────────────────────────────────────────────

    def _get_session_signals(self, session_date: str) -> List[dict]:
        """Obtiene todas las señales de una sesión"""
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM price_action_signals
                    WHERE session_date = ?
                    ORDER BY timestamp ASC
                """, (session_date,))

                rows = cursor.fetchall()
                conn.close()
                return [dict(row) for row in rows]

        except Exception as e:
            print(f"[Report] Error obteniendo signals: {e}")

        return []

    def _get_session_labels(self, session_date: str) -> Dict[str, dict]:
        """Obtiene labels de una sesión, indexados por signal_id"""
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM price_action_labels
                    WHERE session_date = ?
                """, (session_date,))

                rows = cursor.fetchall()
                conn.close()
                return {row['signal_id']: dict(row) for row in rows}

        except Exception as e:
            print(f"[Report] Error obteniendo labels: {e}")

        return {}

    def _get_sessions_summary(self, days: int) -> List[dict]:
        """Obtiene resumen de últimas N sesiones"""
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute(f"""
                    SELECT
                        session_date,
                        COUNT(*) as total_signals,
                        SUM(CASE WHEN order_generated=1 THEN 1 ELSE 0 END) as orders_sent,
                        AVG(pnl_points) as avg_pnl_points,
                        SUM(pnl_usd) as total_pnl_usd
                    FROM price_action_signals
                    WHERE session_date >= date('now', '-{days} days')
                    GROUP BY session_date
                    ORDER BY session_date DESC
                """)

                rows = cursor.fetchall()
                conn.close()
                return [dict(row) for row in rows]

        except Exception as e:
            print(f"[Report] Error obteniendo sumario: {e}")

        return []

    def _get_calibration_run(self, run_id: str) -> Optional[dict]:
        """Obtiene datos de un calibration run"""
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM price_action_calibration_runs
                    WHERE run_id = ?
                """, (run_id,))

                row = cursor.fetchone()
                conn.close()
                return dict(row) if row else None

        except Exception as e:
            print(f"[Report] Error obteniendo run: {e}")

        return None

    def _calculate_session_stats(self, signals: List[dict], labels: Dict[str, dict]) -> dict:
        """Calcula estadísticas de sesión"""
        if not signals:
            return {}

        total_signals = len(signals)
        orders_sent = sum(1 for s in signals if s.get('order_generated') == 1)
        labeled_signals = len(labels)

        # PnL
        pnls = [s.get('pnl_usd', 0) for s in signals if s.get('pnl_usd')]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / len(pnls) if pnls else 0

        # Win rate
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) if pnls else 0

        # Accuracy (de labels)
        correct = sum(1 for l in labels.values() if l.get('setup_valid') == 1)
        accuracy = correct / labeled_signals if labeled_signals > 0 else 0

        return {
            'total_signals': total_signals,
            'orders_sent': orders_sent,
            'labeled_signals': labeled_signals,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'num_trades': len(pnls),
            'wins': wins,
            'win_rate': win_rate,
            'accuracy': accuracy,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # HTML GENERATION
    # ─────────────────────────────────────────────────────────────────────────

    def _build_session_html(self, session_date: str, signals: List[dict], stats: dict) -> str:
        """Construye HTML de reporte de sesión"""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Reporte Sesión - {session_date}</title>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1, h2 {{ color: #333; }}
        .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat-box {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
        .stat-label {{ color: #666; margin-top: 5px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #2196F3; color: white; }}
        tr:hover {{ background: #f5f5f5; }}
        .positive {{ color: green; }}
        .negative {{ color: red; }}
        .footer {{ margin-top: 30px; font-size: 12px; color: #999; }}
    </style>
</head>
<body>
    <h1>📊 Reporte de Sesión</h1>
    <h2>{session_date}</h2>

    <div class="stats">
        <div class="stat-box">
            <div class="stat-value">{stats.get('total_signals', 0)}</div>
            <div class="stat-label">Total Señales</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{stats.get('orders_sent', 0)}</div>
            <div class="stat-label">Órdenes Enviadas</div>
        </div>
        <div class="stat-box">
            <div class="stat-value {'positive' if stats.get('total_pnl', 0) > 0 else 'negative'}">${{stats.get('total_pnl', 0):.2f}}</div>
            <div class="stat-label">PnL Total</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{stats.get('win_rate', 0)*100:.1f}%</div>
            <div class="stat-label">Win Rate</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{stats.get('accuracy', 0)*100:.1f}%</div>
            <div class="stat-label">Accuracy</div>
        </div>
    </div>

    <h2>Señales Detalladas</h2>
    <table>
        <tr>
            <th>Timestamp</th>
            <th>Patrón</th>
            <th>Confluencia</th>
            <th>Entrada</th>
            <th>PnL</th>
            <th>Estado</th>
        </tr>
"""
        for signal in signals:
            entry = f"${{{signal.get('entry_price', 0):.2f}}}" if signal.get('order_generated') else "—"
            pnl = f"${{{signal.get('pnl_usd', 0):.2f}}}" if signal.get('pnl_usd') else "—"
            pnl_class = 'positive' if signal.get('pnl_usd', 0) > 0 else ('negative' if signal.get('pnl_usd', 0) < 0 else '')

            html += f"""
        <tr>
            <td>{signal.get('timestamp', '')[:19]}</td>
            <td>{signal.get('pattern_type', '—')} ({signal.get('pattern_direction', '—')})</td>
            <td>{signal.get('confluence_score', 0):.1f}</td>
            <td>{entry}</td>
            <td class="{pnl_class}">{pnl}</td>
            <td>{signal.get('status', '—')}</td>
        </tr>
"""

        html += """
    </table>

    <div class="footer">
        <p>Generado: """ + datetime.now().isoformat() + """</p>
        <p>Price Action Trading System — Eduardo (PRN-Million plus)</p>
    </div>
</body>
</html>
"""
        return html

    def _build_summary_html(self, sessions: List[dict]) -> str:
        """Construye HTML de reporte sumario"""
        total_pnl = sum(s.get('total_pnl_usd', 0) or 0 for s in sessions)
        total_signals = sum(s.get('total_signals', 0) for s in sessions)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Reporte Sumario</title>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1, h2 {{ color: #333; }}
        .header-stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .header-stat {{ background: white; padding: 15px; border-radius: 8px; }}
        .header-stat .value {{ font-size: 20px; font-weight: bold; color: #2196F3; }}
        table {{ width: 100%; border-collapse: collapse; background: white; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #2196F3; color: white; }}
        tr:hover {{ background: #f5f5f5; }}
        .positive {{ color: green; font-weight: bold; }}
        .negative {{ color: red; font-weight: bold; }}
    </style>
</head>
<body>
    <h1>📈 Reporte Sumario</h1>

    <div class="header-stats">
        <div class="header-stat">
            <div class="value">{len(sessions)}</div>
            <div>Sesiones</div>
        </div>
        <div class="header-stat">
            <div class="value">{total_signals}</div>
            <div>Total Señales</div>
        </div>
        <div class="header-stat">
            <div class="value {'positive' if total_pnl > 0 else 'negative'}">${total_pnl:.2f}</div>
            <div>PnL Acumulado</div>
        </div>
    </div>

    <h2>Por Sesión</h2>
    <table>
        <tr>
            <th>Fecha</th>
            <th>Señales</th>
            <th>Órdenes</th>
            <th>PnL</th>
            <th>Avg PnL/Trade</th>
        </tr>
"""

        for session in sessions:
            pnl = session.get('total_pnl_usd') or 0
            avg_pnl = (pnl / session.get('total_signals', 1)) if session.get('total_signals') else 0
            pnl_class = 'positive' if pnl > 0 else ('negative' if pnl < 0 else '')

            html += f"""
        <tr>
            <td>{session['session_date']}</td>
            <td>{session['total_signals']}</td>
            <td>{session.get('orders_sent', 0)}</td>
            <td class="{pnl_class}">${pnl:.2f}</td>
            <td class="{pnl_class}">${avg_pnl:.2f}</td>
        </tr>
"""

        html += """
    </table>

    <div style="margin-top: 30px; font-size: 12px; color: #999;">
        <p>Generado: """ + datetime.now().isoformat() + """</p>
    </div>
</body>
</html>
"""
        return html

    def _build_calibration_html(self, run: dict) -> str:
        """Construye HTML de reporte de calibración"""
        import json

        best_params = json.loads(run.get('best_params', '{}')) if isinstance(run.get('best_params'), str) else run.get('best_params', {})

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Calibración - {run.get('run_id', '')}</title>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1, h2 {{ color: #333; }}
        .score-box {{ background: #2196F3; color: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .score-value {{ font-size: 36px; font-weight: bold; }}
        .params-table {{ background: white; padding: 15px; border-radius: 8px; margin: 20px 0; }}
        pre {{ background: #f0f0f0; padding: 15px; overflow-x: auto; border-radius: 4px; }}
    </style>
</head>
<body>
    <h1>🔧 Calibración Run</h1>
    <h2>{run.get('run_id', '')}</h2>

    <div class="score-box">
        <div>Score Optimizado</div>
        <div class="score-value">{run.get('best_score', 0):.3f}</div>
    </div>

    <h2>Parámetros Encontrados</h2>
    <div class="params-table">
        <pre>
"""

        for key, val in best_params.items():
            if isinstance(val, float):
                html += f"{key:.<40} {val:.4f}\n"
            else:
                html += f"{key:.<40} {val}\n"

        html += f"""
        </pre>
    </div>

    <h2>Detalles</h2>
    <table style="width: 100%; border-collapse: collapse;">
        <tr style="background: #f0f0f0;">
            <td style="padding: 10px; border: 1px solid #ddd;">Timestamp:</td>
            <td style="padding: 10px; border: 1px solid #ddd;">{run.get('timestamp', '')}</td>
        </tr>
        <tr>
            <td style="padding: 10px; border: 1px solid #ddd;">Señales Evaluadas:</td>
            <td style="padding: 10px; border: 1px solid #ddd;">{run.get('num_signals', 0)}</td>
        </tr>
        <tr style="background: #f0f0f0;">
            <td style="padding: 10px; border: 1px solid #ddd;">Notas:</td>
            <td style="padding: 10px; border: 1px solid #ddd;">{run.get('notes', '—')}</td>
        </tr>
    </table>

    <div style="margin-top: 30px; font-size: 12px; color: #999;">
        <p>Generado: """ + datetime.now().isoformat() + """</p>
    </div>
</body>
</html>
"""
        return html


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Price Action Report Generator")
    print("=" * 60)

    generator = PriceActionReportGenerator()

    print("\nOpciones:")
    print("  1. Generar reporte de sesión")
    print("  2. Generar reporte sumario")
    print("  3. Generar reporte de calibración")
    print("  4. Salir")

    choice = input("\nOpción (1-4): ").strip()

    if choice == '1':
        session_date = input("Fecha de sesión (YYYY-MM-DD): ").strip()
        generator.generate_session_report(session_date)

    elif choice == '2':
        days = int(input("Número de días (default 7): ") or "7")
        generator.generate_summary_report(days)

    elif choice == '3':
        run_id = input("Run ID: ").strip()
        generator.generate_calibration_report(run_id)

    print("\n✅ Terminado.\n")
