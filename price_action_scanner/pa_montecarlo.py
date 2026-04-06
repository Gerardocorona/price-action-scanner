"""
pa_montecarlo.py — Simulación de Monte Carlo para Price Action Scanner
======================================================================
Toma los PnL reales del backtest y ejecuta N simulaciones aleatorias
para proyectar distribuciones de rendimiento futuro.

Genera:
  - Percentiles de PnL esperado (P5, P25, P50, P75, P95)
  - Probabilidad de ruina (drawdown > umbral)
  - Distribución de max drawdown
  - Equity curves simuladas
  - Reporte HTML con visualizaciones

Uso:
    # Desde resultados del backtester:
    python pa_montecarlo.py

    # O importar directamente:
    from pa_montecarlo import MonteCarloSimulator
    mc = MonteCarloSimulator(trade_pnls=[+20, -12, +35, ...])
    report = mc.run(simulations=10000, trades_per_sim=100)
"""

import math
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_REPORTS_DIR = os.path.join(_ENGINE_DIR, "reports")


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MonteCarloResult:
    """Resultado completo de la simulación Monte Carlo."""
    # Config
    num_simulations: int = 0
    trades_per_sim: int = 0
    source_trades: int = 0

    # PnL Distribution
    pnl_mean: float = 0.0
    pnl_median: float = 0.0
    pnl_std: float = 0.0
    pnl_p5: float = 0.0        # Percentil 5  (peor caso realista)
    pnl_p10: float = 0.0
    pnl_p25: float = 0.0
    pnl_p50: float = 0.0       # Mediana
    pnl_p75: float = 0.0
    pnl_p90: float = 0.0
    pnl_p95: float = 0.0       # Mejor caso realista

    # Win Rate Distribution
    wr_mean: float = 0.0
    wr_p5: float = 0.0
    wr_p50: float = 0.0
    wr_p95: float = 0.0

    # Drawdown Distribution
    dd_mean: float = 0.0
    dd_median: float = 0.0
    dd_p5: float = 0.0         # Drawdown menor (mejor)
    dd_p50: float = 0.0
    dd_p95: float = 0.0        # Drawdown severo
    dd_max: float = 0.0        # Peor drawdown observado

    # Risk of Ruin
    prob_ruin_25: float = 0.0   # P(drawdown > 25 pts)
    prob_ruin_50: float = 0.0   # P(drawdown > 50 pts)
    prob_ruin_100: float = 0.0  # P(drawdown > 100 pts)
    prob_negative: float = 0.0  # P(PnL < 0)

    # Profit Factor Distribution
    pf_mean: float = 0.0
    pf_p5: float = 0.0
    pf_p50: float = 0.0
    pf_p95: float = 0.0

    # Expectancy
    expectancy_mean: float = 0.0
    expectancy_p5: float = 0.0
    expectancy_p95: float = 0.0

    # Raw data for plotting
    all_final_pnls: List[float] = field(default_factory=list)
    all_max_drawdowns: List[float] = field(default_factory=list)
    all_win_rates: List[float] = field(default_factory=list)
    sample_equity_curves: List[List[float]] = field(default_factory=list)

    # Source trade stats
    source_win_rate: float = 0.0
    source_avg_win: float = 0.0
    source_avg_loss: float = 0.0
    source_total_pnl: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# MONTE CARLO SIMULATOR
# ═══════════════════════════════════════════════════════════════════════════

class MonteCarloSimulator:
    """
    Simulador de Monte Carlo para proyectar rendimiento futuro.

    Método: Bootstrap con reemplazo
      - Toma la distribución real de PnL por trade del backtest
      - En cada simulación, muestrea N trades aleatoriamente (con reemplazo)
      - Calcula equity curve, drawdown, win rate, profit factor
      - Repite S veces para obtener distribuciones
    """

    def __init__(self, trade_pnls: List[float]):
        """
        Args:
            trade_pnls: Lista de PnL por trade (en puntos) del backtest real
        """
        if not trade_pnls:
            raise ValueError("Se necesita al menos 1 trade para simulación")

        self.trade_pnls = trade_pnls
        self.n_source = len(trade_pnls)

        # Estadísticas de la muestra original
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]

        self.source_win_rate = len(wins) / len(trade_pnls)
        self.source_avg_win = sum(wins) / len(wins) if wins else 0
        self.source_avg_loss = sum(losses) / len(losses) if losses else 0
        self.source_total_pnl = sum(trade_pnls)

    def run(
        self,
        simulations: int = 10000,
        trades_per_sim: int = 100,
        seed: Optional[int] = None,
    ) -> MonteCarloResult:
        """
        Ejecuta la simulación Monte Carlo.

        Args:
            simulations:    Número de simulaciones (default 10,000)
            trades_per_sim: Trades por simulación (default 100)
            seed:           Seed para reproducibilidad (optional)

        Returns:
            MonteCarloResult con distribuciones completas
        """
        if seed is not None:
            random.seed(seed)

        print(f"\n{'='*60}")
        print(f"  MONTE CARLO SIMULATION")
        print(f"  {simulations:,} simulaciones x {trades_per_sim} trades")
        print(f"  Basado en {self.n_source} trades reales")
        print(f"{'='*60}\n")

        all_final_pnls = []
        all_max_drawdowns = []
        all_win_rates = []
        all_profit_factors = []
        all_expectancies = []
        sample_curves = []

        # Guardar ~50 curvas de ejemplo para el gráfico
        save_curve_every = max(simulations // 50, 1)

        for s in range(simulations):
            # ── Muestrear trades con reemplazo ────────────────────────────
            sampled = random.choices(self.trade_pnls, k=trades_per_sim)

            # ── Construir equity curve ────────────────────────────────────
            equity = 0.0
            peak = 0.0
            max_dd = 0.0
            curve = [0.0]
            wins = 0
            gross_profit = 0.0
            gross_loss = 0.0

            for pnl in sampled:
                equity += pnl
                curve.append(equity)

                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd

                if pnl > 0:
                    wins += 1
                    gross_profit += pnl
                elif pnl < 0:
                    gross_loss += abs(pnl)

            # ── Métricas de esta simulación ───────────────────────────────
            all_final_pnls.append(equity)
            all_max_drawdowns.append(max_dd)
            all_win_rates.append(wins / trades_per_sim)

            pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
            if pf != float('inf'):
                all_profit_factors.append(pf)

            all_expectancies.append(equity / trades_per_sim)

            if s % save_curve_every == 0:
                sample_curves.append(curve)

            # Progress
            if s % (simulations // 10) == 0 and s > 0:
                pct = s / simulations * 100
                print(f"  [{pct:5.1f}%] {s:,}/{simulations:,} simulaciones...")

        print(f"  [100.0%] {simulations:,}/{simulations:,} simulaciones completadas\n")

        # ── Compilar resultado ────────────────────────────────────────────
        result = MonteCarloResult(
            num_simulations=simulations,
            trades_per_sim=trades_per_sim,
            source_trades=self.n_source,
            source_win_rate=self.source_win_rate,
            source_avg_win=self.source_avg_win,
            source_avg_loss=self.source_avg_loss,
            source_total_pnl=self.source_total_pnl,
        )

        # PnL distribution
        all_final_pnls.sort()
        result.all_final_pnls = all_final_pnls
        result.pnl_mean = sum(all_final_pnls) / len(all_final_pnls)
        result.pnl_std = math.sqrt(
            sum((x - result.pnl_mean) ** 2 for x in all_final_pnls)
            / (len(all_final_pnls) - 1)
        )
        result.pnl_p5 = self._percentile(all_final_pnls, 5)
        result.pnl_p10 = self._percentile(all_final_pnls, 10)
        result.pnl_p25 = self._percentile(all_final_pnls, 25)
        result.pnl_p50 = self._percentile(all_final_pnls, 50)
        result.pnl_median = result.pnl_p50
        result.pnl_p75 = self._percentile(all_final_pnls, 75)
        result.pnl_p90 = self._percentile(all_final_pnls, 90)
        result.pnl_p95 = self._percentile(all_final_pnls, 95)

        # Win rate distribution
        all_win_rates.sort()
        result.all_win_rates = all_win_rates
        result.wr_mean = sum(all_win_rates) / len(all_win_rates)
        result.wr_p5 = self._percentile(all_win_rates, 5)
        result.wr_p50 = self._percentile(all_win_rates, 50)
        result.wr_p95 = self._percentile(all_win_rates, 95)

        # Drawdown distribution
        all_max_drawdowns.sort()
        result.all_max_drawdowns = all_max_drawdowns
        result.dd_mean = sum(all_max_drawdowns) / len(all_max_drawdowns)
        result.dd_median = self._percentile(all_max_drawdowns, 50)
        result.dd_p5 = self._percentile(all_max_drawdowns, 5)
        result.dd_p50 = self._percentile(all_max_drawdowns, 50)
        result.dd_p95 = self._percentile(all_max_drawdowns, 95)
        result.dd_max = max(all_max_drawdowns)

        # Risk of ruin
        result.prob_ruin_25 = sum(1 for d in all_max_drawdowns if d > 25) / simulations
        result.prob_ruin_50 = sum(1 for d in all_max_drawdowns if d > 50) / simulations
        result.prob_ruin_100 = sum(1 for d in all_max_drawdowns if d > 100) / simulations
        result.prob_negative = sum(1 for p in all_final_pnls if p < 0) / simulations

        # Profit factor distribution
        if all_profit_factors:
            all_profit_factors.sort()
            result.pf_mean = sum(all_profit_factors) / len(all_profit_factors)
            result.pf_p5 = self._percentile(all_profit_factors, 5)
            result.pf_p50 = self._percentile(all_profit_factors, 50)
            result.pf_p95 = self._percentile(all_profit_factors, 95)

        # Expectancy
        all_expectancies.sort()
        result.expectancy_mean = sum(all_expectancies) / len(all_expectancies)
        result.expectancy_p5 = self._percentile(all_expectancies, 5)
        result.expectancy_p95 = self._percentile(all_expectancies, 95)

        # Sample curves
        result.sample_equity_curves = sample_curves

        return result

    @staticmethod
    def _percentile(sorted_list: List[float], pct: float) -> float:
        """Calcula percentil de una lista ya ordenada."""
        n = len(sorted_list)
        idx = (pct / 100) * (n - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return sorted_list[lo]
        frac = idx - lo
        return sorted_list[lo] * (1 - frac) + sorted_list[hi] * frac


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═══════════════════════════════════════════════════════════════════════════

def print_montecarlo_report(r: MonteCarloResult):
    """Imprime resultados en consola."""
    W = 64
    print("\n" + "=" * W)
    print("  MONTE CARLO RESULTS")
    print("=" * W)
    print(f"  Simulaciones:    {r.num_simulations:,}")
    print(f"  Trades/sim:      {r.trades_per_sim}")
    print(f"  Trades fuente:   {r.source_trades}")
    print(f"  WR fuente:       {r.source_win_rate*100:.1f}%")
    print(f"  Avg Win/Loss:    {r.source_avg_win:+.2f} / {r.source_avg_loss:.2f}")
    print("-" * W)

    print(f"\n  {'--- PnL DISTRIBUTION (puntos) ---':^{W}}")
    print(f"  P5  (peor caso):     {r.pnl_p5:+8.2f}")
    print(f"  P10:                 {r.pnl_p10:+8.2f}")
    print(f"  P25:                 {r.pnl_p25:+8.2f}")
    print(f"  P50 (mediana):       {r.pnl_p50:+8.2f}")
    print(f"  P75:                 {r.pnl_p75:+8.2f}")
    print(f"  P90:                 {r.pnl_p90:+8.2f}")
    print(f"  P95 (mejor caso):    {r.pnl_p95:+8.2f}")
    print(f"  Media +/- Std:       {r.pnl_mean:+.2f} +/- {r.pnl_std:.2f}")

    print(f"\n  {'--- WIN RATE DISTRIBUTION ---':^{W}}")
    print(f"  P5:    {r.wr_p5*100:5.1f}%")
    print(f"  P50:   {r.wr_p50*100:5.1f}%")
    print(f"  P95:   {r.wr_p95*100:5.1f}%")
    print(f"  Media: {r.wr_mean*100:5.1f}%")

    print(f"\n  {'--- MAX DRAWDOWN DISTRIBUTION ---':^{W}}")
    print(f"  P5  (mejor):         {r.dd_p5:8.2f}")
    print(f"  P50 (mediana):       {r.dd_p50:8.2f}")
    print(f"  P95 (severo):        {r.dd_p95:8.2f}")
    print(f"  Max observado:       {r.dd_max:8.2f}")

    print(f"\n  {'--- RISK OF RUIN ---':^{W}}")
    print(f"  P(drawdown > 25 pts):    {r.prob_ruin_25*100:5.1f}%")
    print(f"  P(drawdown > 50 pts):    {r.prob_ruin_50*100:5.1f}%")
    print(f"  P(drawdown > 100 pts):   {r.prob_ruin_100*100:5.1f}%")
    print(f"  P(PnL negativo):         {r.prob_negative*100:5.1f}%")

    print(f"\n  {'--- PROFIT FACTOR ---':^{W}}")
    print(f"  P5:    {r.pf_p5:.2f}")
    print(f"  P50:   {r.pf_p50:.2f}")
    print(f"  P95:   {r.pf_p95:.2f}")
    print(f"  Media: {r.pf_mean:.2f}")

    print(f"\n  {'--- EXPECTANCY (pts/trade) ---':^{W}}")
    print(f"  P5:    {r.expectancy_p5:+.3f}")
    print(f"  Media: {r.expectancy_mean:+.3f}")
    print(f"  P95:   {r.expectancy_p95:+.3f}")

    print("\n" + "=" * W)

    # Interpretar resultados
    print("\n  INTERPRETACION:")
    if r.prob_negative < 0.10:
        print(f"  [+] Excelente: solo {r.prob_negative*100:.1f}% probabilidad de perder dinero")
    elif r.prob_negative < 0.25:
        print(f"  [~] Aceptable: {r.prob_negative*100:.1f}% probabilidad de perder dinero")
    else:
        print(f"  [-] Riesgoso: {r.prob_negative*100:.1f}% probabilidad de perder dinero")

    if r.pf_p50 > 1.5:
        print(f"  [+] Profit factor mediano {r.pf_p50:.2f} — estrategia robusta")
    elif r.pf_p50 > 1.0:
        print(f"  [~] Profit factor mediano {r.pf_p50:.2f} — estrategia marginal")
    else:
        print(f"  [-] Profit factor mediano {r.pf_p50:.2f} — estrategia perdedora")

    if r.dd_p95 < 50:
        print(f"  [+] Drawdown P95 de {r.dd_p95:.1f} pts — riesgo controlado")
    elif r.dd_p95 < 100:
        print(f"  [~] Drawdown P95 de {r.dd_p95:.1f} pts — riesgo moderado")
    else:
        print(f"  [-] Drawdown P95 de {r.dd_p95:.1f} pts — riesgo alto")

    print()


# ═══════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ═══════════════════════════════════════════════════════════════════════════

def generate_montecarlo_html(r: MonteCarloResult, output_dir: str = None) -> str:
    """Genera reporte HTML con visualizaciones SVG."""
    if output_dir is None:
        output_dir = _REPORTS_DIR
    os.makedirs(output_dir, exist_ok=True)

    filepath = os.path.join(output_dir, f"montecarlo_{r.num_simulations}sims.html")

    # ── Histograma PnL (SVG) ───────────────────────────────────────────
    pnl_hist_svg = _build_histogram_svg(
        data=r.all_final_pnls,
        width=700, height=250,
        title="PnL Distribution",
        xlabel="PnL (puntos)",
        bins=50,
        highlight_percentiles={5: r.pnl_p5, 50: r.pnl_p50, 95: r.pnl_p95},
    )

    # ── Histograma Drawdown (SVG) ──────────────────────────────────────
    dd_hist_svg = _build_histogram_svg(
        data=r.all_max_drawdowns,
        width=700, height=250,
        title="Max Drawdown Distribution",
        xlabel="Max Drawdown (puntos)",
        bins=40,
        color="#f44336",
        highlight_percentiles={50: r.dd_p50, 95: r.dd_p95},
    )

    # ── Equity Curves (SVG) ────────────────────────────────────────────
    eq_svg = _build_equity_curves_svg(
        curves=r.sample_equity_curves[:50],
        width=700, height=300,
    )

    # ── Build HTML ─────────────────────────────────────────────────────
    pnl_color = "#4CAF50" if r.pnl_median >= 0 else "#f44336"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Monte Carlo Simulation — Price Action Scanner</title>
<style>
  :root {{ --green: #4CAF50; --red: #f44336; --orange: #FF9800; --blue: #2196F3; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
  h1 {{ color: #58a6ff; margin-bottom: 5px; }}
  h2 {{ color: #8b949e; margin: 30px 0 15px; border-bottom: 1px solid #21262d; padding-bottom: 8px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 25px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 20px 0; }}
  .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; }}
  .card .label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .value {{ font-size: 26px; font-weight: 700; margin-top: 4px; }}
  .card .detail {{ font-size: 12px; color: #484f58; margin-top: 2px; }}
  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}
  .neutral {{ color: var(--orange); }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 25px; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 10px 12px; font-size: 12px;
       text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #21262d; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  tr:hover td {{ background: #161b22; }}
  .chart-container {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 20px; margin: 20px 0; }}
  svg {{ width: 100%; height: auto; }}
  .risk-meter {{ display: flex; gap: 4px; margin: 10px 0; }}
  .risk-block {{ width: 20px; height: 40px; border-radius: 3px; }}
  .risk-block.green {{ background: var(--green); }}
  .risk-block.orange {{ background: var(--orange); }}
  .risk-block.red {{ background: var(--red); }}
  .risk-block.off {{ background: #21262d; }}
  .interpretation {{ background: #161b22; border-left: 3px solid var(--blue); padding: 15px 20px; margin: 20px 0; border-radius: 0 8px 8px 0; }}
  .interpretation p {{ margin: 6px 0; font-size: 14px; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #21262d; font-size: 12px; color: #484f58; }}
</style>
</head>
<body>

<h1>Monte Carlo Simulation</h1>
<div class="subtitle">
  Price Action Scanner (Eduardo / PRN-Million plus) &mdash;
  {r.num_simulations:,} simulaciones x {r.trades_per_sim} trades &mdash;
  Basado en {r.source_trades} trades reales
</div>

<!-- ─── KPI CARDS ─────────────────────────────────────────────────── -->
<div class="grid">
  <div class="card">
    <div class="label">PnL Mediano (P50)</div>
    <div class="value" style="color: {pnl_color}">{r.pnl_p50:+.1f} pts</div>
    <div class="detail">Rango P5-P95: {r.pnl_p5:+.1f} a {r.pnl_p95:+.1f}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate Mediano</div>
    <div class="value">{r.wr_p50*100:.1f}%</div>
    <div class="detail">Rango P5-P95: {r.wr_p5*100:.1f}% a {r.wr_p95*100:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor (P50)</div>
    <div class="value {'positive' if r.pf_p50 > 1 else 'negative'}">{r.pf_p50:.2f}</div>
    <div class="detail">Rango P5-P95: {r.pf_p5:.2f} a {r.pf_p95:.2f}</div>
  </div>
  <div class="card">
    <div class="label">P(Perder Dinero)</div>
    <div class="value {'positive' if r.prob_negative < 0.15 else 'negative'}">{r.prob_negative*100:.1f}%</div>
    <div class="detail">En {r.trades_per_sim} trades</div>
  </div>
  <div class="card">
    <div class="label">Drawdown Mediano</div>
    <div class="value negative">{r.dd_p50:.1f} pts</div>
    <div class="detail">P95 (severo): {r.dd_p95:.1f} pts</div>
  </div>
  <div class="card">
    <div class="label">Expectancy</div>
    <div class="value {'positive' if r.expectancy_mean > 0 else 'negative'}">{r.expectancy_mean:+.2f}</div>
    <div class="detail">pts/trade promedio</div>
  </div>
</div>

<!-- ─── INTERPRETATION ────────────────────────────────────────────── -->
<div class="interpretation">
  <p><strong>Interpretacion de resultados:</strong></p>
  <p>{'&#9989; ' if r.prob_negative < 0.10 else '&#9888;&#65039; ' if r.prob_negative < 0.25 else '&#10060; '}
     Probabilidad de perder dinero despues de {r.trades_per_sim} trades: <strong>{r.prob_negative*100:.1f}%</strong></p>
  <p>{'&#9989; ' if r.pf_p50 > 1.2 else '&#9888;&#65039; '}
     En el 50% de los escenarios, el profit factor es <strong>{r.pf_p50:.2f}</strong> o mejor</p>
  <p>{'&#9989; ' if r.dd_p95 < 60 else '&#9888;&#65039; '}
     En el 95% de los escenarios, el max drawdown no supera <strong>{r.dd_p95:.1f} pts</strong></p>
  <p>{'&#9989; ' if r.pnl_p25 > 0 else '&#9888;&#65039; '}
     En el 75% de escenarios, el PnL es mayor a <strong>{r.pnl_p25:+.1f} pts</strong></p>
  <p>&#128176; Rango esperado de PnL (90% confianza): <strong>{r.pnl_p5:+.1f} a {r.pnl_p95:+.1f} pts</strong></p>
</div>

<!-- ─── PnL PERCENTILE TABLE ──────────────────────────────────────── -->
<h2>PnL Distribution (puntos, {r.trades_per_sim} trades)</h2>
<table>
  <tr>
    <th>Percentil</th><th>PnL</th><th>Significado</th>
  </tr>
  <tr><td>P5</td><td class="{'positive' if r.pnl_p5 > 0 else 'negative'}">{r.pnl_p5:+.2f}</td><td>Peor caso realista (1 de 20 veces)</td></tr>
  <tr><td>P10</td><td class="{'positive' if r.pnl_p10 > 0 else 'negative'}">{r.pnl_p10:+.2f}</td><td>Escenario pesimista</td></tr>
  <tr><td>P25</td><td class="{'positive' if r.pnl_p25 > 0 else 'negative'}">{r.pnl_p25:+.2f}</td><td>Cuartil inferior</td></tr>
  <tr style="background:#161b22"><td><strong>P50</strong></td><td style="color:{pnl_color}"><strong>{r.pnl_p50:+.2f}</strong></td><td><strong>Resultado mas probable (mediana)</strong></td></tr>
  <tr><td>P75</td><td class="positive">{r.pnl_p75:+.2f}</td><td>Cuartil superior</td></tr>
  <tr><td>P90</td><td class="positive">{r.pnl_p90:+.2f}</td><td>Escenario optimista</td></tr>
  <tr><td>P95</td><td class="positive">{r.pnl_p95:+.2f}</td><td>Mejor caso realista (1 de 20 veces)</td></tr>
</table>

<!-- ─── RISK OF RUIN TABLE ────────────────────────────────────────── -->
<h2>Risk of Ruin</h2>
<table>
  <tr><th>Escenario</th><th>Probabilidad</th><th>Nivel</th></tr>
  <tr>
    <td>Drawdown > 25 pts</td>
    <td>{r.prob_ruin_25*100:.1f}%</td>
    <td>{'&#128994;' if r.prob_ruin_25 < 0.30 else '&#128992;' if r.prob_ruin_25 < 0.60 else '&#128308;'} {'Bajo' if r.prob_ruin_25 < 0.30 else 'Moderado' if r.prob_ruin_25 < 0.60 else 'Alto'}</td>
  </tr>
  <tr>
    <td>Drawdown > 50 pts</td>
    <td>{r.prob_ruin_50*100:.1f}%</td>
    <td>{'&#128994;' if r.prob_ruin_50 < 0.15 else '&#128992;' if r.prob_ruin_50 < 0.40 else '&#128308;'} {'Bajo' if r.prob_ruin_50 < 0.15 else 'Moderado' if r.prob_ruin_50 < 0.40 else 'Alto'}</td>
  </tr>
  <tr>
    <td>Drawdown > 100 pts</td>
    <td>{r.prob_ruin_100*100:.1f}%</td>
    <td>{'&#128994;' if r.prob_ruin_100 < 0.05 else '&#128992;' if r.prob_ruin_100 < 0.20 else '&#128308;'} {'Bajo' if r.prob_ruin_100 < 0.05 else 'Moderado' if r.prob_ruin_100 < 0.20 else 'Alto'}</td>
  </tr>
  <tr>
    <td>PnL negativo total</td>
    <td>{r.prob_negative*100:.1f}%</td>
    <td>{'&#128994;' if r.prob_negative < 0.10 else '&#128992;' if r.prob_negative < 0.25 else '&#128308;'} {'Bajo' if r.prob_negative < 0.10 else 'Moderado' if r.prob_negative < 0.25 else 'Alto'}</td>
  </tr>
</table>

<!-- ─── CHARTS ────────────────────────────────────────────────────── -->
<h2>PnL Distribution</h2>
<div class="chart-container">
  {pnl_hist_svg}
</div>

<h2>Max Drawdown Distribution</h2>
<div class="chart-container">
  {dd_hist_svg}
</div>

<h2>Equity Curves (50 simulaciones de muestra)</h2>
<div class="chart-container">
  {eq_svg}
</div>

<!-- ─── SOURCE DATA ───────────────────────────────────────────────── -->
<h2>Datos Fuente (Backtest)</h2>
<table>
  <tr><td style="color:#8b949e">Trades analizados</td><td>{r.source_trades}</td></tr>
  <tr><td style="color:#8b949e">Win Rate real</td><td>{r.source_win_rate*100:.1f}%</td></tr>
  <tr><td style="color:#8b949e">Avg Win</td><td class="positive">{r.source_avg_win:+.2f} pts</td></tr>
  <tr><td style="color:#8b949e">Avg Loss</td><td class="negative">{r.source_avg_loss:.2f} pts</td></tr>
  <tr><td style="color:#8b949e">PnL Total</td><td>{r.source_total_pnl:+.2f} pts</td></tr>
</table>

<div class="footer">
  <p>Generado: {datetime.now().isoformat()}</p>
  <p>Monte Carlo Simulation &mdash; Price Action Trading System &mdash; Eduardo (PRN-Million plus)</p>
  <p>Metodo: Bootstrap con reemplazo. {r.num_simulations:,} simulaciones x {r.trades_per_sim} trades por simulacion.</p>
  <p><em>Disclaimer: Resultados pasados no garantizan rendimiento futuro. Esta simulacion asume que la distribucion
  de trades futuros sera similar a la observada. El mercado puede cambiar.</em></p>
</div>

</body>
</html>"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    return filepath


# ═══════════════════════════════════════════════════════════════════════════
# SVG CHART HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _build_histogram_svg(
    data: List[float],
    width: int = 700,
    height: int = 250,
    title: str = "",
    xlabel: str = "",
    bins: int = 50,
    color: str = "#58a6ff",
    highlight_percentiles: Optional[Dict[int, float]] = None,
) -> str:
    """Construye un histograma SVG inline."""
    if not data:
        return "<p>No data</p>"

    d_min = min(data)
    d_max = max(data)
    d_range = d_max - d_min if d_max != d_min else 1

    # Compute bins
    bin_width = d_range / bins
    counts = [0] * bins
    for val in data:
        idx = min(int((val - d_min) / bin_width), bins - 1)
        counts[idx] += 1

    max_count = max(counts) if counts else 1
    margin_left = 50
    margin_bottom = 40
    margin_top = 30
    chart_w = width - margin_left - 10
    chart_h = height - margin_bottom - margin_top
    bar_w = chart_w / bins

    svg = f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">\n'

    # Title
    svg += f'  <text x="{width//2}" y="18" text-anchor="middle" fill="#c9d1d9" font-size="14">{title}</text>\n'

    # Bars
    for i, count in enumerate(counts):
        bar_h = (count / max_count) * chart_h if max_count > 0 else 0
        x = margin_left + i * bar_w
        y = margin_top + chart_h - bar_h

        # Color: green if bin center > 0, red if < 0
        bin_center = d_min + (i + 0.5) * bin_width
        bar_color = "#4CAF50" if bin_center >= 0 else "#f44336"

        svg += f'  <rect x="{x:.1f}" y="{y:.1f}" width="{max(bar_w - 1, 1):.1f}" height="{bar_h:.1f}" fill="{bar_color}" opacity="0.7"/>\n'

    # Zero line
    if d_min < 0 < d_max:
        zero_x = margin_left + (0 - d_min) / d_range * chart_w
        svg += f'  <line x1="{zero_x:.1f}" y1="{margin_top}" x2="{zero_x:.1f}" y2="{margin_top + chart_h}" stroke="#c9d1d9" stroke-width="1" stroke-dasharray="4"/>\n'
        svg += f'  <text x="{zero_x:.1f}" y="{margin_top + chart_h + 15}" text-anchor="middle" fill="#c9d1d9" font-size="10">0</text>\n'

    # Percentile markers
    if highlight_percentiles:
        for pct, val in highlight_percentiles.items():
            px = margin_left + (val - d_min) / d_range * chart_w
            svg += f'  <line x1="{px:.1f}" y1="{margin_top}" x2="{px:.1f}" y2="{margin_top + chart_h}" stroke="#FF9800" stroke-width="1.5" stroke-dasharray="3"/>\n'
            svg += f'  <text x="{px:.1f}" y="{margin_top - 5}" text-anchor="middle" fill="#FF9800" font-size="10">P{pct}: {val:+.1f}</text>\n'

    # X-axis labels
    for i in range(0, bins + 1, bins // 5):
        val = d_min + i * bin_width
        x = margin_left + i * bar_w
        svg += f'  <text x="{x:.1f}" y="{margin_top + chart_h + 15}" text-anchor="middle" fill="#484f58" font-size="9">{val:.0f}</text>\n'

    # X-axis label
    svg += f'  <text x="{width//2}" y="{height - 5}" text-anchor="middle" fill="#484f58" font-size="10">{xlabel}</text>\n'

    svg += '</svg>'
    return svg


def _build_equity_curves_svg(
    curves: List[List[float]],
    width: int = 700,
    height: int = 300,
) -> str:
    """Construye equity curves SVG con múltiples simulaciones."""
    if not curves:
        return "<p>No curves</p>"

    # Find global min/max
    all_vals = [v for curve in curves for v in curve]
    v_min = min(all_vals)
    v_max = max(all_vals)
    v_range = v_max - v_min if v_max != v_min else 1

    max_len = max(len(c) for c in curves)

    margin_left = 50
    margin_top = 20
    margin_bottom = 30
    chart_w = width - margin_left - 10
    chart_h = height - margin_top - margin_bottom

    svg = f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">\n'

    # Zero line
    zero_y = margin_top + chart_h - ((0 - v_min) / v_range * chart_h)
    svg += f'  <line x1="{margin_left}" y1="{zero_y:.1f}" x2="{width - 10}" y2="{zero_y:.1f}" stroke="#21262d" stroke-width="1"/>\n'
    svg += f'  <text x="{margin_left - 5}" y="{zero_y:.1f}" text-anchor="end" fill="#484f58" font-size="10" dominant-baseline="middle">0</text>\n'

    # Draw curves
    colors = ["#58a6ff", "#4CAF50", "#f44336", "#FF9800", "#9C27B0", "#00BCD4"]
    for ci, curve in enumerate(curves):
        if len(curve) < 2:
            continue
        points = []
        for j, val in enumerate(curve):
            x = margin_left + (j / (max_len - 1)) * chart_w
            y = margin_top + chart_h - ((val - v_min) / v_range * chart_h)
            points.append(f"{x:.1f},{y:.1f}")

        color = colors[ci % len(colors)]
        final_val = curve[-1]
        opacity = "0.15" if ci > 5 else "0.4"
        stroke_w = "0.8" if ci > 5 else "1.2"

        svg += f'  <polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="{stroke_w}" opacity="{opacity}"/>\n'

    # Y-axis labels
    for i in range(5):
        val = v_min + (v_range * i / 4)
        y = margin_top + chart_h - (i / 4 * chart_h)
        svg += f'  <text x="{margin_left - 5}" y="{y:.1f}" text-anchor="end" fill="#484f58" font-size="9" dominant-baseline="middle">{val:.0f}</text>\n'
        svg += f'  <line x1="{margin_left}" y1="{y:.1f}" x2="{width - 10}" y2="{y:.1f}" stroke="#21262d" stroke-width="0.5" stroke-dasharray="2"/>\n'

    # X label
    svg += f'  <text x="{width//2}" y="{height - 5}" text-anchor="middle" fill="#484f58" font-size="10">Trades</text>\n'

    svg += '</svg>'
    return svg


# ═══════════════════════════════════════════════════════════════════════════
# MAIN — Ejecuta backtest + Monte Carlo en secuencia
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Ejecuta backtest y luego Monte Carlo."""
    import argparse

    parser = argparse.ArgumentParser(description="Monte Carlo Simulation para Price Action Scanner")
    parser.add_argument("--days", type=int, default=30, help="Dias de backtest (default 30)")
    parser.add_argument("--start", default=None, help="Fecha inicio YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Fecha fin YYYY-MM-DD")
    parser.add_argument("--sims", type=int, default=10000, help="Numero de simulaciones MC (default 10000)")
    parser.add_argument("--trades", type=int, default=100, help="Trades por simulacion (default 100)")
    parser.add_argument("--symbol", default="^GSPC", help="Ticker (default ^GSPC)")
    parser.add_argument("--seed", type=int, default=None, help="Seed para reproducibilidad")
    parser.add_argument("--no-html", action="store_true", help="No generar HTML")

    args = parser.parse_args()

    # ── Paso 1: Correr backtest ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  PASO 1: BACKTESTING")
    print("=" * 60)

    # Importar backtester
    from pa_backtester import (
        HistoricalDataProvider,
        PriceActionBacktester,
        print_console_report,
    )

    provider = HistoricalDataProvider(symbol=args.symbol)
    df_1h, df_5m, df_2m = provider.fetch(start=args.start, end=args.end, days=args.days)

    if df_2m.empty:
        print("No se obtuvieron datos. Abortando.")
        sys.exit(1)

    backtester = PriceActionBacktester()
    bt_result = backtester.run(df_1h, df_5m, df_2m)
    print_console_report(bt_result)

    if not bt_result.trades:
        print("Sin trades en el backtest. No se puede correr Monte Carlo.")
        sys.exit(1)

    # ── Paso 2: Extraer PnLs y correr Monte Carlo ─────────────────────
    trade_pnls = [t.pnl_points for t in bt_result.trades]

    print("\n" + "=" * 60)
    print("  PASO 2: MONTE CARLO SIMULATION")
    print("=" * 60)

    mc = MonteCarloSimulator(trade_pnls=trade_pnls)
    mc_result = mc.run(
        simulations=args.sims,
        trades_per_sim=args.trades,
        seed=args.seed,
    )

    print_montecarlo_report(mc_result)

    # ── Paso 3: Generar HTML ──────────────────────────────────────────
    if not args.no_html:
        html_path = generate_montecarlo_html(mc_result)
        print(f"\n📊 Reporte Monte Carlo HTML: {html_path}")
        print("   Abre en el navegador para ver graficos y distribuciones.\n")


if __name__ == "__main__":
    main()
