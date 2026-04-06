"""
pa_optimizer.py — Optimizador de Parametros via Backtest
========================================================
Conecta el calibrador con el backtester para buscar los parametros
que maximizan rendimiento REAL sobre datos historicos.

Flujo:
  1. Descarga datos historicos (una sola vez)
  2. Define grid de parametros a optimizar
  3. Para cada combinacion:
     a. Genera config temporal con esos parametros
     b. Corre backtest completo
     c. Mide: PnL, Win Rate, Profit Factor, Sharpe, Drawdown
  4. Rankea combinaciones por metrica compuesta
  5. Muestra top 10 y aplica el mejor a pa_config.yaml

Anti-overfitting:
  - Walk-forward (no look-ahead en backtest)
  - Penaliza pocos trades (min 15 para ser valido)
  - Score compuesto que balancea retorno vs riesgo

Uso:
    python pa_optimizer.py --days 30
    python pa_optimizer.py --days 55 --metric sharpe
"""

import argparse
import copy
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import Dict, List, Optional, Tuple

import yaml

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_ENGINE_DIR, ".."))
_CFG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")
_REPORTS_DIR = os.path.join(_ENGINE_DIR, "reports")

for _p in (_ROOT_DIR, _ENGINE_DIR, _SCANNER_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pa_backtester import (
    HistoricalDataProvider,
    PriceActionBacktester,
    BacktestResult,
    generate_backtest_report,
)


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OptimizationRun:
    """Resultado de una combinacion de parametros."""
    params: Dict
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    patterns_found: int = 0
    signals_passed: int = 0
    score: float = 0.0           # Metrica compuesta


@dataclass
class OptimizationResult:
    """Resultado completo de la optimizacion."""
    total_combinations: int = 0
    valid_combinations: int = 0
    elapsed_seconds: float = 0.0
    runs: List[OptimizationRun] = field(default_factory=list)
    best_run: Optional[OptimizationRun] = None
    baseline_run: Optional[OptimizationRun] = None  # Config actual
    improvement_pnl: float = 0.0
    improvement_wr: float = 0.0
    improvement_pf: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# OPTIMIZER ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class PriceActionOptimizer:
    """
    Optimiza parametros corriendo backtests iterativos.
    """

    # ── Grid de parametros a buscar ────────────────────────────────────
    DEFAULT_GRID = {
        # Deteccion de patrones
        'pin_bar_wick_ratio':    [0.55, 0.60, 0.65, 0.70],
        'pin_bar_body_ratio':    [0.25, 0.30, 0.35],
        # Zona S/R
        'zone_tolerance':        [3.0, 4.5, 6.0, 8.0],
        # Risk management
        'sl_distance':           [8.0, 10.0, 12.0, 15.0],
        'tp1_distance':          [15.0, 20.0, 25.0, 30.0],
        'tp2_distance':          [25.0, 35.0, 45.0],
    }

    # Minimo de trades para considerar una combinacion valida
    MIN_TRADES = 10

    def __init__(self):
        with open(_CFG_PATH) as f:
            self.base_cfg = yaml.safe_load(f)

    def optimize(
        self,
        df_1h, df_5m, df_2m,
        grid: Optional[Dict] = None,
        metric: str = 'composite',
        min_trades: int = 10,
        verbose: bool = True,
    ) -> OptimizationResult:
        """
        Ejecuta grid search sobre parametros del backtest.

        Args:
            df_1h, df_5m, df_2m: DataFrames de precio
            grid: Dict de parametros y valores a probar
            metric: 'composite', 'pnl', 'sharpe', 'win_rate', 'profit_factor'
            min_trades: Minimo de trades para considerar valido
            verbose: Imprimir progreso

        Returns:
            OptimizationResult con rankings
        """
        if grid is None:
            grid = self.DEFAULT_GRID

        self.MIN_TRADES = min_trades

        param_names = list(grid.keys())
        param_values = list(grid.values())
        all_combos = list(product(*param_values))
        total = len(all_combos)

        result = OptimizationResult(total_combinations=total)

        print(f"\n{'='*70}")
        print(f"  OPTIMIZADOR DE PARAMETROS — Grid Search via Backtest")
        print(f"{'='*70}")
        print(f"  Parametros:     {len(param_names)}")
        print(f"  Combinaciones:  {total}")
        print(f"  Metrica:        {metric}")
        print(f"  Min trades:     {min_trades}")
        print(f"{'='*70}\n")

        # ── 1. Correr baseline (config actual) ─────────────────────────
        if verbose:
            print("  [BASELINE] Corriendo con config actual...")

        baseline = self._run_backtest_with_params({}, df_1h, df_5m, df_2m, silent=True)
        baseline.score = self._calculate_score(baseline, metric)
        result.baseline_run = baseline

        if verbose:
            print(f"  [BASELINE] PnL={baseline.total_pnl:+.2f}  WR={baseline.win_rate*100:.1f}%  "
                  f"PF={baseline.profit_factor:.2f}  Trades={baseline.total_trades}  "
                  f"Score={baseline.score:.4f}\n")

        # ── 2. Grid search ─────────────────────────────────────────────
        t_start = time.time()
        runs = []
        valid = 0

        for i, combo in enumerate(all_combos):
            params = dict(zip(param_names, combo))

            run = self._run_backtest_with_params(params, df_1h, df_5m, df_2m, silent=True)
            run.score = self._calculate_score(run, metric)
            runs.append(run)

            if run.total_trades >= self.MIN_TRADES:
                valid += 1

            # Progress
            if verbose and (i + 1) % max(total // 20, 1) == 0:
                pct = (i + 1) / total * 100
                elapsed = time.time() - t_start
                eta = (elapsed / (i + 1)) * (total - i - 1)
                print(f"  [{pct:5.1f}%] {i+1}/{total}  "
                      f"Validos: {valid}  "
                      f"ETA: {eta:.0f}s")

        elapsed = time.time() - t_start
        result.elapsed_seconds = elapsed
        result.valid_combinations = valid

        # ── 3. Filtrar y rankear ───────────────────────────────────────
        valid_runs = [r for r in runs if r.total_trades >= self.MIN_TRADES]
        valid_runs.sort(key=lambda r: r.score, reverse=True)

        result.runs = valid_runs

        if valid_runs:
            result.best_run = valid_runs[0]
            result.improvement_pnl = valid_runs[0].total_pnl - baseline.total_pnl
            result.improvement_wr = valid_runs[0].win_rate - baseline.win_rate
            result.improvement_pf = valid_runs[0].profit_factor - baseline.profit_factor

        return result

    def _run_backtest_with_params(
        self, params: Dict, df_1h, df_5m, df_2m, silent: bool = False
    ) -> OptimizationRun:
        """Corre un backtest con parametros modificados."""

        # Crear config temporal
        cfg = copy.deepcopy(self.base_cfg)
        self._apply_params(cfg, params)

        # Escribir config temporal
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.yaml', prefix='pa_opt_')
        try:
            with os.fdopen(tmp_fd, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)

            # Crear backtester con config temporal
            # Suprimir output
            if silent:
                import io
                import contextlib
                f_null = io.StringIO()
                with contextlib.redirect_stdout(f_null):
                    backtester = PriceActionBacktester(config_path=tmp_path)
                    bt_result = backtester.run(df_1h, df_5m, df_2m)
            else:
                backtester = PriceActionBacktester(config_path=tmp_path)
                bt_result = backtester.run(df_1h, df_5m, df_2m)

        finally:
            os.unlink(tmp_path)

        # Convertir a OptimizationRun
        run = OptimizationRun(
            params=params,
            total_trades=bt_result.total_trades,
            win_rate=bt_result.win_rate,
            profit_factor=bt_result.profit_factor,
            total_pnl=bt_result.total_pnl_points,
            avg_pnl=bt_result.avg_pnl_points,
            sharpe=bt_result.sharpe_ratio,
            max_drawdown=bt_result.max_drawdown_points,
            patterns_found=bt_result.patterns_found,
            signals_passed=bt_result.signals_passed,
        )

        return run

    def _apply_params(self, cfg: Dict, params: Dict):
        """Aplica parametros al config dict."""
        if 'pin_bar_wick_ratio' in params:
            cfg['pattern_detectors']['pin_bar']['wick_ratio'] = params['pin_bar_wick_ratio']

        if 'pin_bar_body_ratio' in params:
            cfg['pattern_detectors']['pin_bar']['body_ratio'] = params['pin_bar_body_ratio']

        if 'zone_tolerance' in params:
            cfg['levels']['pivot']['zone_tolerance'] = params['zone_tolerance']
            for level_list in [cfg['levels'].get('resistance', []),
                               cfg['levels'].get('support', [])]:
                if level_list:
                    for level in level_list:
                        level['zone_tolerance'] = params['zone_tolerance']

        if 'sl_distance' in params:
            cfg['execution']['stop_loss_distance'] = params['sl_distance']

        if 'tp1_distance' in params:
            cfg['execution']['take_profit_1_distance'] = params['tp1_distance']

        if 'tp2_distance' in params:
            cfg['execution']['take_profit_2_distance'] = params['tp2_distance']

    def _calculate_score(self, run: OptimizationRun, metric: str) -> float:
        """
        Calcula score compuesto.

        Anti-overfitting:
          - Penaliza combinaciones con pocos trades
          - Penaliza drawdowns altos
          - Balancea retorno vs consistencia
        """
        if run.total_trades < self.MIN_TRADES:
            return -999.0

        if metric == 'pnl':
            return run.total_pnl

        if metric == 'sharpe':
            return run.sharpe

        if metric == 'win_rate':
            return run.win_rate

        if metric == 'profit_factor':
            return run.profit_factor if run.profit_factor < 100 else 0

        # Composite (default): balancea todo
        # PnL normalizado + WR bonus + PF bonus - DD penalty + trade count bonus
        pnl_score = run.total_pnl / 10.0  # Normalizar

        wr_bonus = (run.win_rate - 0.50) * 20 if run.win_rate > 0.50 else (run.win_rate - 0.50) * 40

        pf_score = min(run.profit_factor, 5.0) * 3 if run.profit_factor > 1.0 else (run.profit_factor - 1.0) * 10

        dd_penalty = run.max_drawdown * 0.3

        # Mas trades = mas confianza en el resultado
        trade_bonus = min(run.total_trades / 50.0, 1.0) * 5

        sharpe_bonus = run.sharpe * 2

        score = pnl_score + wr_bonus + pf_score - dd_penalty + trade_bonus + sharpe_bonus

        return score

    def apply_best_params(self, best_params: Dict) -> bool:
        """Aplica los mejores parametros a pa_config.yaml."""
        try:
            with open(_CFG_PATH) as f:
                cfg = yaml.safe_load(f)

            self._apply_params(cfg, best_params)

            # Backup antes de escribir
            backup_path = _CFG_PATH + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(_CFG_PATH) as f:
                backup_content = f.read()
            with open(backup_path, 'w') as f:
                f.write(backup_content)

            # Escribir nueva config
            with open(_CFG_PATH, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)

            print(f"\n  [+] Backup guardado: {backup_path}")
            print(f"  [+] pa_config.yaml actualizado con parametros optimizados")
            return True

        except Exception as e:
            print(f"  [!] Error aplicando parametros: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═══════════════════════════════════════════════════════════════════════════

def print_optimization_report(result: OptimizationResult):
    """Imprime resultados de optimizacion."""
    W = 70
    print(f"\n{'='*W}")
    print(f"  OPTIMIZATION RESULTS")
    print(f"{'='*W}")
    print(f"  Combinaciones probadas:  {result.total_combinations}")
    print(f"  Combinaciones validas:   {result.valid_combinations}")
    print(f"  Tiempo:                  {result.elapsed_seconds:.1f}s")
    print(f"{'-'*W}")

    # Baseline
    b = result.baseline_run
    if b:
        print(f"\n  BASELINE (config actual):")
        print(f"    PnL:            {b.total_pnl:+8.2f} pts")
        print(f"    Win Rate:       {b.win_rate*100:8.1f}%")
        print(f"    Profit Factor:  {b.profit_factor:8.2f}")
        print(f"    Sharpe:         {b.sharpe:8.2f}")
        print(f"    Trades:         {b.total_trades:8d}")
        print(f"    Max DD:         {b.max_drawdown:8.2f} pts")
        print(f"    Score:          {b.score:8.4f}")

    # Best
    best = result.best_run
    if best:
        print(f"\n  MEJOR COMBINACION:")
        print(f"    PnL:            {best.total_pnl:+8.2f} pts  ({result.improvement_pnl:+.2f})")
        print(f"    Win Rate:       {best.win_rate*100:8.1f}%  ({result.improvement_wr*100:+.1f}pp)")
        print(f"    Profit Factor:  {best.profit_factor:8.2f}  ({result.improvement_pf:+.2f})")
        print(f"    Sharpe:         {best.sharpe:8.2f}")
        print(f"    Trades:         {best.total_trades:8d}")
        print(f"    Max DD:         {best.max_drawdown:8.2f} pts")
        print(f"    Score:          {best.score:8.4f}")
        print(f"\n    Parametros:")
        for k, v in best.params.items():
            if isinstance(v, float):
                print(f"      {k:<25s} {v:.2f}")
            else:
                print(f"      {k:<25s} {v}")

    # Top 10
    print(f"\n{'='*W}")
    print(f"  TOP 10 COMBINACIONES")
    print(f"{'='*W}")
    print(f"  {'#':>3}  {'PnL':>8}  {'WR':>6}  {'PF':>6}  {'Sharpe':>7}  {'Trades':>6}  {'DD':>7}  {'Score':>8}  Params")
    print(f"  {'-'*W}")

    for i, run in enumerate(result.runs[:10], 1):
        params_str = " | ".join(f"{k}={v}" for k, v in run.params.items())
        if len(params_str) > 40:
            params_str = params_str[:37] + "..."
        print(f"  {i:>3}  {run.total_pnl:>+8.1f}  {run.win_rate*100:>5.1f}%  "
              f"{run.profit_factor:>6.2f}  {run.sharpe:>7.2f}  {run.total_trades:>6d}  "
              f"{run.max_drawdown:>7.1f}  {run.score:>8.2f}  {params_str}")

    # Worst 3
    if len(result.runs) >= 5:
        print(f"\n  PEORES 3 COMBINACIONES (para referencia):")
        for run in result.runs[-3:]:
            params_str = " | ".join(f"{k}={v}" for k, v in run.params.items())
            print(f"    PnL={run.total_pnl:+.1f}  WR={run.win_rate*100:.0f}%  "
                  f"PF={run.profit_factor:.2f}  Trades={run.total_trades}  {params_str}")

    print(f"\n{'='*W}")


# ═══════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ═══════════════════════════════════════════════════════════════════════════

def generate_optimization_html(result: OptimizationResult, output_dir: str = None) -> str:
    """Genera reporte HTML de optimizacion."""
    if output_dir is None:
        output_dir = _REPORTS_DIR
    os.makedirs(output_dir, exist_ok=True)

    filepath = os.path.join(output_dir, f"optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")

    b = result.baseline_run
    best = result.best_run

    # Table rows
    rows = ""
    for i, run in enumerate(result.runs[:30], 1):
        is_best = (i == 1)
        row_style = 'style="background:#0d2137"' if is_best else ''
        pnl_cls = "positive" if run.total_pnl > 0 else "negative"
        params_str = " | ".join(f"{k}={v}" for k, v in run.params.items())

        rows += f"""
        <tr {row_style}>
          <td>{'&#9733; ' if is_best else ''}{i}</td>
          <td class="{pnl_cls}">{run.total_pnl:+.2f}</td>
          <td>{run.win_rate*100:.1f}%</td>
          <td>{run.profit_factor:.2f}</td>
          <td>{run.sharpe:.2f}</td>
          <td>{run.total_trades}</td>
          <td>{run.max_drawdown:.1f}</td>
          <td>{run.score:.2f}</td>
          <td style="font-size:11px;color:#8b949e">{params_str}</td>
        </tr>"""

    # Param comparison (best vs baseline)
    param_compare = ""
    if best:
        for k, v in best.params.items():
            # Get baseline value
            baseline_val = "—"
            if k == 'pin_bar_wick_ratio':
                baseline_val = "0.65"
            elif k == 'pin_bar_body_ratio':
                baseline_val = "0.30"
            elif k == 'zone_tolerance':
                baseline_val = "4.5"
            elif k == 'sl_distance':
                baseline_val = "12.0"
            elif k == 'tp1_distance':
                baseline_val = "20.0"
            elif k == 'tp2_distance':
                baseline_val = "35.0"

            changed = str(v) != baseline_val
            color = "var(--green)" if changed else "#8b949e"
            param_compare += f"""
            <tr>
              <td>{k}</td>
              <td>{baseline_val}</td>
              <td style="color:{color};font-weight:{'700' if changed else '400'}">{v}</td>
              <td>{'CHANGED' if changed else '—'}</td>
            </tr>"""

    imp_pnl_cls = "positive" if result.improvement_pnl > 0 else "negative"
    imp_wr_cls = "positive" if result.improvement_wr > 0 else "negative"
    imp_pf_cls = "positive" if result.improvement_pf > 0 else "negative"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Optimization Report — Price Action Scanner</title>
<style>
  :root {{ --green: #4CAF50; --red: #f44336; --orange: #FF9800; --blue: #2196F3; --gold: #FFD700; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; }}
  h1 {{ color: #58a6ff; margin-bottom: 4px; }}
  h2 {{ color: #8b949e; margin: 30px 0 14px; border-bottom: 1px solid #21262d; padding-bottom: 8px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 28px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 20px 0; }}
  .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; }}
  .card .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .5px; }}
  .card .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .card .detail {{ font-size: 12px; color: #484f58; margin-top: 2px; }}
  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 25px; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 10px 12px; font-size: 11px;
       text-transform: uppercase; letter-spacing: .5px; border-bottom: 2px solid #21262d; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  tr:hover td {{ background: #161b22; }}
  .vs {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: 20px; margin: 20px 0; align-items: start; }}
  .vs-card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 20px; }}
  .vs-card h3 {{ color: #8b949e; margin-bottom: 12px; font-size: 13px; text-transform: uppercase; }}
  .vs-arrow {{ color: var(--gold); font-size: 36px; align-self: center; }}
  .vs-metric {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #21262d; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #21262d; font-size: 12px; color: #484f58; }}
</style>
</head>
<body>

<h1>Optimization Report</h1>
<div class="subtitle">
  Grid Search via Backtest &mdash;
  {result.total_combinations} combinaciones &mdash;
  {result.elapsed_seconds:.0f}s &mdash;
  {datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>

<!-- ─── IMPROVEMENT CARDS ─────────────────────────────────────────── -->
<div class="grid">
  <div class="card">
    <div class="label">Mejora PnL</div>
    <div class="value {imp_pnl_cls}">{result.improvement_pnl:+.2f} pts</div>
    <div class="detail">{b.total_pnl:+.2f} &rarr; {best.total_pnl:+.2f}</div>
  </div>
  <div class="card">
    <div class="label">Mejora Win Rate</div>
    <div class="value {imp_wr_cls}">{result.improvement_wr*100:+.1f}pp</div>
    <div class="detail">{b.win_rate*100:.1f}% &rarr; {best.win_rate*100:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">Mejora Profit Factor</div>
    <div class="value {imp_pf_cls}">{result.improvement_pf:+.2f}</div>
    <div class="detail">{b.profit_factor:.2f} &rarr; {best.profit_factor:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Combinaciones Validas</div>
    <div class="value">{result.valid_combinations}</div>
    <div class="detail">de {result.total_combinations} probadas</div>
  </div>
</div>

<!-- ─── BASELINE vs OPTIMIZED ─────────────────────────────────────── -->
<h2>Baseline vs Optimizado</h2>
<div class="vs">
  <div class="vs-card">
    <h3>Config Actual (Baseline)</h3>
    <div class="vs-metric"><span>PnL</span><span>{b.total_pnl:+.2f} pts</span></div>
    <div class="vs-metric"><span>Win Rate</span><span>{b.win_rate*100:.1f}%</span></div>
    <div class="vs-metric"><span>Profit Factor</span><span>{b.profit_factor:.2f}</span></div>
    <div class="vs-metric"><span>Sharpe</span><span>{b.sharpe:.2f}</span></div>
    <div class="vs-metric"><span>Trades</span><span>{b.total_trades}</span></div>
    <div class="vs-metric"><span>Max DD</span><span>{b.max_drawdown:.1f} pts</span></div>
  </div>
  <div class="vs-arrow">&rarr;</div>
  <div class="vs-card" style="border-color:var(--gold)">
    <h3 style="color:var(--gold)">Optimizado</h3>
    <div class="vs-metric"><span>PnL</span><span class="{imp_pnl_cls}">{best.total_pnl:+.2f} pts</span></div>
    <div class="vs-metric"><span>Win Rate</span><span class="{imp_wr_cls}">{best.win_rate*100:.1f}%</span></div>
    <div class="vs-metric"><span>Profit Factor</span><span class="{imp_pf_cls}">{best.profit_factor:.2f}</span></div>
    <div class="vs-metric"><span>Sharpe</span><span>{best.sharpe:.2f}</span></div>
    <div class="vs-metric"><span>Trades</span><span>{best.total_trades}</span></div>
    <div class="vs-metric"><span>Max DD</span><span>{best.max_drawdown:.1f} pts</span></div>
  </div>
</div>

<!-- ─── PARAMETER CHANGES ─────────────────────────────────────────── -->
<h2>Cambios en Parametros</h2>
<table>
  <tr><th>Parametro</th><th>Baseline</th><th>Optimizado</th><th>Status</th></tr>
  {param_compare}
</table>

<!-- ─── RANKING TABLE ─────────────────────────────────────────────── -->
<h2>Top 30 Combinaciones</h2>
<table>
  <tr>
    <th>#</th><th>PnL</th><th>WR</th><th>PF</th><th>Sharpe</th>
    <th>Trades</th><th>Max DD</th><th>Score</th><th>Parametros</th>
  </tr>
  {rows}
</table>

<div class="footer">
  <p>Generado: {datetime.now().isoformat()}</p>
  <p>Optimizer &mdash; Price Action Trading System &mdash; Eduardo (PRN-Million plus)</p>
  <p>Metodo: Grid search exhaustivo. Cada combinacion ejecuta un backtest completo walk-forward.</p>
  <p><em>Nota: Optimizacion sobre datos historicos puede llevar a overfitting.
  Validar resultados con datos out-of-sample antes de operar en vivo.</em></p>
</div>

</body>
</html>"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    return filepath


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Optimizador de parametros via backtest")
    parser.add_argument("--days", type=int, default=30, help="Dias de datos historicos")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--symbol", default="^GSPC")
    parser.add_argument("--metric", default="composite",
                        choices=["composite", "pnl", "sharpe", "win_rate", "profit_factor"],
                        help="Metrica a optimizar")
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--apply", action="store_true", help="Aplicar mejores parametros automaticamente")
    parser.add_argument("--quick", action="store_true", help="Grid reducido (rapido)")
    parser.add_argument("--no-html", action="store_true")
    args = parser.parse_args()

    # ── 1. Descargar datos ─────────────────────────────────────────────
    provider = HistoricalDataProvider(symbol=args.symbol)
    df_1h, df_5m, df_2m = provider.fetch(start=args.start, end=args.end, days=args.days)

    if df_2m.empty:
        print("Sin datos. Abortando.")
        sys.exit(1)

    # ── 2. Definir grid ────────────────────────────────────────────────
    if args.quick:
        grid = {
            'zone_tolerance':   [3.0, 4.5, 6.0, 8.0],
            'sl_distance':      [8.0, 10.0, 12.0, 15.0],
            'tp1_distance':     [15.0, 20.0, 25.0],
            'tp2_distance':     [25.0, 35.0, 45.0],
        }
    else:
        grid = None  # Uses DEFAULT_GRID

    # ── 3. Optimizar ──────────────────────────────────────────────────
    optimizer = PriceActionOptimizer()
    result = optimizer.optimize(
        df_1h, df_5m, df_2m,
        grid=grid,
        metric=args.metric,
        min_trades=args.min_trades,
    )

    # ── 4. Reportar ──────────────────────────────────────────────────
    print_optimization_report(result)

    if not args.no_html:
        html_path = generate_optimization_html(result)
        print(f"\n  📊 Reporte HTML: {html_path}\n")

    # ── 5. Aplicar? ──────────────────────────────────────────────────
    if args.apply and result.best_run:
        print("\n  Aplicando mejores parametros a pa_config.yaml...")
        optimizer.apply_best_params(result.best_run.params)
    elif result.best_run and not args.apply:
        print("\n  Para aplicar los mejores parametros:")
        print("    python pa_optimizer.py --days 30 --apply")


if __name__ == "__main__":
    main()
