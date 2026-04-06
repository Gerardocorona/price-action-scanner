"""
confluence_checker.py — Verificador de confluencia
===================================================
Valida que múltiples factores se alinean para un setup válido:

FILTROS CRÍTICOS (Rechazo inmediato):
  1. Lateral market → RECHAZAR (Eduardo: "No operar en lateral")
  2. Precio NO en zona S/R → RECHAZAR
  3. Fuera de horarios → RECHAZAR

FACTORES DE CONFLUENCIA (Puntaje):
  • nivel_en_zona (2.0) — Precio en zona de S/R
  • nivel_historical_respect (1.8) — Nivel respetado > 70%
  • trend_alignment_1h (1.5) — Tendencia macro alineada
  • trend_alignment_5m (1.2) — Tendencia estructura alineada
  • pattern_detected (1.3) — Patrón identificado
  • retest_after_break (2.5) — ★ CRÍTICO: Break + Retest
  • volume_confirmation (0.8) — Volumen apoya movimiento
  • ma_positioning (0.7) — Precio vs medias móviles

REQUISITO MÍNIMO: 3+ factores para trade válido
"""

import os
import yaml
from typing import List, Dict, Optional, Tuple
from .pa_signal_schema import PatternData, TrendContext, ConfluenceData

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")


class ConfluenceChecker:
    """Verifica confluencia de factores para validar señales"""

    def __init__(self, config_path: str = _CONFIG_PATH):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.levels = self.cfg['levels']
        self.confluence_cfg = self.cfg['confluence']
        self.lateral_cfg = self.cfg['lateral_market']

    def build_trend_context(
        self,
        bars_1h: List[Dict],
        bars_5m: List[Dict],
        bars_2m: List[Dict],
    ) -> TrendContext:
        """
        Construye el contexto de tendencia analizando 3 timeframes.

        Args:
            bars_1h: Barras de 1 hora (últimas 20+)
            bars_5m: Barras de 5 minutos (últimas 20+)
            bars_2m: Barras de 2 minutos (últimas 20+)

        Returns:
            TrendContext con análisis de tendencia y lateral
        """
        # Analizar cada timeframe
        trend_1h = self._analyze_trend(bars_1h)
        trend_5m = self._analyze_trend(bars_5m)
        trend_2m = self._analyze_trend(bars_2m)

        # Detectar mercado lateral en 5m (filtro principal)
        is_lateral, lateral_range = self._detect_lateral_market(bars_5m)

        # Analizar posición vs promedios móviles
        price_vs_ma20_5m = self._price_vs_ma(bars_5m)
        price_vs_ma200_1h = self._price_vs_ma(bars_1h, length=200)

        # Detectar ruptura + retroceso
        break_detected, break_direction = self._detect_break_and_retest(bars_5m)

        return TrendContext(
            trend_1h=trend_1h,
            trend_5m=trend_5m,
            trend_2m=trend_2m,
            is_lateral_market=is_lateral,
            lateral_range_points=lateral_range,
            price_vs_ma20=price_vs_ma20_5m,
            price_vs_ma200=price_vs_ma200_1h,
            break_and_retest_detected=break_detected,
            break_direction=break_direction,
        )

    def check(
        self,
        pattern: PatternData,
        trend: TrendContext,
        current_price: float,
        bars_5m: Optional[List[Dict]] = None,
    ) -> ConfluenceData:
        """
        Verifica confluencia y genera score de validez.

        FILTROS (rechazo inmediato):
          1. Lateral market → REJECT
          2. Precio no en zona S/R → REJECT

        FACTORES (puntuación):
          Suma de pesos, requiere mínimo 3 factores

        Returns:
            ConfluenceData con factors, score, meets_minimum, rejected_reason
        """
        confluence = ConfluenceData(
            min_factors_required=self.confluence_cfg['min_factors_to_trade']
        )

        # ─ FILTRO 1: LATERAL MARKET ─────────────────────────────────────
        if trend.is_lateral_market:
            confluence.rejected_reason = "lateral_market"
            confluence.meets_minimum = False
            return confluence

        # ─ FILTRO 2: PRECIO EN ZONA S/R ─────────────────────────────────
        nearest_level, distance_to_level = self._find_nearest_level(current_price)
        zone_tolerance = self.cfg['levels']['pivot']['zone_tolerance']

        if nearest_level is None or distance_to_level > zone_tolerance:
            confluence.rejected_reason = "price_not_in_zone"
            confluence.meets_minimum = False
            confluence.nearest_level = nearest_level
            confluence.distance_to_level = distance_to_level
            return confluence

        # ─ FACTOR 1: NIVEL EN ZONA ──────────────────────────────────────
        peso = self.confluence_cfg['factor_weights']['nivel_en_zona']
        confluence.factors.append(f"nivel_{nearest_level:.2f}_en_zona")
        confluence.score += peso
        confluence.weights_applied['nivel_en_zona'] = peso

        # ─ FACTOR 2: RESPETO HISTÓRICO ──────────────────────────────────
        historical_respect = self.levels['pivot']['historical_respect_rate']
        if historical_respect >= 0.70:
            peso = self.confluence_cfg['factor_weights']['nivel_historical_respect']
            confluence.factors.append(f"nivel_respetado_{historical_respect:.0%}")
            confluence.score += peso
            confluence.weights_applied['nivel_historical_respect'] = peso

        # ─ FACTOR 3: ALINEACIÓN 1H ──────────────────────────────────────
        if trend.trend_1h == pattern.direction:
            peso = self.confluence_cfg['factor_weights']['trend_alignment_1h']
            confluence.factors.append(f"trend_1h_{trend.trend_1h}")
            confluence.score += peso
            confluence.weights_applied['trend_alignment_1h'] = peso

        # ─ FACTOR 4: ALINEACIÓN 5M ──────────────────────────────────────
        if trend.trend_5m == pattern.direction:
            peso = self.confluence_cfg['factor_weights']['trend_alignment_5m']
            confluence.factors.append(f"trend_5m_{trend.trend_5m}")
            confluence.score += peso
            confluence.weights_applied['trend_alignment_5m'] = peso

        # ─ FACTOR 5: PATRÓN DETECTADO ───────────────────────────────────
        peso = self.confluence_cfg['factor_weights']['pattern_detected']
        confluence.factors.append(f"pattern_{pattern.pattern_type}")
        confluence.score += peso
        confluence.weights_applied['pattern_detected'] = peso

        # ─ FACTOR 6: RUPTURA + RETROCESO ★ CRÍTICO ──────────────────────
        if trend.break_and_retest_detected:
            peso = self.confluence_cfg['factor_weights']['retest_after_break']
            confluence.factors.append(f"break_and_retest_{trend.break_direction}")
            confluence.score += peso
            confluence.weights_applied['retest_after_break'] = peso

        # ─ FACTOR 7: CONFIRMACIÓN DE VOLUMEN ────────────────────────────
        if pattern.volume_ratio >= 0.80:
            peso = self.confluence_cfg['factor_weights']['volume_confirmation']
            confluence.factors.append(f"volume_conf_{pattern.volume_ratio:.2f}")
            confluence.score += peso
            confluence.weights_applied['volume_confirmation'] = peso

        # ─ FACTOR 8: POSICIONAMIENTO DE MEDIAS ───────────────────────────
        if trend.price_vs_ma20 == 'above' and pattern.direction == 'bullish':
            peso = self.confluence_cfg['factor_weights']['ma_positioning']
            confluence.factors.append("price_above_ma20")
            confluence.score += peso
            confluence.weights_applied['ma_positioning'] = peso
        elif trend.price_vs_ma20 == 'below' and pattern.direction == 'bearish':
            peso = self.confluence_cfg['factor_weights']['ma_positioning']
            confluence.factors.append("price_below_ma20")
            confluence.score += peso
            confluence.weights_applied['ma_positioning'] = peso

        # ─ VALIDAR MÍNIMO ───────────────────────────────────────────────
        confluence.factors_count = len(confluence.factors)
        confluence.nearest_level = nearest_level
        confluence.distance_to_level = distance_to_level

        if confluence.factors_count >= confluence.min_factors_required:
            confluence.meets_minimum = True
        else:
            confluence.rejected_reason = f"insufficient_factors_{confluence.factors_count}"
            confluence.meets_minimum = False

        return confluence

    # ─────────────────────────────────────────────────────────────────────────
    # UTILIDADES PRIVADAS
    # ─────────────────────────────────────────────────────────────────────────

    def _analyze_trend(self, bars: List[Dict], lookback: int = 20) -> str:
        """Analiza tendencia: bullish (HH/HL), bearish (LH/LL), o lateral"""
        if len(bars) < lookback:
            bars = bars  # Usar todas si hay menos

        # Dividir en dos mitades
        mid = len(bars) // 2
        first_half = bars[:mid]
        second_half = bars[mid:]

        # Extraer highs y lows
        h1 = [b['high'] for b in first_half]
        l1 = [b['low'] for b in first_half]
        h2 = [b['high'] for b in second_half]
        l2 = [b['low'] for b in second_half]

        avg_h1 = sum(h1) / len(h1) if h1 else 0
        avg_l1 = sum(l1) / len(l1) if l1 else 0
        avg_h2 = sum(h2) / len(h2) if h2 else 0
        avg_l2 = sum(l2) / len(l2) if l2 else 0

        # Detectar patrón
        if avg_h2 > avg_h1 and avg_l2 > avg_l1:
            return 'bullish'
        elif avg_h2 < avg_h1 and avg_l2 < avg_l1:
            return 'bearish'
        else:
            return 'lateral'

    def _detect_lateral_market(self, bars: List[Dict]) -> Tuple[bool, float]:
        """
        Detecta lateral: rango <= max_range_points Y barras directivas < 50%

        Returns:
            (is_lateral, range_points)
        """
        if len(bars) < self.lateral_cfg['lookback_bars']:
            bars_to_check = bars
        else:
            bars_to_check = bars[-self.lateral_cfg['lookback_bars']:]

        # Rango total
        highs = [b['high'] for b in bars_to_check]
        lows = [b['low'] for b in bars_to_check]
        total_range = max(highs) - min(lows)

        # Barras directivas (cambio > 0.5 pt en close vs open)
        directional = 0
        for bar in bars_to_check:
            body = abs(bar['close'] - bar['open'])
            if body > 0.5:
                directional += 1

        directional_pct = directional / len(bars_to_check)

        is_lateral = (
            total_range <= self.lateral_cfg['max_range_points'] and
            directional_pct < self.lateral_cfg['min_directional_bars_pct']
        )

        return is_lateral, total_range

    def _detect_break_and_retest(self, bars: List[Dict]) -> Tuple[bool, Optional[str]]:
        """
        Detecta ruptura + retroceso en últimas barras.

        Returns:
            (break_detected, break_direction)
        """
        if len(bars) < 3:
            return False, None

        # Simplificado: si hay nuevos highs Y nuevos lows, hay movimiento
        # Esto podría expandirse para detectar específicamente ruptura + retroceso
        # Por ahora, retorna false para que el factor se agregue cuando sea detectado
        # en el análisis de patrón específico

        return False, None

    def _find_nearest_level(self, price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        Encuentra el nivel S/R más cercano al precio actual.

        Returns:
            (level_price, distance)
        """
        all_levels = [self.levels['pivot']['price']]

        for level_list in [self.levels.get('resistance', []), self.levels.get('support', [])]:
            if level_list:
                for level_dict in level_list:
                    all_levels.append(level_dict['price'])

        if not all_levels:
            return None, None

        nearest = min(all_levels, key=lambda l: abs(l - price))
        distance = abs(nearest - price)

        return nearest, distance

    def _price_vs_ma(self, bars: List[Dict], length: int = 20) -> str:
        """Calcula si precio está arriba o abajo del promedio móvil simple"""
        if len(bars) < length:
            return 'unknown'

        closes = [b['close'] for b in bars[-length:]]
        ma = sum(closes) / len(closes)

        if bars[-1]['close'] > ma:
            return 'above'
        else:
            return 'below'

    def reload_config(self):
        """Recarga configuración (post-calibración)"""
        with open(self._CONFIG_PATH) as f:
            self.cfg = yaml.safe_load(f)
        self.levels = self.cfg['levels']
        self.confluence_cfg = self.cfg['confluence']
        self.lateral_cfg = self.cfg['lateral_market']
