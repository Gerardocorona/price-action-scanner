"""
pa_montecarlo_compound.py — Monte Carlo con Capital Compuesto
=============================================================
Simula crecimiento de capital reinvirtiendo ganancias.

Modelo:
  - Capital inicial: $X
  - Cada trade arriesga Y% del capital actual
  - PnL se convierte a retorno sobre riesgo (PnL_pts / SL_distance)
  - Si gana: capital += risk_amount * (PnL / SL)
  - Si pierde: capital -= risk_amount (pierde lo arriesgado)

Uso:
    python pa_montecarlo_compound.py --capital 5000 --risk-pct 50 --trades 60
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


@dataclass
class CompoundResult:
    """Resultado de simulación con capital compuesto."""
    # Config
    initial_capital: float = 0.0
    risk_pct: float = 0.0
    trades_per_sim: int = 0
    num_simulations: int = 0
    source_trades: int = 0
    sl_distance: float = 12.0

    # Capital final distribution
    capital_mean: float = 0.0
    capital_median: float = 0.0
    capital_p5: float = 0.0
    capital_p10: float = 0.0
    capital_p25: float = 0.0
    capital_p50: float = 0.0
    capital_p75: float = 0.0
    capital_p90: float = 0.0
    capital_p95: float = 0.0
    capital_max: float = 0.0
    capital_min: float = 0.0

    # ROI distribution
    roi_mean: float = 0.0
    roi_median: float = 0.0
    roi_p5: float = 0.0
    roi_p25: float = 0.0
    roi_p50: float = 0.0
    roi_p75: float = 0.0
    roi_p95: float = 0.0

    # Risk
    prob_double: float = 0.0       # P(capital >= 2x initial)
    prob_triple: float = 0.0       # P(capital >= 3x)
    prob_5x: float = 0.0           # P(capital >= 5x)
    prob_10x: float = 0.0          # P(capital >= 10x)
    prob_ruin: float = 0.0         # P(capital < 500, basically wiped)
    prob_loss: float = 0.0         # P(capital < initial)
    prob_half: float = 0.0         # P(capital < 50% of initial)
    max_drawdown_pct_median: float = 0.0
    max_drawdown_pct_p95: float = 0.0

    # Growth milestones (median trade # to reach)
    trades_to_double_median: float = 0.0
    trades_to_triple_median: float = 0.0

    # Raw data
    all_final_capitals: List[float] = field(default_factory=list)
    sample_curves: List[List[float]] = field(default_factory=list)
    all_max_dd_pct: List[float] = field(default_factory=list)


class CompoundMonteCarloSimulator:
    """
    Monte Carlo con reinversión de ganancias.

    Cada trade del backtest tiene un PnL en puntos SPX.
    Convertimos a retorno sobre riesgo:
      return_on_risk = PnL_pts / SL_distance

    En cada trade simulado:
      risk_amount = capital * risk_pct
      pnl_dollar = risk_amount * return_on_risk
      capital += pnl_dollar

    Esto modela opciones 0DTE donde:
      - Compras con X% de tu capital
      - Si SPX se mueve a favor, ganas proporcionalmente
      - Si toca SL, pierdes lo arriesgado
    """

    def __init__(self, trade_pnls: List[float], sl_distance: float = 12.0):
        self.trade_pnls = trade_pnls
        self.sl_distance = sl_distance
        self.n_source = len(trade_pnls)

        # Convertir PnLs a retornos sobre riesgo
        self.returns_on_risk = [pnl / sl_distance for pnl in trade_pnls]

        # Stats
        wins = [r for r in self.returns_on_risk if r > 0]
        losses = [r for r in self.returns_on_risk if r < 0]
        self.source_wr = len(wins) / len(self.returns_on_risk)
        self.source_avg_win_ror = sum(wins) / len(wins) if wins else 0
        self.source_avg_loss_ror = sum(losses) / len(losses) if losses else 0

    def run(
        self,
        initial_capital: float = 5000.0,
        risk_pct: float = 0.50,
        trades_per_sim: int = 60,
        simulations: int = 10000,
        seed: Optional[int] = None,
    ) -> CompoundResult:

        if seed is not None:
            random.seed(seed)

        print(f"\n{'='*64}")
        print(f"  MONTE CARLO — CAPITAL COMPUESTO")
        print(f"  Capital inicial:  ${initial_capital:,.0f}")
        print(f"  Riesgo por trade: {risk_pct*100:.0f}% del capital")
        print(f"  Trades/sim:       {trades_per_sim}")
        print(f"  Simulaciones:     {simulations:,}")
        print(f"  SL distance:      {self.sl_distance} pts")
        print(f"  Trades fuente:    {self.n_source}")
        print(f"{'='*64}\n")

        all_final_capitals = []
        all_max_dd_pct = []
        sample_curves = []
        trades_to_double = []
        trades_to_triple = []

        save_curve_every = max(simulations // 100, 1)

        for s in range(simulations):
            capital = initial_capital
            peak_capital = capital
            max_dd_pct = 0.0
            curve = [capital]
            hit_double = None
            hit_triple = None

            # Muestrear trades
            sampled_rors = random.choices(self.returns_on_risk, k=trades_per_sim)

            for t_idx, ror in enumerate(sampled_rors):
                # Calcular riesgo
                risk_amount = capital * risk_pct

                # PnL en dolares
                pnl_dollar = risk_amount * ror

                # Limitar perdida al riesgo (no puedes perder mas de lo arriesgado)
                if pnl_dollar < -risk_amount:
                    pnl_dollar = -risk_amount

                capital += pnl_dollar

                # Floor: capital no puede ser negativo
                if capital < 0:
                    capital = 0

                curve.append(capital)

                # Track peak y drawdown
                if capital > peak_capital:
                    peak_capital = capital
                if peak_capital > 0:
                    dd_pct = (peak_capital - capital) / peak_capital * 100
                    if dd_pct > max_dd_pct:
                        max_dd_pct = dd_pct

                # Milestones
                if hit_double is None and capital >= initial_capital * 2:
                    hit_double = t_idx + 1
                if hit_triple is None and capital >= initial_capital * 3:
                    hit_triple = t_idx + 1

            all_final_capitals.append(capital)
            all_max_dd_pct.append(max_dd_pct)

            if hit_double is not None:
                trades_to_double.append(hit_double)
            if hit_triple is not None:
                trades_to_triple.append(hit_triple)

            if s % save_curve_every == 0:
                sample_curves.append(curve)

            if s % (simulations // 10) == 0 and s > 0:
                pct = s / simulations * 100
                print(f"  [{pct:5.1f}%] {s:,}/{simulations:,}...")

        print(f"  [100.0%] {simulations:,}/{simulations:,} completadas\n")

        # ── Compilar resultados ───────────────────────────────────────
        result = CompoundResult(
            initial_capital=initial_capital,
            risk_pct=risk_pct,
            trades_per_sim=trades_per_sim,
            num_simulations=simulations,
            source_trades=self.n_source,
            sl_distance=self.sl_distance,
        )

        all_final_capitals.sort()
        result.all_final_capitals = all_final_capitals
        result.capital_mean = sum(all_final_capitals) / len(all_final_capitals)
        result.capital_median = _percentile(all_final_capitals, 50)
        result.capital_p5 = _percentile(all_final_capitals, 5)
        result.capital_p10 = _percentile(all_final_capitals, 10)
        result.capital_p25 = _percentile(all_final_capitals, 25)
        result.capital_p50 = _percentile(all_final_capitals, 50)
        result.capital_p75 = _percentile(all_final_capitals, 75)
        result.capital_p90 = _percentile(all_final_capitals, 90)
        result.capital_p95 = _percentile(all_final_capitals, 95)
        result.capital_max = max(all_final_capitals)
        result.capital_min = min(all_final_capitals)

        # ROI
        rois = [(c - initial_capital) / initial_capital * 100 for c in all_final_capitals]
        result.roi_mean = sum(rois) / len(rois)
        result.roi_median = _percentile(rois, 50)
        result.roi_p5 = _percentile(rois, 5)
        result.roi_p25 = _percentile(rois, 25)
        result.roi_p50 = _percentile(rois, 50)
        result.roi_p75 = _percentile(rois, 75)
        result.roi_p95 = _percentile(rois, 95)

        # Probabilities
        n = simulations
        result.prob_double = sum(1 for c in all_final_capitals if c >= initial_capital * 2) / n
        result.prob_triple = sum(1 for c in all_final_capitals if c >= initial_capital * 3) / n
        result.prob_5x = sum(1 for c in all_final_capitals if c >= initial_capital * 5) / n
        result.prob_10x = sum(1 for c in all_final_capitals if c >= initial_capital * 10) / n
        result.prob_ruin = sum(1 for c in all_final_capitals if c < 500) / n
        result.prob_loss = sum(1 for c in all_final_capitals if c < initial_capital) / n
        result.prob_half = sum(1 for c in all_final_capitals if c < initial_capital * 0.5) / n

        # Drawdown
        all_max_dd_pct.sort()
        result.all_max_dd_pct = all_max_dd_pct
        result.max_drawdown_pct_median = _percentile(all_max_dd_pct, 50)
        result.max_drawdown_pct_p95 = _percentile(all_max_dd_pct, 95)

        # Milestones
        if trades_to_double:
            trades_to_double.sort()
            result.trades_to_double_median = _percentile(trades_to_double, 50)
        if trades_to_triple:
            trades_to_triple.sort()
            result.trades_to_triple_median = _percentile(trades_to_triple, 50)

        result.sample_curves = sample_curves

        return result


def _percentile(sorted_list: List[float], pct: float) -> float:
    n = len(sorted_list)
    if n == 0:
        return 0.0
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

def print_compound_report(r: CompoundResult):
    W = 64
    ic = r.initial_capital

    print("\n" + "=" * W)
    print("  MONTE CARLO — CAPITAL COMPUESTO — RESULTADOS")
    print("=" * W)
    print(f"  Capital inicial:    ${ic:>12,.2f}")
    print(f"  Riesgo por trade:   {r.risk_pct*100:.0f}%")
    print(f"  Trades simulados:   {r.trades_per_sim}")
    print(f"  Simulaciones:       {r.num_simulations:,}")
    print("-" * W)

    print(f"\n  {'--- CAPITAL FINAL ---':^{W}}")
    print(f"  {'Percentil':<22} {'Capital':>12}  {'ROI':>10}  {'Multiplo':>8}")
    print(f"  {'-'*56}")
    for label, cap, roi in [
        ("P5  (peor caso)", r.capital_p5, r.roi_p5),
        ("P10", r.capital_p10, (r.capital_p10 - ic) / ic * 100),
        ("P25", r.capital_p25, r.roi_p25),
        ("P50 (mediana)", r.capital_p50, r.roi_p50),
        ("P75", r.capital_p75, r.roi_p75),
        ("P90", r.capital_p90, (r.capital_p90 - ic) / ic * 100),
        ("P95 (mejor caso)", r.capital_p95, r.roi_p95),
    ]:
        mult = cap / ic if ic > 0 else 0
        print(f"  {label:<22} ${cap:>11,.2f}  {roi:>+9.1f}%  {mult:>7.1f}x")

    print(f"\n  Media:               ${r.capital_mean:>11,.2f}  {r.roi_mean:>+9.1f}%")
    print(f"  Maximo observado:    ${r.capital_max:>11,.2f}  {(r.capital_max/ic):>7.1f}x")
    print(f"  Minimo observado:    ${r.capital_min:>11,.2f}")

    print(f"\n  {'--- PROBABILIDADES DE CRECIMIENTO ---':^{W}}")
    print(f"  P(duplicar ${ic*2:,.0f}):       {r.prob_double*100:>6.1f}%")
    print(f"  P(triplicar ${ic*3:,.0f}):     {r.prob_triple*100:>6.1f}%")
    print(f"  P(5x = ${ic*5:,.0f}):          {r.prob_5x*100:>6.1f}%")
    print(f"  P(10x = ${ic*10:,.0f}):        {r.prob_10x*100:>6.1f}%")

    if r.trades_to_double_median > 0:
        print(f"\n  Trades para duplicar (mediana): {r.trades_to_double_median:.0f}")
    if r.trades_to_triple_median > 0:
        print(f"  Trades para triplicar (mediana): {r.trades_to_triple_median:.0f}")

    print(f"\n  {'--- RIESGO ---':^{W}}")
    print(f"  P(perder dinero):       {r.prob_loss*100:>6.1f}%")
    print(f"  P(perder > 50%):        {r.prob_half*100:>6.1f}%")
    print(f"  P(ruina < $500):        {r.prob_ruin*100:>6.1f}%")
    print(f"  Max DD% mediano:        {r.max_drawdown_pct_median:>6.1f}%")
    print(f"  Max DD% P95 (severo):   {r.max_drawdown_pct_p95:>6.1f}%")

    print("\n" + "=" * W)

    # Interpretacion
    print(f"\n  INTERPRETACION ($5,000 → {r.trades_per_sim} trades, 50% riesgo):\n")

    if r.prob_loss < 0.15:
        print(f"  [+] {r.prob_loss*100:.1f}% chance de perder — a tu favor")
    else:
        print(f"  [!] {r.prob_loss*100:.1f}% chance de perder — cuidado")

    print(f"  [$] Resultado mas probable: ${r.capital_p50:,.0f} ({r.roi_p50:+.0f}% ROI)")
    print(f"  [$] 75% de veces terminas con > ${r.capital_p25:,.0f}")
    print(f"  [$] Mejor 5% de escenarios: > ${r.capital_p95:,.0f}")

    if r.prob_double > 0.5:
        print(f"  [+] {r.prob_double*100:.0f}% chance de duplicar tu dinero")
    if r.prob_triple > 0.2:
        print(f"  [+] {r.prob_triple*100:.0f}% chance de triplicar")
    if r.prob_5x > 0.05:
        print(f"  [+] {r.prob_5x*100:.0f}% chance de 5x")

    print(f"\n  [!] Riesgo 50% por trade es AGRESIVO.")
    print(f"      Max drawdown P95: {r.max_drawdown_pct_p95:.0f}% de tu capital.")
    if r.max_drawdown_pct_p95 > 60:
        print(f"      Considera reducir a 25-30% para menor volatilidad.")

    print()


# ═══════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ═══════════════════════════════════════════════════════════════════════════

def generate_compound_html(r: CompoundResult, output_dir: str = None) -> str:
    if output_dir is None:
        output_dir = _REPORTS_DIR
    os.makedirs(output_dir, exist_ok=True)

    ic = r.initial_capital
    filepath = os.path.join(output_dir, f"montecarlo_compound_{ic:.0f}_{r.risk_pct*100:.0f}pct_{r.trades_per_sim}trades.html")

    # ── Equity curves SVG ──────────────────────────────────────────────
    curves = r.sample_curves[:80]
    eq_svg = ""
    if curves:
        all_vals = [v for c in curves for v in c]
        v_min = max(min(all_vals), 0)
        v_max = max(all_vals)
        v_range = v_max - v_min if v_max != v_min else 1
        max_len = max(len(c) for c in curves)

        w, h = 720, 320
        ml, mt, mb = 70, 25, 30
        cw = w - ml - 10
        ch = h - mt - mb

        eq_svg = f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">\n'

        # Initial capital line
        ic_y = mt + ch - ((ic - v_min) / v_range * ch)
        eq_svg += f'  <line x1="{ml}" y1="{ic_y:.1f}" x2="{w-10}" y2="{ic_y:.1f}" stroke="#484f58" stroke-width="1" stroke-dasharray="6,3"/>\n'
        eq_svg += f'  <text x="{ml-5}" y="{ic_y:.1f}" text-anchor="end" fill="#FF9800" font-size="10" dominant-baseline="middle">${ic:,.0f}</text>\n'

        # Double line
        double_y = mt + ch - ((ic * 2 - v_min) / v_range * ch)
        if 0 < double_y < h:
            eq_svg += f'  <line x1="{ml}" y1="{double_y:.1f}" x2="{w-10}" y2="{double_y:.1f}" stroke="#4CAF50" stroke-width="0.5" stroke-dasharray="3"/>\n'
            eq_svg += f'  <text x="{ml-5}" y="{double_y:.1f}" text-anchor="end" fill="#4CAF50" font-size="9" dominant-baseline="middle">2x</text>\n'

        colors = ["#58a6ff", "#4CAF50", "#f44336", "#FF9800", "#9C27B0", "#00BCD4",
                  "#E91E63", "#8BC34A", "#FF5722", "#607D8B"]
        for ci, curve in enumerate(curves):
            if len(curve) < 2:
                continue
            pts = []
            for j, val in enumerate(curve):
                x = ml + (j / (max_len - 1)) * cw
                y = mt + ch - ((max(val, v_min) - v_min) / v_range * ch)
                pts.append(f"{x:.1f},{y:.1f}")

            color = colors[ci % len(colors)]
            opacity = "0.12" if ci > 8 else "0.35"
            sw = "0.7" if ci > 8 else "1.2"
            eq_svg += f'  <polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{sw}" opacity="{opacity}"/>\n'

        # Y axis
        for i in range(5):
            val = v_min + v_range * i / 4
            y = mt + ch - (i / 4 * ch)
            eq_svg += f'  <text x="{ml-5}" y="{y:.1f}" text-anchor="end" fill="#484f58" font-size="9" dominant-baseline="middle">${val:,.0f}</text>\n'
            eq_svg += f'  <line x1="{ml}" y1="{y:.1f}" x2="{w-10}" y2="{y:.1f}" stroke="#21262d" stroke-width="0.5" stroke-dasharray="2"/>\n'

        # X axis
        for i in range(0, max_len, max(max_len // 6, 1)):
            x = ml + (i / (max_len - 1)) * cw
            eq_svg += f'  <text x="{x:.1f}" y="{h - 8}" text-anchor="middle" fill="#484f58" font-size="9">T{i}</text>\n'

        eq_svg += '</svg>'

    # ── Histogram SVG ──────────────────────────────────────────────────
    hist_svg = _build_capital_histogram(r.all_final_capitals, ic)

    # ── Build HTML ─────────────────────────────────────────────────────
    med_color = "#4CAF50" if r.capital_p50 > ic else "#f44336"
    roi_color = "#4CAF50" if r.roi_p50 > 0 else "#f44336"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Monte Carlo Compuesto — ${ic:,.0f} | {r.risk_pct*100:.0f}% riesgo | {r.trades_per_sim} trades</title>
<style>
  :root {{ --green: #4CAF50; --red: #f44336; --orange: #FF9800; --blue: #2196F3; --gold: #FFD700; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; }}
  h1 {{ color: #58a6ff; margin-bottom: 4px; }}
  h2 {{ color: #8b949e; margin: 32px 0 14px; border-bottom: 1px solid #21262d; padding-bottom: 8px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 28px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(195px, 1fr)); gap: 12px; margin: 20px 0; }}
  .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; }}
  .card .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .5px; }}
  .card .value {{ font-size: 26px; font-weight: 700; margin-top: 4px; }}
  .card .detail {{ font-size: 12px; color: #484f58; margin-top: 2px; }}
  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 25px; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 10px 12px; font-size: 12px;
       text-transform: uppercase; letter-spacing: .5px; border-bottom: 2px solid #21262d; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  tr:hover td {{ background: #161b22; }}
  .chart-container {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 20px; margin: 20px 0; }}
  svg {{ width: 100%; height: auto; }}
  .highlight {{ background: #161b22; border-left: 3px solid var(--gold); padding: 16px 20px; margin: 20px 0; border-radius: 0 8px 8px 0; }}
  .highlight h3 {{ color: var(--gold); margin-bottom: 10px; }}
  .highlight p {{ margin: 5px 0; font-size: 14px; }}
  .prob-bar {{ display: flex; align-items: center; gap: 10px; margin: 6px 0; }}
  .prob-bar .bar-bg {{ flex: 1; height: 22px; background: #21262d; border-radius: 4px; overflow: hidden; }}
  .prob-bar .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .prob-bar .bar-label {{ width: 55px; text-align: right; font-size: 13px; font-weight: 600; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #21262d; font-size: 12px; color: #484f58; }}
</style>
</head>
<body>

<h1>Monte Carlo &mdash; Capital Compuesto</h1>
<div class="subtitle">
  ${ic:,.0f} inicial &rarr; {r.trades_per_sim} trades &rarr; {r.risk_pct*100:.0f}% riesgo/trade
  &mdash; {r.num_simulations:,} simulaciones &mdash; {r.source_trades} trades fuente
</div>

<!-- ─── KPI CARDS ─────────────────────────────────────────────────── -->
<div class="grid">
  <div class="card">
    <div class="label">Capital Mediano</div>
    <div class="value" style="color:{med_color}">${r.capital_p50:,.0f}</div>
    <div class="detail">{r.capital_p50/ic:.1f}x inicial</div>
  </div>
  <div class="card">
    <div class="label">ROI Mediano</div>
    <div class="value" style="color:{roi_color}">{r.roi_p50:+,.0f}%</div>
    <div class="detail">Rango: {r.roi_p5:+,.0f}% a {r.roi_p95:+,.0f}%</div>
  </div>
  <div class="card">
    <div class="label">P(Duplicar)</div>
    <div class="value {'positive' if r.prob_double > 0.5 else 'negative'}">{r.prob_double*100:.1f}%</div>
    <div class="detail">&ge; ${ic*2:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">P(Triplicar)</div>
    <div class="value">{r.prob_triple*100:.1f}%</div>
    <div class="detail">&ge; ${ic*3:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">P(Perder)</div>
    <div class="value {'positive' if r.prob_loss < 0.15 else 'negative'}">{r.prob_loss*100:.1f}%</div>
    <div class="detail">Capital &lt; ${ic:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">Max DD% (P95)</div>
    <div class="value negative">{r.max_drawdown_pct_p95:.0f}%</div>
    <div class="detail">Mediano: {r.max_drawdown_pct_median:.0f}%</div>
  </div>
</div>

<!-- ─── MAIN RESULT ───────────────────────────────────────────────── -->
<div class="highlight">
  <h3>Resultado en {r.trades_per_sim} trades</h3>
  <p>Con <strong>${ic:,.0f}</strong> de capital inicial y arriesgando <strong>{r.risk_pct*100:.0f}%</strong> por trade:</p>
  <p>&#128176; El <strong>50%</strong> de las veces terminas con <strong>${r.capital_p50:,.0f}</strong> o mas ({r.capital_p50/ic:.1f}x)</p>
  <p>&#128176; El <strong>75%</strong> de las veces terminas con <strong>${r.capital_p25:,.0f}</strong> o mas</p>
  <p>&#128176; Solo hay <strong>{r.prob_loss*100:.1f}%</strong> de probabilidad de perder dinero</p>
  <p>&#9888;&#65039; En el peor 5% de escenarios: <strong>${r.capital_p5:,.0f}</strong></p>
  <p>&#127775; En el mejor 5%: <strong>${r.capital_p95:,.0f}</strong> ({r.capital_p95/ic:.1f}x)</p>
</div>

<!-- ─── PERCENTILE TABLE ──────────────────────────────────────────── -->
<h2>Distribucion de Capital Final</h2>
<table>
  <tr><th>Percentil</th><th>Capital</th><th>ROI</th><th>Multiplo</th><th>Significado</th></tr>
  <tr><td>P5</td><td>${r.capital_p5:,.2f}</td><td>{r.roi_p5:+.1f}%</td><td>{r.capital_p5/ic:.2f}x</td><td>Peor caso realista</td></tr>
  <tr><td>P10</td><td>${r.capital_p10:,.2f}</td><td>{(r.capital_p10-ic)/ic*100:+.1f}%</td><td>{r.capital_p10/ic:.2f}x</td><td>Escenario pesimista</td></tr>
  <tr><td>P25</td><td>${r.capital_p25:,.2f}</td><td>{r.roi_p25:+.1f}%</td><td>{r.capital_p25/ic:.2f}x</td><td>Cuartil inferior</td></tr>
  <tr style="background:#0d2137"><td><strong>P50</strong></td><td style="color:{med_color}"><strong>${r.capital_p50:,.2f}</strong></td><td><strong>{r.roi_p50:+.1f}%</strong></td><td><strong>{r.capital_p50/ic:.2f}x</strong></td><td><strong>Resultado mas probable</strong></td></tr>
  <tr><td>P75</td><td class="positive">${r.capital_p75:,.2f}</td><td>{r.roi_p75:+.1f}%</td><td>{r.capital_p75/ic:.2f}x</td><td>Cuartil superior</td></tr>
  <tr><td>P90</td><td class="positive">${r.capital_p90:,.2f}</td><td>{(r.capital_p90-ic)/ic*100:+.1f}%</td><td>{r.capital_p90/ic:.2f}x</td><td>Escenario optimista</td></tr>
  <tr><td>P95</td><td class="positive">${r.capital_p95:,.2f}</td><td>{r.roi_p95:+.1f}%</td><td>{r.capital_p95/ic:.2f}x</td><td>Mejor caso realista</td></tr>
</table>

<!-- ─── GROWTH PROBABILITIES ──────────────────────────────────────── -->
<h2>Probabilidades de Crecimiento</h2>

<div class="prob-bar">
  <span style="width:140px">2x (${ic*2:,.0f})</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{r.prob_double*100:.1f}%;background:var(--green)"></div></div>
  <span class="bar-label">{r.prob_double*100:.1f}%</span>
</div>
<div class="prob-bar">
  <span style="width:140px">3x (${ic*3:,.0f})</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{r.prob_triple*100:.1f}%;background:var(--green)"></div></div>
  <span class="bar-label">{r.prob_triple*100:.1f}%</span>
</div>
<div class="prob-bar">
  <span style="width:140px">5x (${ic*5:,.0f})</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{r.prob_5x*100:.1f}%;background:var(--blue)"></div></div>
  <span class="bar-label">{r.prob_5x*100:.1f}%</span>
</div>
<div class="prob-bar">
  <span style="width:140px">10x (${ic*10:,.0f})</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{r.prob_10x*100:.1f}%;background:var(--gold)"></div></div>
  <span class="bar-label">{r.prob_10x*100:.1f}%</span>
</div>

<h2 style="margin-top:30px">Riesgo de Perdida</h2>

<div class="prob-bar">
  <span style="width:140px">Perder dinero</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{r.prob_loss*100:.1f}%;background:var(--red)"></div></div>
  <span class="bar-label">{r.prob_loss*100:.1f}%</span>
</div>
<div class="prob-bar">
  <span style="width:140px">Perder &gt;50%</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{r.prob_half*100:.1f}%;background:var(--red)"></div></div>
  <span class="bar-label">{r.prob_half*100:.1f}%</span>
</div>
<div class="prob-bar">
  <span style="width:140px">Ruina (&lt;$500)</span>
  <div class="bar-bg"><div class="bar-fill" style="width:{min(r.prob_ruin*100, 100):.1f}%;background:#b71c1c"></div></div>
  <span class="bar-label">{r.prob_ruin*100:.1f}%</span>
</div>

<!-- ─── CHARTS ────────────────────────────────────────────────────── -->
<h2>Equity Curves ({min(len(r.sample_curves), 80)} simulaciones)</h2>
<div class="chart-container">
  {eq_svg}
</div>

<h2>Distribucion de Capital Final</h2>
<div class="chart-container">
  {hist_svg}
</div>

<!-- ─── MILESTONES ────────────────────────────────────────────────── -->
{'<h2>Milestones</h2><table>' +
  (f'<tr><td>Trades para duplicar (mediana)</td><td><strong>{r.trades_to_double_median:.0f}</strong></td></tr>' if r.trades_to_double_median > 0 else '') +
  (f'<tr><td>Trades para triplicar (mediana)</td><td><strong>{r.trades_to_triple_median:.0f}</strong></td></tr>' if r.trades_to_triple_median > 0 else '') +
  '</table>' if r.trades_to_double_median > 0 else ''}

<div class="footer">
  <p>Generado: {datetime.now().isoformat()}</p>
  <p>Monte Carlo Compuesto &mdash; Price Action Trading System &mdash; Eduardo (PRN-Million plus)</p>
  <p>Metodo: Bootstrap con reemplazo. {r.num_simulations:,} simulaciones, {r.trades_per_sim} trades/sim, {r.risk_pct*100:.0f}% riesgo.</p>
  <p>PnL convertido a retorno sobre riesgo (SL={r.sl_distance} pts). Compounding aplicado trade a trade.</p>
  <p><em>Disclaimer: Resultados pasados no garantizan rendimiento futuro. El 50% de riesgo por trade
  es una estrategia agresiva. Consultar siempre con un asesor financiero.</em></p>
</div>

</body>
</html>"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    return filepath


def _build_capital_histogram(data: List[float], initial_capital: float) -> str:
    """Histograma de capital final."""
    if not data:
        return "<p>No data</p>"

    bins = 50
    d_min = min(data)
    d_max = max(data)
    d_range = d_max - d_min if d_max != d_min else 1
    bin_width = d_range / bins

    counts = [0] * bins
    for val in data:
        idx = min(int((val - d_min) / bin_width), bins - 1)
        counts[idx] += 1

    max_count = max(counts) if counts else 1
    w, h = 720, 260
    ml, mt, mb = 70, 25, 40
    cw = w - ml - 10
    ch = h - mt - mb
    bw = cw / bins

    svg = f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">\n'

    for i, count in enumerate(counts):
        bar_h = (count / max_count) * ch if max_count > 0 else 0
        x = ml + i * bw
        y = mt + ch - bar_h
        bin_center = d_min + (i + 0.5) * bin_width
        color = "#4CAF50" if bin_center >= initial_capital else "#f44336"
        svg += f'  <rect x="{x:.1f}" y="{y:.1f}" width="{max(bw-1,1):.1f}" height="{bar_h:.1f}" fill="{color}" opacity="0.7"/>\n'

    # Initial capital line
    ic_x = ml + (initial_capital - d_min) / d_range * cw
    if 0 < ic_x < w:
        svg += f'  <line x1="{ic_x:.1f}" y1="{mt}" x2="{ic_x:.1f}" y2="{mt+ch}" stroke="#FF9800" stroke-width="2" stroke-dasharray="4"/>\n'
        svg += f'  <text x="{ic_x:.1f}" y="{mt-5}" text-anchor="middle" fill="#FF9800" font-size="10">${initial_capital:,.0f}</text>\n'

    # X axis labels
    for i in range(0, bins + 1, bins // 5):
        val = d_min + i * bin_width
        x = ml + i * bw
        svg += f'  <text x="{x:.1f}" y="{mt+ch+15}" text-anchor="middle" fill="#484f58" font-size="9">${val:,.0f}</text>\n'

    svg += f'  <text x="{w//2}" y="{h-5}" text-anchor="middle" fill="#484f58" font-size="10">Capital Final ($)</text>\n'
    svg += '</svg>'
    return svg


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Monte Carlo con capital compuesto")
    parser.add_argument("--capital", type=float, default=5000, help="Capital inicial ($)")
    parser.add_argument("--risk-pct", type=float, default=50, help="Riesgo por trade (%%)")
    parser.add_argument("--trades", type=int, default=60, help="Trades por simulacion")
    parser.add_argument("--sims", type=int, default=10000, help="Numero de simulaciones")
    parser.add_argument("--days", type=int, default=30, help="Dias de backtest para datos")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--symbol", default="^GSPC")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-html", action="store_true")
    args = parser.parse_args()

    # ── Paso 1: Backtest ──────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  PASO 1: BACKTEST — Obtener trades reales")
    print("=" * 64)

    from pa_backtester import HistoricalDataProvider, PriceActionBacktester, print_console_report

    provider = HistoricalDataProvider(symbol=args.symbol)
    df_1h, df_5m, df_2m = provider.fetch(start=args.start, end=args.end, days=args.days)

    if df_2m.empty:
        print("Sin datos. Abortando.")
        sys.exit(1)

    backtester = PriceActionBacktester()
    bt_result = backtester.run(df_1h, df_5m, df_2m)
    print_console_report(bt_result)

    if not bt_result.trades:
        print("Sin trades. Abortando.")
        sys.exit(1)

    # ── Paso 2: Monte Carlo Compuesto ─────────────────────────────────
    trade_pnls = [t.pnl_points for t in bt_result.trades]

    mc = CompoundMonteCarloSimulator(trade_pnls=trade_pnls, sl_distance=12.0)
    result = mc.run(
        initial_capital=args.capital,
        risk_pct=args.risk_pct / 100,
        trades_per_sim=args.trades,
        simulations=args.sims,
        seed=args.seed,
    )

    print_compound_report(result)

    if not args.no_html:
        html_path = generate_compound_html(result)
        print(f"\n📊 Reporte HTML: {html_path}")
        print("   Abre en el navegador para graficos y distribuciones.\n")


if __name__ == "__main__":
    main()
