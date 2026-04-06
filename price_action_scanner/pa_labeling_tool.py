"""
pa_labeling_tool.py — Herramienta de Etiquetado Post-Sesión
============================================================
Permite al usuario validar señales después de la sesión:
  1. ¿Fue setup correcto? (sí/no)
  2. ¿Fue patrón correcto? (sí/no)
  3. ¿Fue confluencia correcta? (sí/no)
  4. Notas cualitativas
  5. Nivel de confianza

Las etiquetas se guardan en price_action_labels tabla.
Usadas para calibration feedback loop.
"""

import os
import sys
import sqlite3
import yaml
from datetime import datetime
from typing import Optional, List
from .pa_signal_schema import CalibrationLabel

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_CONFIG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")
_DB_PATH = os.path.join(_ENGINE_DIR, "db", "trading_lab.db")


class PriceActionLabelingTool:
    """
    Herramienta interactiva para etiquetar señales con ground truth.

    Flujo:
      1. Usuario carga sesión (date)
      2. Lee señales de DB
      3. Para cada señal: valida componentes + anota
      4. Guarda etiquetas en price_action_labels tabla
    """

    def __init__(self, db_manager=None):
        self._db = db_manager
        with open(_CONFIG_PATH) as f:
            self.cfg = yaml.safe_load(f)

    def get_session_signals(self, session_date: str) -> List[dict]:
        """
        Obtiene todas las señales de una sesión.

        Args:
            session_date: Formato YYYY-MM-DD

        Returns:
            Lista de dicts con datos de señal
        """
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                query = """
                SELECT
                    id, timestamp, pattern_type, pattern_direction,
                    pattern_confidence, confluence_factors, confluence_score,
                    price_at_signal, order_generated, status,
                    entry_price, stop_loss, take_profit_1, exit_price,
                    pnl_points, pnl_usd
                FROM price_action_signals
                WHERE session_date = ?
                ORDER BY timestamp ASC
                """

                cursor.execute(query, (session_date,))
                rows = cursor.fetchall()
                conn.close()

                return [dict(row) for row in rows]

        except Exception as e:
            print(f"[Labeling Tool] Error obteniendo señales: {e}")
            return []

    def label_signal(
        self,
        signal_id: str,
        setup_valid: int,
        pattern_correct: int,
        confluencia_correct: int,
        notes: str = "",
        confidence_level: str = "media",
        labeled_by: str = "usuario",
    ) -> bool:
        """
        Guarda etiqueta de ground truth para una señal.

        Args:
            signal_id: ID de la señal
            setup_valid: 1=sí/0=no, era setup correcto
            pattern_correct: 1=sí/0=no, patrón detectado correctamente
            confluencia_correct: 1=sí/0=no, confluencia era real
            notes: Observaciones cualitativas
            confidence_level: 'alta', 'media', 'baja'
            labeled_by: Usuario que etiqueta

        Returns:
            True si guardó exitosamente
        """
        try:
            # Validar rango
            if setup_valid not in (0, 1) or pattern_correct not in (0, 1) or confluencia_correct not in (0, 1):
                print("[Labeling Tool] Valores deben ser 0 o 1")
                return False

            if confidence_level not in ('alta', 'media', 'baja'):
                print("[Labeling Tool] Confianza debe ser: alta, media, baja")
                return False

            # Obtener session_date de la señal
            session_date = self._get_signal_session_date(signal_id)
            if not session_date:
                print(f"[Labeling Tool] Señal {signal_id} no encontrada")
                return False

            # Crear label
            label = CalibrationLabel(
                signal_id=signal_id,
                session_date=session_date,
                setup_valid=setup_valid,
                pattern_correct=pattern_correct,
                confluencia_correct=confluencia_correct,
                notes=notes,
                confidence_level=confidence_level,
                labeled_by=labeled_by,
            )

            # Guardar en DB
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                cursor = conn.cursor()

                query = """
                INSERT OR REPLACE INTO price_action_labels (
                    signal_id, session_date, setup_valid, pattern_correct,
                    confluencia_correct, notes, confidence_level,
                    labeled_at, labeled_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """

                values = (
                    label.signal_id,
                    label.session_date,
                    label.setup_valid,
                    label.pattern_correct,
                    label.confluencia_correct,
                    label.notes,
                    label.confidence_level,
                    label.labeled_at,
                    label.labeled_by,
                )

                cursor.execute(query, values)
                conn.commit()
                conn.close()

                print(f"[Labeling Tool] ✅ Etiqueta guardada: {signal_id} "
                      f"(setup={setup_valid}, pattern={pattern_correct}, conf={confluencia_correct})")
                return True

        except Exception as e:
            print(f"[Labeling Tool] Error guardando etiqueta: {e}")
            return False

    def get_unlabeled_signals(self, session_date: str) -> List[dict]:
        """Obtiene señales sin etiquetar de una sesión"""
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                query = """
                SELECT s.*
                FROM price_action_signals s
                LEFT JOIN price_action_labels l ON s.id = l.signal_id
                WHERE s.session_date = ? AND l.signal_id IS NULL
                ORDER BY s.timestamp ASC
                """

                cursor.execute(query, (session_date,))
                rows = cursor.fetchall()
                conn.close()

                return [dict(row) for row in rows]

        except Exception as e:
            print(f"[Labeling Tool] Error obteniendo no etiquetadas: {e}")
            return []

    def get_session_stats(self, session_date: str) -> dict:
        """
        Retorna estadísticas de etiquetado para una sesión.

        Returns:
            {
                'total_signals': int,
                'labeled_signals': int,
                'unlabeled_signals': int,
                'accuracy': float (% de señales correctas),
                'precision': float (% setup_valid donde pattern_correct),
                'avg_confidence': str,
            }
        """
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                cursor = conn.cursor()

                # Total de señales
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM price_action_signals WHERE session_date = ?",
                    (session_date,)
                )
                total = cursor.fetchone()[0]

                # Etiquetadas
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM price_action_labels WHERE session_date = ?",
                    (session_date,)
                )
                labeled = cursor.fetchone()[0]

                # Correctas (setup_valid=1)
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM price_action_labels WHERE session_date = ? AND setup_valid = 1",
                    (session_date,)
                )
                correct = cursor.fetchone()[0]

                # Precision (pattern correct cuando setup valid)
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM price_action_labels "
                    "WHERE session_date = ? AND setup_valid = 1 AND pattern_correct = 1",
                    (session_date,)
                )
                precision_count = cursor.fetchone()[0]

                # Confianza promedio
                cursor.execute(
                    """SELECT
                        CASE WHEN confidence_level = 'alta' THEN 3
                             WHEN confidence_level = 'media' THEN 2
                             ELSE 1
                        END as conf_score
                    FROM price_action_labels WHERE session_date = ?
                    """,
                    (session_date,)
                )
                conf_scores = [row[0] for row in cursor.fetchall()]
                avg_conf_num = sum(conf_scores) / len(conf_scores) if conf_scores else 0
                avg_conf_map = {3: 'alta', 2: 'media', 1: 'baja'}
                avg_conf = avg_conf_map.get(round(avg_conf_num), 'media')

                conn.close()

                return {
                    'session_date': session_date,
                    'total_signals': total,
                    'labeled_signals': labeled,
                    'unlabeled_signals': total - labeled,
                    'accuracy': (correct / total * 100) if total > 0 else 0,
                    'precision': (precision_count / correct * 100) if correct > 0 else 0,
                    'avg_confidence': avg_conf,
                }

        except Exception as e:
            print(f"[Labeling Tool] Error obteniendo stats: {e}")
            return {}

    def _get_signal_session_date(self, signal_id: str) -> Optional[str]:
        """Busca la session_date de una señal por ID"""
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT session_date FROM price_action_signals WHERE id = ?",
                    (signal_id,)
                )
                row = cursor.fetchone()
                conn.close()

                return row[0] if row else None

        except Exception as e:
            print(f"[Labeling Tool] Error buscando session_date: {e}")

        return None

    def export_session_labels(self, session_date: str, output_path: str) -> bool:
        """
        Exporta etiquetas de una sesión a archivo CSV.

        Args:
            session_date: Formato YYYY-MM-DD
            output_path: Ruta del archivo CSV

        Returns:
            True si exportó exitosamente
        """
        try:
            import csv

            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                query = """
                SELECT s.*, l.setup_valid, l.pattern_correct, l.confluencia_correct,
                       l.notes, l.confidence_level, l.labeled_at
                FROM price_action_signals s
                LEFT JOIN price_action_labels l ON s.id = l.signal_id
                WHERE s.session_date = ?
                ORDER BY s.timestamp ASC
                """

                cursor.execute(query, (session_date,))
                rows = cursor.fetchall()
                conn.close()

                if not rows:
                    print(f"[Labeling Tool] Sin señales para {session_date}")
                    return False

                # Escribir CSV
                with open(output_path, 'w', newline='') as f:
                    fieldnames = [
                        'signal_id', 'timestamp', 'pattern_type', 'pattern_direction',
                        'confluence_score', 'setup_valid', 'pattern_correct',
                        'confluencia_correct', 'notes', 'confidence_level',
                        'entry_price', 'pnl_points', 'pnl_usd', 'status'
                    ]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()

                    for row in rows:
                        writer.writerow({
                            'signal_id': row['id'],
                            'timestamp': row['timestamp'],
                            'pattern_type': row['pattern_type'],
                            'pattern_direction': row['pattern_direction'],
                            'confluence_score': row['confluence_score'],
                            'setup_valid': row['setup_valid'],
                            'pattern_correct': row['pattern_correct'],
                            'confluencia_correct': row['confluencia_correct'],
                            'notes': row['notes'],
                            'confidence_level': row['confidence_level'],
                            'entry_price': row['entry_price'],
                            'pnl_points': row['pnl_points'],
                            'pnl_usd': row['pnl_usd'],
                            'status': row['status'],
                        })

                print(f"[Labeling Tool] ✅ Etiquetas exportadas: {output_path}")
                return True

        except Exception as e:
            print(f"[Labeling Tool] Error exportando: {e}")
            return False


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT - Interfaz interactiva simple
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    labeler = PriceActionLabelingTool()

    print("\n" + "=" * 60)
    print("Price Action Signal Labeling Tool")
    print("=" * 60)

    # Obtener fecha de sesión
    session_date = input("\nFecha de sesión (YYYY-MM-DD): ").strip()

    # Obtener señales sin etiquetar
    unlabeled = labeler.get_unlabeled_signals(session_date)

    if not unlabeled:
        print(f"✅ No hay señales sin etiquetar para {session_date}")
        sys.exit(0)

    print(f"\n📊 {len(unlabeled)} señales sin etiquetar:\n")

    for i, signal in enumerate(unlabeled, 1):
        print(f"\n[{i}/{len(unlabeled)}] Signal ID: {signal['id']}")
        print(f"  Patrón: {signal['pattern_type']} ({signal['pattern_direction']})")
        print(f"  Confianza patrón: {signal['pattern_confidence']:.2f}")
        print(f"  Confluencia: {signal['confluence_score']:.1f}")
        print(f"  Precio: ${signal['price_at_signal']:.2f}")
        print(f"  PnL: {signal['pnl_points']:.1f}pts (${signal['pnl_usd'] or 0:.2f})")

        # Recopilar labels
        print("\n  ¿Validar? (s/n): ", end="")
        if input().lower() != 's':
            continue

        print("  Setup fue correcto (1=sí, 0=no): ", end="")
        setup_valid = int(input().strip() or "0")

        print("  Patrón fue correcto (1=sí, 0=no): ", end="")
        pattern_correct = int(input().strip() or "0")

        print("  Confluencia fue correcta (1=sí, 0=no): ", end="")
        confluencia_correct = int(input().strip() or "0")

        print("  Notas (Enter para saltar): ", end="")
        notes = input().strip()

        print("  Confianza (alta/media/baja): ", end="")
        conf = (input().strip() or "media").lower()

        # Guardar etiqueta
        labeler.label_signal(
            signal_id=signal['id'],
            setup_valid=setup_valid,
            pattern_correct=pattern_correct,
            confluencia_correct=confluencia_correct,
            notes=notes,
            confidence_level=conf,
        )

    # Mostrar estadísticas finales
    print("\n" + "=" * 60)
    stats = labeler.get_session_stats(session_date)
    if stats:
        print(f"📈 Estadísticas de {session_date}:")
        print(f"  Total señales: {stats['total_signals']}")
        print(f"  Etiquetadas: {stats['labeled_signals']}")
        print(f"  Precisión: {stats['accuracy']:.1f}%")
        print(f"  Confianza promedio: {stats['avg_confidence']}")

        # Exportar CSV
        export_path = f"labels_{session_date}.csv"
        labeler.export_session_labels(session_date, export_path)

    print("\n✅ Etiquetado completado.\n")
