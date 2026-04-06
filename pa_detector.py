"""
pa_detector.py — Detector de patrones de Price Action
=====================================================
Detecta patrones de velas basados en pa_config.yaml:
  • Pin Bar (wick largo, body pequeño)
  • Engulfing (vela envuelve anterior)
  • Inside Bar (rango comprimido)
  • Shooting Star (pin bar bearish)
  • Hammer (pin bar bullish)

Parámetros calibrados visualmente basado en presentación de Eduardo.
"""

import os
import yaml
from typing import List, Dict, Optional
from .pa_signal_schema import PatternData

_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")


class PriceActionDetector:
    """Detecta patrones de velas según pa_config.yaml"""

    def __init__(self, config_path: str = _CONFIG_PATH):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.detectors = self.cfg['pattern_detectors']

    def detect_latest(self, bars: List[Dict]) -> Optional[PatternData]:
        """
        Detecta patrón en la última barra cerrada.
        Usado en trading en vivo (2m).

        Args:
            bars: Lista de barras dict con keys: open, high, low, close, volume

        Returns:
            PatternData con el patrón encontrado o None
        """
        if not bars or len(bars) < 2:
            return None

        # Última barra cerrada
        bar = bars[-1]
        index = len(bars) - 1

        # Intentar detectar cada patrón
        patterns = [
            ('pin_bar', self._detect_pin_bar(bars, index)),
            ('shooting_star', self._detect_shooting_star(bars, index)),
            ('hammer', self._detect_hammer(bars, index)),
            ('engulfing', self._detect_engulfing(bars, index)),
            ('inside_bar', self._detect_inside_bar(bars, index)),
        ]

        # Retornar patrón con mayor confianza
        valid_patterns = [p for p in patterns if p[1] is not None]
        if not valid_patterns:
            return None

        best_pattern = max(valid_patterns, key=lambda x: x[1].confidence)
        return best_pattern[1]

    def scan_bars(self, bars: List[Dict]) -> List[PatternData]:
        """
        Escanea todas las barras para encontrar todos los patrones.
        Usado en backtesting.

        Returns:
            Lista de PatternData encontrados
        """
        results = []
        for i in range(1, len(bars)):
            for pattern_name in ['pin_bar', 'shooting_star', 'hammer', 'engulfing', 'inside_bar']:
                detector = getattr(self, f'_detect_{pattern_name}', None)
                if detector:
                    pattern = detector(bars, i)
                    if pattern:
                        results.append(pattern)
        return results

    # ─────────────────────────────────────────────────────────────────────────
    # DETECTORES DE PATRÓN
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_pin_bar(self, bars: List[Dict], index: int) -> Optional[PatternData]:
        """Pin Bar: Wick largo + body pequeño"""
        if not self.detectors['pin_bar']['enabled']:
            return None

        bar = self._bar_to_obj(bars[index])
        cfg = self.detectors['pin_bar']

        # Validar ratios
        body_ratio = bar['body_ratio']
        upper_wick_ratio = bar['upper_wick'] / bar['range']
        lower_wick_ratio = bar['lower_wick'] / bar['range']

        # Pin bar puede ser bullish (wick abajo) o bearish (wick arriba)
        is_bullish_pin = (lower_wick_ratio >= cfg['wick_ratio'] and
                         body_ratio <= cfg['body_ratio'] and
                         bar['lower_wick'] / max(bar['body'], 0.01) >= cfg['wick_to_body_ratio'])

        is_bearish_pin = (upper_wick_ratio >= cfg['wick_ratio'] and
                         body_ratio <= cfg['body_ratio'] and
                         bar['upper_wick'] / max(bar['body'], 0.01) >= cfg['wick_to_body_ratio'])

        if is_bullish_pin:
            return PatternData(
                pattern_type='pin_bar',
                direction='bullish',
                confidence=cfg['confidence_weight'],
                wick_ratio=lower_wick_ratio,
                body_ratio=body_ratio,
                volume_ratio=bars[index].get('volume', 0) / self._avg_volume(bars),
                open=bar['open'],
                high=bar['high'],
                low=bar['low'],
                close=bar['close'],
                volume=bars[index].get('volume', 0),
                params_used=cfg
            )

        if is_bearish_pin:
            return PatternData(
                pattern_type='pin_bar',
                direction='bearish',
                confidence=cfg['confidence_weight'],
                wick_ratio=upper_wick_ratio,
                body_ratio=body_ratio,
                volume_ratio=bars[index].get('volume', 0) / self._avg_volume(bars),
                open=bar['open'],
                high=bar['high'],
                low=bar['low'],
                close=bar['close'],
                volume=bars[index].get('volume', 0),
                params_used=cfg
            )

        return None

    def _detect_shooting_star(self, bars: List[Dict], index: int) -> Optional[PatternData]:
        """Shooting Star: Pin bar bearish (wick arriba)"""
        if not self.detectors['shooting_star']['enabled']:
            return None

        bar = self._bar_to_obj(bars[index])
        cfg = self.detectors['shooting_star']

        upper_wick_pct = bar['upper_wick'] / bar['range']
        body_ratio = bar['body_ratio']
        lower_wick_pct = bar['lower_wick'] / bar['range']

        if (upper_wick_pct >= cfg['upper_wick_pct'] and
            body_ratio <= cfg['body_ratio'] and
            lower_wick_pct <= cfg['lower_wick_pct'] and
            bar['close'] < bar['open']):  # Debe cerrar más bajo que abre

            return PatternData(
                pattern_type='shooting_star',
                direction='bearish',
                confidence=cfg['confidence_weight'],
                wick_ratio=upper_wick_pct,
                body_ratio=body_ratio,
                volume_ratio=bars[index].get('volume', 0) / self._avg_volume(bars),
                open=bar['open'],
                high=bar['high'],
                low=bar['low'],
                close=bar['close'],
                volume=bars[index].get('volume', 0),
                params_used=cfg
            )

        return None

    def _detect_hammer(self, bars: List[Dict], index: int) -> Optional[PatternData]:
        """Hammer: Pin bar bullish (wick abajo)"""
        if not self.detectors['hammer']['enabled']:
            return None

        bar = self._bar_to_obj(bars[index])
        cfg = self.detectors['hammer']

        lower_wick_pct = bar['lower_wick'] / bar['range']
        body_ratio = bar['body_ratio']
        upper_wick_pct = bar['upper_wick'] / bar['range']

        if (lower_wick_pct >= cfg['lower_wick_pct'] and
            body_ratio <= cfg['body_ratio'] and
            upper_wick_pct <= cfg['upper_wick_pct'] and
            bar['close'] > bar['open']):  # Debe cerrar más alto que abre

            return PatternData(
                pattern_type='hammer',
                direction='bullish',
                confidence=cfg['confidence_weight'],
                wick_ratio=lower_wick_pct,
                body_ratio=body_ratio,
                volume_ratio=bars[index].get('volume', 0) / self._avg_volume(bars),
                open=bar['open'],
                high=bar['high'],
                low=bar['low'],
                close=bar['close'],
                volume=bars[index].get('volume', 0),
                params_used=cfg
            )

        return None

    def _detect_engulfing(self, bars: List[Dict], index: int) -> Optional[PatternData]:
        """Engulfing: Vela actual envuelve vela anterior"""
        if index < 1 or not self.detectors['engulfing']['enabled']:
            return None

        bar_curr = self._bar_to_obj(bars[index])
        bar_prev = self._bar_to_obj(bars[index - 1])
        cfg = self.detectors['engulfing']

        # Body actual debe ser >= 115% del anterior
        body_ratio = bar_curr['body'] / max(bar_prev['body'], 0.01)

        # Envolvimiento: high actual > high anterior Y low actual < low anterior
        is_engulfing = (bar_curr['high'] > bar_prev['high'] and
                       bar_curr['low'] < bar_prev['low'] and
                       body_ratio >= cfg['body_ratio'])

        if not is_engulfing:
            return None

        # Dirección: según cierre
        direction = 'bullish' if bar_curr['close'] > bar_curr['open'] else 'bearish'

        return PatternData(
            pattern_type='engulfing',
            direction=direction,
            confidence=cfg['confidence_weight'],
            wick_ratio=body_ratio,
            body_ratio=bar_curr['body_ratio'],
            volume_ratio=bars[index].get('volume', 0) / self._avg_volume(bars),
            open=bar_curr['open'],
            high=bar_curr['high'],
            low=bar_curr['low'],
            close=bar_curr['close'],
            volume=bars[index].get('volume', 0),
            params_used=cfg
        )

    def _detect_inside_bar(self, bars: List[Dict], index: int) -> Optional[PatternData]:
        """Inside Bar: Rango comprimido (inside bar)"""
        if index < 1 or not self.detectors['inside_bar']['enabled']:
            return None

        bar_curr = self._bar_to_obj(bars[index])
        bar_prev = self._bar_to_obj(bars[index - 1])
        cfg = self.detectors['inside_bar']

        # Rango actual <= 80% del anterior
        range_ratio = bar_curr['range'] / max(bar_prev['range'], 0.01)
        is_inside = (range_ratio <= cfg['range_ratio'] and
                    bar_curr['high'] <= bar_prev['high'] and
                    bar_curr['low'] >= bar_prev['low'] and
                    bar_curr['body'] >= cfg['min_body_ratio'] * bar_curr['range'])

        if not is_inside:
            return None

        return PatternData(
            pattern_type='inside_bar',
            direction='neutral',  # Inside bar es de compresión
            confidence=cfg['confidence_weight'],
            wick_ratio=range_ratio,
            body_ratio=bar_curr['body_ratio'],
            volume_ratio=bars[index].get('volume', 0) / self._avg_volume(bars),
            open=bar_curr['open'],
            high=bar_curr['high'],
            low=bar_curr['low'],
            close=bar_curr['close'],
            volume=bars[index].get('volume', 0),
            params_used=cfg
        )

    # ─────────────────────────────────────────────────────────────────────────
    # UTILIDADES
    # ─────────────────────────────────────────────────────────────────────────

    def _bar_to_obj(self, bar: Dict) -> Dict:
        """Convierte diccionario de barra a objeto con ratios calculados"""
        o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
        return {
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'range': h - l,
            'body': abs(c - o),
            'body_ratio': abs(c - o) / (h - l) if h > l else 0,
            'upper_wick': h - max(o, c),
            'lower_wick': min(o, c) - l,
            'is_bullish': c > o,
        }

    def _avg_volume(self, bars: List[Dict], lookback: int = 20) -> float:
        """Calcula volumen promedio de últimas N barras"""
        volumes = [bar.get('volume', 1000) for bar in bars[-lookback:]]
        return sum(volumes) / len(volumes) if volumes else 1000

    def reload_config(self):
        """Recarga configuración (post-calibración)"""
        with open(self._CONFIG_PATH) as f:
            self.cfg = yaml.safe_load(f)
        self.detectors = self.cfg['pattern_detectors']
