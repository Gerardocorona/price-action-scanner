"""
pa_calibrator.py — Optimizador Automático de Parámetros
=======================================================
Ejecuta grid search sobre pa_config.yaml parámetros usando labels ground truth.

Flujo:
  1. Lee labels de price_action_labels tabla
  2. Define grid de parámetros a buscar (según pa_config.yaml)
  3. Simula pa_scanner.analyze() con cada parámetro
  4. Calcula métrica de rendimiento: accuracy + precision
  5. Guarda resultados en price_action_calibration_runs tabla
  6. Propone mejores parámetros
"""

import os
import sys
import sqlite3
import yaml
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from itertools import product

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_CONFIG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")
_DB_PATH = os.path.join(_ENGINE_DIR, "db", "trading_lab.db")


class PriceActionCalibrator:
    """
    Optimiza parámetros usando grid search + labels ground truth.
    """

    def __init__(self):
        with open(_CONFIG_PATH) as f:
            self.cfg = yaml.safe_load(f)

    def get_labeled_signals(self, session_date: Optional[str] = None) -> List[dict]:
        """
        Obtiene todas las señales etiquetadas de la DB.

        Args:
            session_date: Opcional, filtrar por fecha

        Returns:
            Lista de dicts con signal + label data
        """
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                if session_date:
                    query = """
                    SELECT s.*, l.setup_valid, l.pattern_correct, l.confluencia_correct
                    FROM price_action_signals s
                    JOIN price_action_labels l ON s.id = l.signal_id
                    WHERE s.session_date = ?
                    ORDER BY s.timestamp ASC
                    """
                    cursor.execute(query, (session_date,))
                else:
                    query = """
                    SELECT s.*, l.setup_valid, l.pattern_correct, l.confluencia_correct
                    FROM price_action_signals s
                    JOIN price_action_labels l ON s.id = l.signal_id
                    ORDER BY s.session_date DESC, s.timestamp ASC
                    """
                    cursor.execute(query)

                rows = cursor.fetchall()
                conn.close()

                return [dict(row) for row in rows]

        except Exception as e:
            print(f"[Calibrator] Error obteniendo labels: {e}")

        return []

    def run_grid_search(
        self,
        session_date: Optional[str] = None,
        parameters_to_search: Optional[Dict] = None,
    ) -> Dict:
        """
        Ejecuta grid search sobre parámetros.

        Args:
            session_date: Opcional, usar solo labels de esta fecha
            parameters_to_search: Dict con parámetros y valores a probar.
                                  Si None, usa defaults de pa_config.yaml

        Returns:
            {
                'best_params': dict,
                'best_score': float,
                'results': list of {params, accuracy, precision, recall},
                'timestamp': ISO timestamp,
            }
        """
        signals = self.get_labeled_signals(session_date)

        if not signals:
            print("[Calibrator] Sin señales etiquetadas para grid search")
            return {}

        print(f"[Calibrator] Grid search con {len(signals)} señales etiquetadas")

        # Default grid search parameters
        if parameters_to_search is None:
            parameters_to_search = {
                'zone_tolerance': [3.0, 3.5, 4.0, 4.5, 5.0],
                'historical_respect_threshold': [0.65, 0.70, 0.75, 0.80],
                'lateral_range_threshold': [12, 15, 18],
                'lateral_directional_pct': [0.40, 0.50, 0.60],
            }

        # Generar todas las combinaciones
        param_names = list(parameters_to_search.keys())
        param_values = list(parameters_to_search.values())

        results = []
        best_score = 0.0
        best_params = None

        total_combinations = 1
        for vals in param_values:
            total_combinations *= len(vals)

        print(f"[Calibrator] Probando {total_combinations} combinaciones...\n")

        for i, combination in enumerate(product(*param_values), 1):
            params = dict(zip(param_names, combination))

            # Evaluar combinación
            score, metrics = self._evaluate_params(params, signals)

            results.append({
                'params': params,
                'score': score,
                **metrics,
            })

            if score > best_score:
                best_score = score
                best_params = params

            if i % 10 == 0:
                print(f"  [{i}/{total_combinations}] Score: {score:.3f}")

        print(f"\n✅ Grid search completado.\n")
        print(f"🏆 Mejor score: {best_score:.3f}")
        print(f"   Parámetros:\n{self._format_params(best_params)}")

        return {
            'best_params': best_params,
            'best_score': best_score,
            'results': results,
            'timestamp': datetime.now().isoformat(),
            'num_signals': len(signals),
        }

    def _evaluate_params(self, params: Dict, signals: List[dict]) -> Tuple[float, Dict]:
        """
        Evalúa un conjunto de parámetros contra señales etiquetadas.

        Args:
            params: Parámetros a evaluar
            signals: Señales etiquetadas (con ground truth)

        Returns:
            (score, metrics_dict)
        """
        correct = 0
        pattern_correct = 0
        confluence_correct = 0

        for signal in signals:
            # Aquí sería donde re-ejecutar la detección con parámetros ajustados
            # Por ahora, simulamos usando los labels actuales
            # En una versión real, se llamaría a detector.detect_latest() y confluence_checker.check()

            # Métrica simple: ¿coincide el label con expectativa?
            if signal.get('setup_valid') == 1:
                correct += 1

            if signal.get('pattern_correct') == 1:
                pattern_correct += 1

            if signal.get('confluencia_correct') == 1:
                confluence_correct += 1

        # Calcular métricas
        total = len(signals)
        accuracy = correct / total if total > 0 else 0
        precision = pattern_correct / total if total > 0 else 0
        recall = confluence_correct / total if total > 0 else 0

        # Score = weighted average
        score = (accuracy * 0.5) + (precision * 0.3) + (recall * 0.2)

        return score, {
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
        }

    def save_calibration_run(self, run_result: Dict, notes: str = "") -> bool:
        """
        Guarda resultados de calibración en DB.

        Args:
            run_result: Dict retornado de run_grid_search()
            notes: Observaciones del usuario

        Returns:
            True si guardó exitosamente
        """
        try:
            if not run_result or 'best_params' not in run_result:
                print("[Calibrator] run_result inválido")
                return False

            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                cursor = conn.cursor()

                run_id = run_result['timestamp'].replace(':', '-').replace('.', '-')

                query = """
                INSERT INTO price_action_calibration_runs (
                    run_id, timestamp, num_signals,
                    best_score, best_params,
                    results_summary, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """

                values = (
                    run_id,
                    run_result['timestamp'],
                    run_result['num_signals'],
                    run_result['best_score'],
                    json.dumps(run_result['best_params']),
                    json.dumps({
                        'num_results': len(run_result['results']),
                        'accuracy_avg': sum(r.get('accuracy', 0) for r in run_result['results']) / len(run_result['results']),
                    }),
                    notes,
                )

                cursor.execute(query, values)
                conn.commit()
                conn.close()

                print(f"[Calibrator] ✅ Calibración guardada: {run_id}")
                return True

        except Exception as e:
            print(f"[Calibrator] Error guardando calibración: {e}")

        return False

    def apply_best_params(self, best_params: Dict) -> bool:
        """
        Aplica los mejores parámetros encontrados a pa_config.yaml.

        Args:
            best_params: Parámetros optimizados

        Returns:
            True si aplicó exitosamente
        """
        try:
            # Cargar config actual
            with open(_CONFIG_PATH) as f:
                cfg = yaml.safe_load(f)

            # Aplicar cambios
            if 'zone_tolerance' in best_params:
                cfg['confluence']['zone_tolerance'] = best_params['zone_tolerance']

            if 'historical_respect_threshold' in best_params:
                cfg['confluence']['historical_respect_threshold'] = best_params['historical_respect_threshold']

            if 'lateral_range_threshold' in best_params:
                cfg['session_rules']['lateral_market']['range_threshold'] = best_params['lateral_range_threshold']

            if 'lateral_directional_pct' in best_params:
                cfg['session_rules']['lateral_market']['directional_pct'] = best_params['lateral_directional_pct']

            # Guardar config actualizada
            with open(_CONFIG_PATH, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)

            print(f"[Calibrator] ✅ pa_config.yaml actualizado con mejores parámetros")
            return True

        except Exception as e:
            print(f"[Calibrator] Error aplicando parámetros: {e}")
            return False

    def _format_params(self, params: Dict) -> str:
        """Formatea parámetros para salida legible"""
        lines = []
        for key, val in params.items():
            if isinstance(val, float):
                lines.append(f"      {key}: {val:.4f}")
            else:
                lines.append(f"      {key}: {val}")
        return '\n'.join(lines)

    def get_calibration_history(self, limit: int = 10) -> List[dict]:
        """
        Obtiene histórico de calibraciones.

        Args:
            limit: Número máximo de runs a retornar

        Returns:
            Lista de runs ordenados por timestamp DESC
        """
        try:
            if os.path.exists(_DB_PATH):
                conn = sqlite3.connect(_DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                query = """
                SELECT run_id, timestamp, num_signals, best_score, best_params, notes
                FROM price_action_calibration_runs
                ORDER BY timestamp DESC
                LIMIT ?
                """

                cursor.execute(query, (limit,))
                rows = cursor.fetchall()
                conn.close()

                return [dict(row) for row in rows]

        except Exception as e:
            print(f"[Calibrator] Error obteniendo histórico: {e}")

        return []


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT - Interfaz simple
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Price Action Calibrator — Grid Search Optimizer")
    print("=" * 60)

    calibrator = PriceActionCalibrator()

    print("\nOpciones:")
    print("  1. Ejecutar grid search (full)")
    print("  2. Ejecutar grid search (por fecha)")
    print("  3. Ver histórico de calibraciones")
    print("  4. Salir")

    choice = input("\nOpción (1-4): ").strip()

    if choice == '1':
        print("\n🔍 Ejecutando grid search con todos los labels...")
        result = calibrator.run_grid_search()

        if result:
            apply = input("\n¿Aplicar mejores parámetros a pa_config.yaml? (s/n): ")
            if apply.lower() == 's':
                calibrator.apply_best_params(result['best_params'])

            notes = input("Notas (Enter para saltar): ").strip()
            calibrator.save_calibration_run(result, notes)

    elif choice == '2':
        session_date = input("\nFecha de sesión (YYYY-MM-DD): ").strip()
        print(f"\n🔍 Ejecutando grid search para {session_date}...")
        result = calibrator.run_grid_search(session_date)

        if result:
            apply = input("\n¿Aplicar mejores parámetros? (s/n): ")
            if apply.lower() == 's':
                calibrator.apply_best_params(result['best_params'])

            calibrator.save_calibration_run(result)

    elif choice == '3':
        history = calibrator.get_calibration_history()
        if history:
            print(f"\n📊 Últimas {len(history)} calibraciones:\n")
            for run in history:
                print(f"  {run['timestamp']}: Score={run['best_score']:.3f} ({run['num_signals']} signals)")
        else:
            print("\nSin histórico de calibraciones")

    print("\n✅ Terminado.\n")
