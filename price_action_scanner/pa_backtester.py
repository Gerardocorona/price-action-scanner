"""
pa_backtester.py — Backtesting Engine for Price Action Scanner
==============================================================
Descarga datos históricos de SPX, simula la estrategia barra a barra,
y genera reportes detallados de rendimiento.

Uso:
    python pa_backtester.py                        # últimos 30 días
    python pa_backtester.py --days 60              # últimos 60 días
    python pa_backtester.py --start 2026-03-01 --end 2026-04-04

Dependencias:
    pip install yfinance pandas
"""

import argparse
import asyncio
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("❌  yfinance no instalado. Ejecuta: pip install yfinance")
    sys.exit(1)

# ── Ajustar sys.path para imports relativos ────────────────────────────────
_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_ENGINE_DIR, ".."))
for _p in (_ROOT_DIR, _ENGINE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .pa_detector import PriceActionDetector
from .confluence_checker import ConfluenceChecker
from .pa_signal_schema import (
    PatternData,
    TrendContext,
    ConfluenceData,
    OrderData,
    PriceActionSignal,
)

import yaml

_CFG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    """Una operación ejecutada durante el backtest."""
    trade_id: int
    signal_id: str
    entry_time: str
    entry_price: float
    direction: str          # 'CALL' o 'PUT'
    pattern_type: str
    pattern_direction: str
    confluence_score: float
    confluence_factors: int
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    trail_active: bool = False
    trail_stop: float = 0.0
    trail_high: float = 0.0     # Max favorable excursion
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_points: float = 0.0
    bars_held: int = 0


@dataclass
class BacktestResult:
    """Resultado completo del backtest."""
    # Config
    symbol: str
    start_date: str
    end_date: str
    total_bars_2m: int = 0

    # Señales
    patterns_found: int = 0
    signals_generated: int = 0
    signals_rejected_lateral: int = 0
    signals_rejected_zone: int = 0
    signals_rejected_confluence: int = 0
    signals_passed: int = 0

    # Trades
    trades: List[Trade] = field(default_factory=list)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0

    # PnL
    total_pnl_points: float = 0.0
    avg_pnl_points: float = 0.0
    max_win_points: float = 0.0
    max_loss_points: float = 0.0
    avg_win_points: float = 0.0
    avg_loss_points: float = 0.0

    # Risk metrics
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown_points: float = 0.0
    max_consecutive_losses: int = 0
    sharpe_ratio: float = 0.0

    # Pattern breakdown
    pattern_stats: Dict = field(default_factory=dict)

    # Equity curve
    equity_curve: List[float] = field(default_factory=list)

    # Daily breakdown
    daily_pnl: Dict[str, float] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# DATA PROVIDER — Descarga + resample de datos históricos
# ═══════════════════════════════════════════════════════════════════════════

class HistoricalDataProvider:
    """
    Descarga datos intraday de SPX via yfinance y genera barras
    multi-timeframe (1H, 5M, 2M).

    Nota: yfinance tiene límites —
      • 2m data: máximo 60 días de historia
      • 5m data: máximo 60 días
      • 1h data: máximo 730 días
    """

    def __init__(self, symbol: str = "^GSPC"):
        self.symbol = symbol

    def fetch(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        days: int = 30,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Descarga datos y retorna (df_1h, df_5m, df_2m).

        Args:
            start: Fecha inicio 'YYYY-MM-DD' (opcional)
            end:   Fecha fin 'YYYY-MM-DD' (opcional)
            days:  Días hacia atrás desde hoy (default 30)

        Returns:
            Tupla de 3 DataFrames con columnas [open, high, low, close, volume]
        """
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")
        if start is None:
            start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        ticker = yf.Ticker(self.symbol)

        print(f"\n📥 Descargando datos de {self.symbol}...")
        print(f"   Rango: {start} → {end}")

        # ─ Descargar 2-minute data ──────────────────────────────────────────
        print("   Descargando barras de 2m...", end="", flush=True)
        df_2m = ticker.history(start=start, end=end, interval="2m")
        print(f" {len(df_2m)} barras")

        # ─ Descargar 5-minute data ──────────────────────────────────────────
        print("   Descargando barras de 5m...", end="", flush=True)
        df_5m = ticker.history(start=start, end=end, interval="5m")
        print(f" {len(df_5m)} barras")

        # ─ Descargar 1-hour data ────────────────────────────────────────────
        print("   Descargando barras de 1h...", end="", flush=True)
        df_1h = ticker.history(start=start, end=end, interval="1h")
        print(f" {len(df_1h)} barras")

        # Normalizar columnas a minúsculas
        for df in (df_1h, df_5m, df_2m):
            df.columns = [c.lower() for c in df.columns]

        print(f"\n✅ Datos descargados: {len(df_2m)} bars(2m), "
              f"{len(df_5m)} bars(5m), {len(df_1h)} bars(1h)\n")

        return df_1h, df_5m, df_2m

    @staticmethod
    def df_to_bars(df: pd.DataFrame) -> List[Dict]:
        """Convierte un DataFrame a list-of-dicts para el scanner."""
        bars = []
        for idx, row in df.iterrows():
            bars.append({
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row.get('volume', 0)),
                'timestamp': str(idx),
            })
        return bars


# ═══════════════════════════════════════════════════════════════════════════
# BACKTESTING ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class PriceActionBacktester:
    """
    Motor de backtesting que simula la estrategia barra a barra.

    Walk-forward approach:
      - Para cada nueva barra de 2m cerrada:
        1. Construir ventana de 1H/5M/2M disponible hasta ese momento
        2. Ejecutar pa_detector.detect_latest()
        3. Si patrón → confluence_checker.check()
        4. Si confluencia → abrir trade
        5. Gestionar trades abiertos (SL/TP/Trail tick-by-tick)
    """

    def __init__(self, config_path: str = _CFG_PATH):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.detector = PriceActionDetector(config_path=config_path)
        self.checker = ConfluenceChecker(config_path=config_path)

        # Risk params
        self._sl_dist = self.cfg['execution']['stop_loss_distance']
        self._tp1_dist = self.cfg['execution']['take_profit_1_distance']
        self._tp2_dist = self.cfg['execution']['take_profit_2_distance']
        self._trail_enabled = self.cfg['execution']['trailing_stop']['enabled']
        self._trail_activate = self.cfg['execution']['trailing_stop']['activate_after_points']
        self._trail_distance = self.cfg['execution']['trailing_stop']['trail_distance']
        self._max_signals = self.cfg['execution']['session_max_signals']
        self._max_contracts = self.cfg['execution']['max_contracts_per_signal']

    def run(
        self,
        df_1h: pd.DataFrame,
        df_5m: pd.DataFrame,
        df_2m: pd.DataFrame,
    ) -> BacktestResult:
        """
        Ejecuta el backtest completo.

        Walk-forward: para cada barra de 2m, mira solo datos
        anteriores (sin look-ahead bias).
        """
        result = BacktestResult(
            symbol="SPX",
            start_date=str(df_2m.index[0].date()) if len(df_2m) > 0 else "",
            end_date=str(df_2m.index[-1].date()) if len(df_2m) > 0 else "",
            total_bars_2m=len(df_2m),
        )

        bars_1h_all = HistoricalDataProvider.df_to_bars(df_1h)
        bars_5m_all = HistoricalDataProvider.df_to_bars(df_5m)
        bars_2m_all = HistoricalDataProvider.df_to_bars(df_2m)

        # ── State ──────────────────────────────────────────────────────────
        open_trades: List[Trade] = []
        closed_trades: List[Trade] = []
        trade_counter = 0
        daily_signal_count: Dict[str, int] = defaultdict(int)
        equity = 0.0
        equity_curve = [0.0]
        peak_equity = 0.0
        max_drawdown = 0.0

        total_bars = len(bars_2m_all)
        print_every = max(total_bars // 20, 1)

        print("=" * 70)
        print("  BACKTESTING — Price Action Scanner (SPX 0DTE)")
        print(f"  {result.start_date} → {result.end_date}")
        print(f"  Barras 2m: {total_bars}")
        print("=" * 70)

        # ── Walk-forward loop ──────────────────────────────────────────────
        for i in range(2, total_bars):
            bar_2m = bars_2m_all[i]
            current_price = bar_2m['close']
            bar_ts = bar_2m.get('timestamp', '')
            bar_date = bar_ts[:10] if len(bar_ts) >= 10 else ''

            if i % print_every == 0:
                pct = i / total_bars * 100
                print(f"  [{pct:5.1f}%] Barra {i}/{total_bars}  "
                      f"Precio: {current_price:.2f}  "
                      f"Trades: {len(closed_trades)} cerrados, {len(open_trades)} abiertos")

            # ── 1. Gestionar trades abiertos ────────────────────────────────
            trades_to_close = []
            for trade in open_trades:
                trade.bars_held += 1
                exit_reason, exit_price = self._check_trade_exit(
                    trade, bar_2m
                )
                if exit_reason:
                    trade.exit_time = bar_ts
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason

                    if trade.direction == 'CALL':
                        trade.pnl_points = exit_price - trade.entry_price
                    else:
                        trade.pnl_points = trade.entry_price - exit_price

                    trades_to_close.append(trade)

            for trade in trades_to_close:
                open_trades.remove(trade)
                closed_trades.append(trade)
                equity += trade.pnl_points
                equity_curve.append(equity)

                if equity > peak_equity:
                    peak_equity = equity
                dd = peak_equity - equity
                if dd > max_drawdown:
                    max_drawdown = dd

            # ── 2. Detectar patrón en 2m ────────────────────────────────────
            window_2m = bars_2m_all[max(0, i - 30):i + 1]

            if len(window_2m) < 2:
                continue

            pattern = self.detector.detect_latest(window_2m)
            if not pattern:
                continue

            result.patterns_found += 1

            # ── 3. Construir contexto de tendencia ──────────────────────────
            # Buscar barras 5m y 1h cuyo timestamp sea <= bar_ts actual
            window_5m = self._get_bars_up_to(bars_5m_all, bar_ts, count=50)
            window_1h = self._get_bars_up_to(bars_1h_all, bar_ts, count=30)

            if len(window_5m) < 5 or len(window_1h) < 3:
                continue

            trend = self.checker.build_trend_context(
                bars_1h=window_1h,
                bars_5m=window_5m,
                bars_2m=window_2m,
            )

            # ── 4. Filtro: mercado lateral ──────────────────────────────────
            if trend.is_lateral_market:
                result.signals_rejected_lateral += 1
                continue

            # ── 5. Verificar confluencia ────────────────────────────────────
            confluence = self.checker.check(
                pattern=pattern,
                trend=trend,
                current_price=current_price,
                bars_5m=window_5m,
            )

            result.signals_generated += 1

            if confluence.rejected_reason == 'price_not_in_zone':
                result.signals_rejected_zone += 1
                continue

            if not confluence.meets_minimum:
                result.signals_rejected_confluence += 1
                continue

            result.signals_passed += 1

            # ── 6. ¿Límite diario alcanzado? ────────────────────────────────
            if daily_signal_count[bar_date] >= self._max_signals:
                continue

            # ── 7. ¿Ya hay trade abierto en misma dirección? ────────────────
            same_dir = any(
                t.direction == ('CALL' if pattern.direction == 'bullish' else 'PUT')
                for t in open_trades
            )
            if same_dir:
                continue

            # ── 8. Abrir trade ──────────────────────────────────────────────
            trade_counter += 1
            daily_signal_count[bar_date] += 1

            direction = 'CALL' if pattern.direction == 'bullish' else 'PUT'

            if direction == 'CALL':
                sl = current_price - self._sl_dist
                tp1 = current_price + self._tp1_dist
                tp2 = current_price + self._tp2_dist
            else:
                sl = current_price + self._sl_dist
                tp1 = current_price - self._tp1_dist
                tp2 = current_price - self._tp2_dist

            trade = Trade(
                trade_id=trade_counter,
                signal_id=f"bt-{trade_counter:04d}",
                entry_time=bar_ts,
                entry_price=current_price,
                direction=direction,
                pattern_type=pattern.pattern_type,
                pattern_direction=pattern.direction,
                confluence_score=confluence.score,
                confluence_factors=confluence.factors_count,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                trail_high=current_price,
            )
            open_trades.append(trade)

        # ── Cerrar trades abiertos al final ─────────────────────────────────
        final_price = bars_2m_all[-1]['close'] if bars_2m_all else 0
        for trade in open_trades:
            trade.exit_time = bars_2m_all[-1].get('timestamp', '')
            trade.exit_price = final_price
            trade.exit_reason = 'session_end'
            if trade.direction == 'CALL':
                trade.pnl_points = final_price - trade.entry_price
            else:
                trade.pnl_points = trade.entry_price - final_price
            closed_trades.append(trade)
            equity += trade.pnl_points
            equity_curve.append(equity)

        # ── Compilar resultados ─────────────────────────────────────────────
        result.trades = closed_trades
        result.equity_curve = equity_curve
        result.max_drawdown_points = max_drawdown
        self._compile_stats(result)

        return result

    def _check_trade_exit(self, trade: Trade, bar: Dict) -> Tuple[Optional[str], float]:
        """
        Verifica si un trade debe cerrarse basado en SL/TP/Trail.

        Simula movimiento intra-bar: high primero si CALL, low primero si PUT.

        Returns:
            (exit_reason, exit_price) o (None, 0)
        """
        h = bar['high']
        l = bar['low']

        if trade.direction == 'CALL':
            # ─ CALL: ganamos si sube ─────────────────────────────────────
            # Check SL primero (asumimos lo peor)
            if l <= trade.stop_loss:
                return 'stop_loss', trade.stop_loss

            # Check TP2
            if h >= trade.take_profit_2:
                return 'take_profit_2', trade.take_profit_2

            # Check TP1 (medio cierre — simplificamos como cierre completo)
            if h >= trade.take_profit_1:
                return 'take_profit_1', trade.take_profit_1

            # Trailing stop
            if self._trail_enabled:
                if h > trade.trail_high:
                    trade.trail_high = h
                gain = trade.trail_high - trade.entry_price
                if gain >= self._trail_activate:
                    trade.trail_active = True
                    trade.trail_stop = trade.trail_high - self._trail_distance

                if trade.trail_active and l <= trade.trail_stop:
                    return 'trailing_stop', trade.trail_stop

        else:
            # ─ PUT: ganamos si baja ──────────────────────────────────────
            if h >= trade.stop_loss:
                return 'stop_loss', trade.stop_loss

            if l <= trade.take_profit_2:
                return 'take_profit_2', trade.take_profit_2

            if l <= trade.take_profit_1:
                return 'take_profit_1', trade.take_profit_1

            # Trailing: track lowest low
            if self._trail_enabled:
                if l < trade.trail_high or trade.trail_high == trade.entry_price:
                    if l < trade.trail_high:
                        trade.trail_high = l
                gain = trade.entry_price - trade.trail_high
                if gain >= self._trail_activate:
                    trade.trail_active = True
                    trade.trail_stop = trade.trail_high + self._trail_distance

                if trade.trail_active and h >= trade.trail_stop:
                    return 'trailing_stop', trade.trail_stop

        return None, 0.0

    def _get_bars_up_to(
        self, bars: List[Dict], current_ts: str, count: int
    ) -> List[Dict]:
        """
        Retorna las últimas `count` barras cuyo timestamp <= current_ts.
        Evita look-ahead bias.
        """
        filtered = [b for b in bars if b.get('timestamp', '') <= current_ts]
        return filtered[-count:]

    def _compile_stats(self, result: BacktestResult):
        """Calcula todas las métricas agregadas."""
        trades = result.trades
        if not trades:
            return

        result.total_trades = len(trades)

        pnls = [t.pnl_points for t in trades]
        wins_list = [p for p in pnls if p > 0]
        losses_list = [p for p in pnls if p < 0]

        result.wins = len(wins_list)
        result.losses = len(losses_list)
        result.breakeven = result.total_trades - result.wins - result.losses

        result.total_pnl_points = sum(pnls)
        result.avg_pnl_points = result.total_pnl_points / len(pnls) if pnls else 0

        result.max_win_points = max(pnls) if pnls else 0
        result.max_loss_points = min(pnls) if pnls else 0

        result.avg_win_points = sum(wins_list) / len(wins_list) if wins_list else 0
        result.avg_loss_points = sum(losses_list) / len(losses_list) if losses_list else 0

        result.win_rate = result.wins / result.total_trades if result.total_trades else 0

        gross_profit = sum(wins_list)
        gross_loss = abs(sum(losses_list))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        result.expectancy = result.avg_pnl_points

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for p in pnls:
            if p < 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0
        result.max_consecutive_losses = max_consec

        # Sharpe ratio (annualised, assuming ~6.5h trading day, bars every ~2min => ~195 bars/day)
        if len(pnls) >= 2:
            avg_ret = sum(pnls) / len(pnls)
            std_ret = math.sqrt(sum((p - avg_ret) ** 2 for p in pnls) / (len(pnls) - 1))
            if std_ret > 0:
                result.sharpe_ratio = (avg_ret / std_ret) * math.sqrt(252)
            else:
                result.sharpe_ratio = 0.0

        # Pattern breakdown
        pattern_map: Dict[str, Dict] = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0})
        for t in trades:
            key = t.pattern_type
            if t.pnl_points > 0:
                pattern_map[key]['wins'] += 1
            else:
                pattern_map[key]['losses'] += 1
            pattern_map[key]['pnl'] += t.pnl_points
        result.pattern_stats = dict(pattern_map)

        # Daily PnL
        daily: Dict[str, float] = defaultdict(float)
        for t in trades:
            day = t.entry_time[:10] if t.entry_time and len(t.entry_time) >= 10 else 'unknown'
            daily[day] += t.pnl_points
        result.daily_pnl = dict(sorted(daily.items()))


# ═══════════════════════════════════════════════════════════════════════════
# HTML REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_backtest_report(result: BacktestResult, output_dir: str = None) -> str:
    """Genera un reporte HTML detallado del backtest."""
    if output_dir is None:
        output_dir = os.path.join(_ENGINE_DIR, "reports")
    os.makedirs(output_dir, exist_ok=True)

    filename = f"backtest_{result.start_date}_to_{result.end_date}.html"
    filepath = os.path.join(output_dir, filename)

    # ── Equity curve como SVG inline ────────────────────────────────────
    eq = result.equity_curve
    if len(eq) > 1:
        eq_min = min(eq)
        eq_max = max(eq)
        eq_range = eq_max - eq_min if eq_max != eq_min else 1
        w, h_svg = 700, 250
        points = []
        for i, val in enumerate(eq):
            x = i / (len(eq) - 1) * w
            y = h_svg - ((val - eq_min) / eq_range * (h_svg - 20) + 10)
            points.append(f"{x:.1f},{y:.1f}")
        polyline = " ".join(points)
        zero_y = h_svg - ((0 - eq_min) / eq_range * (h_svg - 20) + 10)
    else:
        polyline = "0,125 700,125"
        zero_y = 125
        w, h_svg = 700, 250

    # ── Trade table rows ────────────────────────────────────────────────
    trade_rows = ""
    for t in result.trades:
        pnl_cls = "positive" if t.pnl_points > 0 else ("negative" if t.pnl_points < 0 else "")
        trade_rows += f"""
        <tr>
            <td>{t.trade_id}</td>
            <td>{t.entry_time[:19] if t.entry_time else ''}</td>
            <td>{t.pattern_type}</td>
            <td>{t.direction}</td>
            <td>${t.entry_price:.2f}</td>
            <td>${(t.exit_price if t.exit_price else 0):.2f}</td>
            <td class="{pnl_cls}">{t.pnl_points:+.2f}</td>
            <td>{t.exit_reason or ''}</td>
            <td>{t.confluence_factors}</td>
            <td>{t.bars_held}</td>
        </tr>"""

    # ── Pattern breakdown rows ──────────────────────────────────────────
    pattern_rows = ""
    for pname, pdata in result.pattern_stats.items():
        total_p = pdata['wins'] + pdata['losses']
        wr = pdata['wins'] / total_p * 100 if total_p else 0
        pnl_cls = "positive" if pdata['pnl'] > 0 else "negative"
        pattern_rows += f"""
        <tr>
            <td>{pname}</td>
            <td>{total_p}</td>
            <td>{pdata['wins']}</td>
            <td>{pdata['losses']}</td>
            <td>{wr:.1f}%</td>
            <td class="{pnl_cls}">{pdata['pnl']:+.2f}</td>
        </tr>"""

    # ── Daily PnL rows ──────────────────────────────────────────────────
    daily_rows = ""
    running = 0.0
    for day, pnl in result.daily_pnl.items():
        running += pnl
        pnl_cls = "positive" if pnl > 0 else ("negative" if pnl < 0 else "")
        daily_rows += f"""
        <tr>
            <td>{day}</td>
            <td class="{pnl_cls}">{pnl:+.2f}</td>
            <td>{running:+.2f}</td>
        </tr>"""

    # ── Build HTML ──────────────────────────────────────────────────────
    pnl_color = "#4CAF50" if result.total_pnl_points >= 0 else "#f44336"
    wr_color = "#4CAF50" if result.win_rate >= 0.50 else "#FF9800"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Backtest Report — SPX Price Action</title>
<style>
  :root {{ --green: #4CAF50; --red: #f44336; --orange: #FF9800; --blue: #2196F3; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
  h1 {{ color: #58a6ff; margin-bottom: 5px; }}
  h2 {{ color: #8b949e; margin: 30px 0 15px; border-bottom: 1px solid #21262d; padding-bottom: 8px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 25px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
  .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; }}
  .card .label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 25px; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 10px 12px; font-size: 12px;
       text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #21262d; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  tr:hover td {{ background: #161b22; }}
  .chart-container {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 20px; margin: 20px 0; }}
  svg {{ width: 100%; height: auto; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #21262d; font-size: 12px; color: #484f58; }}
  .signal-funnel {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 15px 0; }}
  .funnel-step {{ background: #161b22; border: 1px solid #21262d; border-radius: 6px; padding: 10px 14px; text-align: center; }}
  .funnel-step .num {{ font-size: 22px; font-weight: 700; color: #58a6ff; }}
  .funnel-step .desc {{ font-size: 11px; color: #8b949e; }}
  .funnel-arrow {{ color: #484f58; font-size: 20px; }}
</style>
</head>
<body>

<h1>Backtest Report</h1>
<div class="subtitle">Price Action Scanner (Eduardo / PRN-Million plus) &mdash; SPX 0DTE &mdash; {result.start_date} to {result.end_date}</div>

<!-- ─── KPI CARDS ─────────────────────────────────────────────────── -->
<div class="grid">
  <div class="card">
    <div class="label">Total PnL (pts)</div>
    <div class="value" style="color: {pnl_color}">{result.total_pnl_points:+.2f}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value" style="color: {wr_color}">{result.win_rate*100:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">Total Trades</div>
    <div class="value">{result.total_trades}</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor</div>
    <div class="value">{result.profit_factor:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Sharpe Ratio</div>
    <div class="value">{result.sharpe_ratio:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Max Drawdown</div>
    <div class="value negative">-{result.max_drawdown_points:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Avg Win / Avg Loss</div>
    <div class="value">{result.avg_win_points:.1f} / {result.avg_loss_points:.1f}</div>
  </div>
  <div class="card">
    <div class="label">Max Consec. Losses</div>
    <div class="value">{result.max_consecutive_losses}</div>
  </div>
</div>

<!-- ─── SIGNAL FUNNEL ─────────────────────────────────────────────── -->
<h2>Signal Funnel</h2>
<div class="signal-funnel">
  <div class="funnel-step"><div class="num">{result.total_bars_2m}</div><div class="desc">Barras 2m</div></div>
  <div class="funnel-arrow">&rarr;</div>
  <div class="funnel-step"><div class="num">{result.patterns_found}</div><div class="desc">Patrones</div></div>
  <div class="funnel-arrow">&rarr;</div>
  <div class="funnel-step"><div class="num">{result.signals_generated}</div><div class="desc">Señales</div></div>
  <div class="funnel-arrow">&rarr;</div>
  <div class="funnel-step"><div class="num">{result.signals_passed}</div><div class="desc">Con confluencia</div></div>
  <div class="funnel-arrow">&rarr;</div>
  <div class="funnel-step"><div class="num">{result.total_trades}</div><div class="desc">Trades</div></div>
</div>
<p style="color:#8b949e; font-size:13px; margin-top:8px;">
  Rechazados: {result.signals_rejected_lateral} lateral | {result.signals_rejected_zone} fuera de zona | {result.signals_rejected_confluence} confluencia insuficiente
</p>

<!-- ─── EQUITY CURVE ─────────────────────────────────────────────── -->
<h2>Equity Curve (puntos)</h2>
<div class="chart-container">
  <svg viewBox="0 0 {w} {h_svg}" preserveAspectRatio="xMidYMid meet">
    <line x1="0" y1="{zero_y:.1f}" x2="{w}" y2="{zero_y:.1f}" stroke="#21262d" stroke-dasharray="4"/>
    <polyline points="{polyline}" fill="none" stroke="{pnl_color}" stroke-width="2"/>
  </svg>
</div>

<!-- ─── PATTERN BREAKDOWN ─────────────────────────────────────────── -->
<h2>Pattern Breakdown</h2>
<table>
  <tr><th>Patrón</th><th>Total</th><th>Wins</th><th>Losses</th><th>Win Rate</th><th>PnL (pts)</th></tr>
  {pattern_rows}
</table>

<!-- ─── DAILY PnL ─────────────────────────────────────────────────── -->
<h2>Daily PnL</h2>
<table>
  <tr><th>Fecha</th><th>PnL (pts)</th><th>Acumulado</th></tr>
  {daily_rows}
</table>

<!-- ─── TRADE LOG ─────────────────────────────────────────────────── -->
<h2>Trade Log</h2>
<table>
  <tr>
    <th>#</th><th>Entry Time</th><th>Pattern</th><th>Dir</th>
    <th>Entry</th><th>Exit</th><th>PnL</th><th>Reason</th><th>Factors</th><th>Bars</th>
  </tr>
  {trade_rows}
</table>

<!-- ─── CONFIGURATION SNAPSHOT ────────────────────────────────────── -->
<h2>Configuration Snapshot</h2>
<table>
  <tr><td style="color:#8b949e">Stop Loss</td><td>{result.trades[0].stop_loss if result.trades else 'N/A'} ({_CFG_PATH})</td></tr>
  <tr><td style="color:#8b949e">SL Distance</td><td>{12.0} pts</td></tr>
  <tr><td style="color:#8b949e">TP1 Distance</td><td>{20.0} pts</td></tr>
  <tr><td style="color:#8b949e">TP2 Distance</td><td>{35.0} pts</td></tr>
  <tr><td style="color:#8b949e">Trail Activate</td><td>{8.0} pts</td></tr>
  <tr><td style="color:#8b949e">Trail Distance</td><td>{5.0} pts</td></tr>
  <tr><td style="color:#8b949e">Min Confluence Factors</td><td>3</td></tr>
  <tr><td style="color:#8b949e">Max Signals/Session</td><td>5</td></tr>
</table>

<div class="footer">
  <p>Generado: {datetime.now().isoformat()}</p>
  <p>Price Action Trading System &mdash; Eduardo (PRN-Million plus) &mdash; SPX 0DTE</p>
  <p>Engine: pa_backtester.py | Config: pa_config.yaml v1.1.0</p>
</div>

</body>
</html>"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    return filepath


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═══════════════════════════════════════════════════════════════════════════

def print_console_report(result: BacktestResult):
    """Imprime resumen en consola."""
    W = 60
    print("\n" + "=" * W)
    print("  BACKTEST RESULTS")
    print("=" * W)
    print(f"  Period:        {result.start_date} → {result.end_date}")
    print(f"  Bars (2m):     {result.total_bars_2m}")
    print("-" * W)

    print(f"\n  --- SIGNAL FUNNEL ---")
    print(f"  Patterns found:             {result.patterns_found}")
    print(f"  Signals generated:          {result.signals_generated}")
    print(f"    Rejected (lateral):       {result.signals_rejected_lateral}")
    print(f"    Rejected (zone):          {result.signals_rejected_zone}")
    print(f"    Rejected (confluence):    {result.signals_rejected_confluence}")
    print(f"  Signals passed:             {result.signals_passed}")
    print(f"  Trades executed:            {result.total_trades}")

    print(f"\n  --- PERFORMANCE ---")
    pnl_sign = "+" if result.total_pnl_points >= 0 else ""
    print(f"  Total PnL:                  {pnl_sign}{result.total_pnl_points:.2f} pts")
    print(f"  Win Rate:                   {result.win_rate*100:.1f}%  ({result.wins}W / {result.losses}L / {result.breakeven}BE)")
    print(f"  Profit Factor:              {result.profit_factor:.2f}")
    print(f"  Expectancy:                 {result.expectancy:.2f} pts/trade")
    print(f"  Sharpe Ratio:               {result.sharpe_ratio:.2f}")

    print(f"\n  --- RISK ---")
    print(f"  Max Drawdown:               {result.max_drawdown_points:.2f} pts")
    print(f"  Max Win:                    {result.max_win_points:.2f} pts")
    print(f"  Max Loss:                   {result.max_loss_points:.2f} pts")
    print(f"  Avg Win / Avg Loss:         {result.avg_win_points:.2f} / {result.avg_loss_points:.2f}")
    print(f"  Max Consec. Losses:         {result.max_consecutive_losses}")

    print(f"\n  --- PATTERN BREAKDOWN ---")
    for pname, pdata in result.pattern_stats.items():
        total_p = pdata['wins'] + pdata['losses']
        wr = pdata['wins'] / total_p * 100 if total_p else 0
        print(f"  {pname:<20s}  {total_p:3d} trades  WR={wr:.0f}%  PnL={pdata['pnl']:+.1f}pts")

    print(f"\n  --- DAILY PnL ---")
    for day, pnl in result.daily_pnl.items():
        bar = "#" * max(1, int(abs(pnl) / 2))
        sign = "+" if pnl >= 0 else ""
        print(f"  {day}  {sign}{pnl:6.2f} pts  {'[32m' if pnl >= 0 else '[31m'}{bar}[0m")

    print("\n" + "=" * W)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Backtest Price Action Scanner con datos historicos de SPX"
    )
    parser.add_argument("--symbol", default="^GSPC", help="Ticker symbol (default: ^GSPC = S&P 500)")
    parser.add_argument("--days", type=int, default=30, help="Dias hacia atras (default 30)")
    parser.add_argument("--start", default=None, help="Fecha inicio YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Fecha fin YYYY-MM-DD")
    parser.add_argument("--no-report", action="store_true", help="No generar HTML report")

    args = parser.parse_args()

    # 1. Descargar datos
    provider = HistoricalDataProvider(symbol=args.symbol)
    df_1h, df_5m, df_2m = provider.fetch(
        start=args.start, end=args.end, days=args.days
    )

    if df_2m.empty:
        print("❌  No se obtuvieron datos de 2m. Verifica el símbolo y rango de fechas.")
        print("    Nota: yfinance limita datos intraday a ~60 días.")
        sys.exit(1)

    # 2. Ejecutar backtest
    backtester = PriceActionBacktester()
    result = backtester.run(df_1h, df_5m, df_2m)

    # 3. Mostrar resultados en consola
    print_console_report(result)

    # 4. Generar HTML report
    if not args.no_report:
        report_path = generate_backtest_report(result)
        print(f"\n📊 Reporte HTML: {report_path}")
        print(f"   Abre en el navegador para ver detalles completos.\n")


if __name__ == "__main__":
    main()
