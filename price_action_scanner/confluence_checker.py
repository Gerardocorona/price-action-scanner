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

        # Detectar mercado lateral en 2m (más preciso — zona lateral visible en timeframe de entrada)
        # NOTA: En 5m una ventana de 20 barras = 100 min, rango siempre > 10pts
        #       En 2m una ventana de 20 barras = 40 min, rango lateral = 6-10pts (correcto)
        is_lateral, lateral_range = self._detect_lateral_market(bars_2m if bars_2m else bars_5m)

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

    def compute_bollinger(self, bars: List[Dict], length: int = 20, mult: float = 2.0) -> Dict:
        """
        Calcula Bollinger Bands a partir de barras.
        Returns: {'upper': float, 'basis': float, 'lower': float}
        """
        if len(bars) < length:
            closes = [b['close'] for b in bars]
        else:
            closes = [b['close'] for b in bars[-length:]]

        basis = sum(closes) / len(closes)
        variance = sum((c - basis) ** 2 for c in closes) / len(closes)
        std = variance ** 0.5
        return {
            'upper': basis + mult * std,
            'basis': basis,
            'lower': basis - mult * std,
        }

    def detect_range_position(
        self,
        current_price: float,
        bars_2m: List[Dict],
        resistance: Optional[float] = None,
    ) -> Dict:
        """
        Detecta la posición del precio dentro del rango operativo:
          Techo = resistencia confirmada (o BB Upper)
          Piso  = BB Basis (línea azul)

        Returns:
            {
                'range_top': float,
                'range_bottom': float,
                'range_size': float,
                'position': 'at_top' | 'at_bottom' | 'middle',
                'bb': {'upper', 'basis', 'lower'},
                'pct_in_range': float (0.0=bottom, 1.0=top),
            }
        """
        bb = self.compute_bollinger(bars_2m)
        range_top = resistance if resistance else bb['upper']
        range_bottom = bb['basis']
        range_size = range_top - range_bottom

        if range_size <= 0:
            pct = 0.5
        else:
            pct = (current_price - range_bottom) / range_size

        zone_pct = self.cfg.get('range_trading', {}).get('zone_pct', 0.15)
        if pct >= (1.0 - zone_pct):
            position = 'at_top'
        elif pct <= zone_pct:
            position = 'at_bottom'
        else:
            position = 'middle'

        return {
            'range_top': range_top,
            'range_bottom': range_bottom,
            'range_size': range_size,
            'position': position,
            'bb': bb,
            'pct_in_range': pct,
        }

    def check(
        self,
        pattern: PatternData,
        trend: TrendContext,
        current_price: float,
        bars_5m: Optional[List[Dict]] = None,
        bars_2m: Optional[List[Dict]] = None,
        resistance: Optional[float] = None,
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

        # ─ FACTOR 9: POSICIÓN EN RANGO BB (techo/piso) ─────────────────
        if bars_2m and len(bars_2m) >= 5:
            rng = self.detect_range_position(current_price, bars_2m, resistance)
            range_weights = self.confluence_cfg.get('factor_weights', {})

            # PUT: precio en techo del rango + patrón bearish
            if rng['position'] == 'at_top' and pattern.direction == 'bearish':
                peso = range_weights.get('range_at_extreme', 1.8)
                confluence.factors.append(
                    f"range_at_top_{rng['range_top']:.0f}"
                )
                confluence.score += peso
                confluence.weights_applied['range_at_extreme'] = peso

            # CALL: precio en piso del rango + patrón bullish
            elif rng['position'] == 'at_bottom' and pattern.direction == 'bullish':
                peso = range_weights.get('range_at_extreme', 1.8)
                confluence.factors.append(
                    f"range_at_bottom_{rng['range_bottom']:.0f}"
                )
                confluence.score += peso
                confluence.weights_applied['range_at_extreme'] = peso

        # ─ FACTOR 10: BULL/BEAR TRAP PATTERN ───────────────────────────
        if pattern.pattern_type in ('bull_trap', 'bear_trap'):
            peso = range_weights.get('trap_pattern', 2.0) if bars_2m else 2.0
            confluence.factors.append(f"trap_{pattern.pattern_type}")
            confluence.score += peso
            confluence.weights_applied['trap_pattern'] = peso

        # ─ FACTOR 11: SEGUNDA VELA CONFIRMACIÓN ────────────────────────
        if pattern.pattern_type == 'second_candle':
            peso = range_weights.get('second_candle_confirm', 1.5) if bars_2m else 1.5
            confluence.factors.append(f"second_candle_{pattern.direction}")
            confluence.score += peso
            confluence.weights_applied['second_candle_confirm'] = peso

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

        # Barras directivas (body > threshold = movimiento con dirección real)
        # CALIBRADO: En SPX, body de 0.5pt es ruido. Threshold real = 3.0pts (del config)
        body_threshold = self.lateral_cfg.get('directional_body_threshold', 3.0)
        directional = 0
        for bar in bars_to_check:
            body = abs(bar['close'] - bar['open'])
            if body > body_threshold:
                directional += 1

        directional_pct = directional / len(bars_to_check)

        is_lateral = (
            total_range <= self.lateral_cfg['max_range_points'] and
            directional_pct < self.lateral_cfg['min_directional_bars_pct']
        )

        return is_lateral, total_range

    def detect_breakout_from_lateral(self, bars: List[Dict]) -> Tuple[bool, Optional[str]]:
        """
        Detecta ruptura de lateralidad confirmada con vela envolvente (engulfing).

        Reglas:
        1. Detectar si está en lateral
        2. Última barra debe ser vela envolvente (engulfing)
        3. Dirección de la ruptura

        Returns:
            (breakout_confirmed, direction) donde direction es 'bullish' o 'bearish'
        """
        if len(bars) < 2:
            return False, None

        is_lateral, _ = self._detect_lateral_market(bars[:-1])  # Checamos lateralidad sin la última vela
        if not is_lateral:
            return False, None

        # Última barra (la que rompe)
        last_bar = bars[-1]
        prev_bar = bars[-2]

        # Vela envolvente alcista (engulfing bullish)
        if (last_bar['open'] <= prev_bar['close'] and
            last_bar['close'] > prev_bar['open'] and
            last_bar['close'] > prev_bar['close']):
            return True, 'bullish'

        # Vela envolvente bajista (engulfing bearish)
        if (last_bar['open'] >= prev_bar['close'] and
            last_bar['close'] < prev_bar['open'] and
            last_bar['close'] < prev_bar['close']):
            return True, 'bearish'

        return False, None

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
