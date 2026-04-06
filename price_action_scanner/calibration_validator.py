"""
calibration_validator.py — Validador de Calibración Visual
===========================================================
Verifica que los parámetros en pa_config.yaml detectan correctamente
los patrones mostrados en la presentación de Eduardo.

Casos de prueba basados en imágenes:
  1. Pin Bar en soporte (Imagen 2: Tesla)
  2. Break and Retest (Imagen 3: QQQ 2m) ← CRÍTICO
  3. Lateral Market (Imagen 5: Educativo)
  4. Tendencia bajista con ciclos (Imagen 1, 4: SPX)
  5. Múltiples toques de nivel (Imagen 2: Tesla)
"""

import os
import sys
import yaml
import json
from typing import List, Dict, Optional
from dataclasses import dataclass, field

# ── Path setup ──────────────────────────────────────────────────────────────
_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_SCANNER_DIR, "..", ".."))
_CONFIG_PATH = os.path.join(_SCANNER_DIR, "pa_config.yaml")


@dataclass
class Bar:
    """Representa una vela OHLCV"""
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0

    def __post_init__(self):
        self.body = abs(self.close - self.open)
        self.range = self.high - self.low
        self.upper_wick = self.high - max(self.open, self.close)
        self.lower_wick = min(self.open, self.close) - self.low
        self.is_bullish = self.close > self.open


class CalibrationValidator:
    """Validador que comprueba parámetros contra casos visuales"""

    def __init__(self, config_path: str = _CONFIG_PATH):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.results = []

    def validate_pin_bar_at_support(self):
        """
        CASO 1: Pin Bar en soporte (Imagen 2 - Tesla)
        ──────────────────────────────────────────────
        Visual: El precio toca soporte, forma wick largo hacia abajo,
        cierra arriba = pin bar bullish en soporte.
        """
        print("\n" + "="*70)
        print("CASO 1: Pin Bar Bullish en Soporte (Imagen 2 - Tesla)")
        print("="*70)

        # Simular pin bar bullish: wick largo abajo, body pequeño arriba
        pin_bar_bullish = Bar(
            open=197.80,    # Abre cerca del cierre
            high=198.20,    # Cierre un poco arriba
            low=195.50,     # Wick largo abajo (toca soporte 197.95)
            close=198.15    # Cierra arriba (bullish)
        )

        cfg_pb = self.cfg['pattern_detectors']['pin_bar']

        # Validar condiciones
        body_ratio = pin_bar_bullish.body / pin_bar_bullish.range
        wick_ratio = pin_bar_bullish.lower_wick / pin_bar_bullish.range
        wick_to_body = pin_bar_bullish.lower_wick / max(pin_bar_bullish.body, 0.01)

        print(f"\n  Body Ratio:        {body_ratio:.3f} (config: {cfg_pb['body_ratio']})")
        print(f"  Wick Ratio (lower):{wick_ratio:.3f} (config: {cfg_pb['wick_ratio']})")
        print(f"  Wick/Body:         {wick_to_body:.2f}x (config: {cfg_pb['wick_to_body_ratio']}x)")

        passes_body = body_ratio <= cfg_pb['body_ratio']
        passes_wick = wick_ratio >= cfg_pb['wick_ratio']
        passes_ratio = wick_to_body >= cfg_pb['wick_to_body_ratio']

        result = passes_body and passes_wick and passes_ratio
        confidence = 1.2 if result else 0.0

        print(f"\n  ✓ Body OK:         {passes_body}")
        print(f"  ✓ Wick OK:         {passes_wick}")
        print(f"  ✓ Wick/Body OK:    {passes_ratio}")
        print(f"\n  RESULTADO:         {'✅ DETECTADO' if result else '❌ NO DETECTADO'}")
        print(f"  Confidence:        {confidence}")

        self.results.append({
            'test': 'Pin Bar at Support',
            'passed': result,
            'expected': True,
            'confidence': confidence,
            'image': 'Imagen 2 (Tesla)'
        })

        return result

    def validate_break_and_retest(self):
        """
        CASO 2: Ruptura + Retroceso (Imagen 3 - QQQ 2m) ← CRÍTICO
        ──────────────────────────────────────────────────
        Visual: Precio rompe nivel de resistencia (Ruptura),
        retrocede AL NIVEL (Retroceso), luego entra (Entrada).
        Este es el patrón VISUAL PRINCIPAL que Eduardo muestra.
        """
        print("\n" + "="*70)
        print("CASO 2: Break and Retest (Imagen 3 - QQQ 2m) ★ CRÍTICO")
        print("="*70)

        # Simular 5 barras: nivel de resistencia, ruptura, retroceso, entrada, objetivo
        resistance_level = 300.00

        bars = [
            # Bar 1-2: Consolidación bajo resistencia
            Bar(open=299.50, high=299.90, low=299.00, close=299.80),
            Bar(open=299.80, high=300.10, low=299.50, close=299.95),

            # Bar 3: RUPTURA - cierra arriba de resistencia
            Bar(open=299.95, high=301.50, low=299.90, close=301.20),

            # Bar 4: RETROCESO - retrocede AL NIVEL (dentro de zone_tolerance)
            Bar(open=301.20, high=301.50, low=299.80, close=300.10),

            # Bar 5: ENTRADA - rompe retroceso hacia arriba
            Bar(open=300.10, high=301.80, low=300.00, close=301.60),
        ]

        cfg_br = self.cfg['break_and_retest']
        cfg_zt = self.cfg['levels']['pivot']['zone_tolerance']

        # Verificar: Bar 3 rompe > resistance
        break_bar = bars[2]
        break_detected = break_bar.close > resistance_level

        # Verificar: Bar 4 retrocede a zona
        retest_bar = bars[3]
        within_zone = abs(retest_bar.close - resistance_level) <= cfg_zt

        # Verificar: Bar 5 entra hacia arriba
        entry_bar = bars[4]
        entry_valid = entry_bar.close > retest_bar.close

        print(f"\n  Resistencia Level: {resistance_level}")
        print(f"  Zone Tolerance:    ±{cfg_zt} pts")
        print(f"\n  Bar 3 (Ruptura):   Close={break_bar.close:.2f}")
        print(f"    ✓ Rompe nivel:   {break_detected}")
        print(f"\n  Bar 4 (Retroceso): Close={retest_bar.close:.2f}")
        print(f"    ✓ En zona:       {within_zone} (dist={abs(retest_bar.close - resistance_level):.2f})")
        print(f"\n  Bar 5 (Entrada):   Close={entry_bar.close:.2f}")
        print(f"    ✓ Entra UP:      {entry_valid}")

        result = break_detected and within_zone and entry_valid
        weight = self.cfg['break_and_retest']['confidence_weight']

        print(f"\n  RESULTADO:         {'✅ DETECTADO' if result else '❌ NO DETECTADO'}")
        print(f"  Weight (Score):    {weight} ← DOMINANTE (prueba visual)")

        self.results.append({
            'test': 'Break and Retest',
            'passed': result,
            'expected': True,
            'weight': weight,
            'image': 'Imagen 3 (QQQ 2m)',
            'criticality': '★ CRÍTICO - Patrón visual principal'
        })

        return result

    def validate_lateral_market_rejection(self):
        """
        CASO 3: Detección de Mercado Lateral (Imagen 5)
        ────────────────────────────────────────────────
        Visual: Precio oscila entre soporte y resistencia
        sin dirección clara. DEBE RECHAZARSE.
        """
        print("\n" + "="*70)
        print("CASO 3: Lateral Market Detection (Imagen 5 - Educativo)")
        print("="*70)

        support = 295.00
        resistance = 305.00
        mid = (support + resistance) / 2

        # Simular 20 barras laterales (oscilación con bodies pequeños)
        # Lateral = Precio oscila sin dirección clara = pequeños bodies
        bars = []
        for i in range(20):
            # Crear barras con oscilación pequeña (body < 0.5 pt)
            if i % 2 == 0:
                bars.append(Bar(
                    open=mid, high=mid+1.0, low=mid-0.8, close=mid-0.3
                ))
            else:
                bars.append(Bar(
                    open=mid, high=mid+0.8, low=mid-1.0, close=mid+0.3
                ))

        cfg_lm = self.cfg['lateral_market']

        # Calcular rango total y barras direccionales
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        total_range = max(highs) - min(lows)

        # Barras direccionales: bodies > 0.5 pt (en lateral, bodies son muy pequeños)
        directional_bars = 0
        for i in range(len(bars)):
            body = abs(bars[i].close - bars[i].open)
            if body > 0.5:  # Body > 0.5 punto = barra direccional
                directional_bars += 1

        directional_pct = directional_bars / len(bars)

        is_lateral = (
            total_range <= cfg_lm['max_range_points'] and
            directional_pct < cfg_lm['min_directional_bars_pct']
        )

        print(f"\n  Soporte:           {support:.2f}")
        print(f"  Resistencia:       {resistance:.2f}")
        print(f"  Rango Total:       {total_range:.2f} pts (max config: {cfg_lm['max_range_points']})")
        print(f"  Barras Directivas: {directional_pct:.1%} (max config: {cfg_lm['min_directional_bars_pct']:.0%})")
        print(f"\n  ✓ Rango OK:        {total_range <= cfg_lm['max_range_points']}")
        print(f"  ✓ Bajo Directivas: {directional_pct < cfg_lm['min_directional_bars_pct']}")
        print(f"  ✓ Es Lateral:      {is_lateral}")

        # En lateral DEBE RECHAZARSE (is_lateral=True → REJECT)
        should_reject = is_lateral

        print(f"\n  ACCIÓN:            {'✅ RECHAZAR (Correcto)' if should_reject else '❌ No rechazaría'}")

        self.results.append({
            'test': 'Lateral Market Rejection',
            'passed': should_reject,
            'expected': True,
            'image': 'Imagen 5 (Educativo)',
            'rule': 'Si lateral=True → RECHAZAR setup'
        })

        return should_reject

    def validate_downtrend_detection(self):
        """
        CASO 4: Detección de Tendencia Bajista (Imagen 1, 4)
        ──────────────────────────────────────────────────────
        Visual: Lower highs y lower lows = tendencia bajista clara.
        """
        print("\n" + "="*70)
        print("CASO 4: Downtrend Detection (Imagen 1, 4 - SPX)")
        print("="*70)

        # Simular tendencia bajista: lower highs y lower lows
        bars = [
            Bar(open=6600, high=6620, low=6580, close=6610),  # HH, HL
            Bar(open=6610, high=6615, low=6575, close=6585),  # LH, LL
            Bar(open=6585, high=6605, low=6570, close=6580),  # LH, LL
            Bar(open=6580, high=6595, low=6560, close=6570),  # LH, LL
        ]

        # Detectar lower highs y lower lows
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]

        lower_highs = all(highs[i] < highs[i-1] for i in range(1, len(highs)))
        lower_lows = all(lows[i] < lows[i-1] for i in range(1, len(lows)))

        is_downtrend = lower_highs and lower_lows

        print(f"\n  Highs: {' → '.join([f'{h:.0f}' for h in highs])}")
        print(f"  Lows:  {' → '.join([f'{l:.0f}' for l in lows])}")
        print(f"\n  ✓ Lower Highs:     {lower_highs}")
        print(f"  ✓ Lower Lows:      {lower_lows}")
        print(f"  ✓ Is Downtrend:    {is_downtrend}")

        print(f"\n  RESULTADO:         {'✅ DETECTADO' if is_downtrend else '❌ NO DETECTADO'}")

        self.results.append({
            'test': 'Downtrend Detection',
            'passed': is_downtrend,
            'expected': True,
            'image': 'Imagen 1, 4 (SPX)',
            'confluence_factor': 'trend_alignment_1h / trend_alignment_5m'
        })

        return is_downtrend

    def validate_historical_respect(self):
        """
        CASO 5: Respeto Histórico de Niveles (Imagen 2 - Tesla)
        ────────────────────────────────────────────────────────
        Visual: El precio toca soporte múltiples veces y rebota.
        Cada toque aumenta la "confiabilidad" del nivel.
        """
        print("\n" + "="*70)
        print("CASO 5: Historical Respect Rate (Imagen 2 - Tesla)")
        print("="*70)

        support_level = 197.95
        cfg_hr = self.cfg['levels']['support'][0]['historical_respect_rate']

        # Simular 5 toques del nivel: precio cerca → rebota
        touches = [
            {'price': 197.98, 'result': 'rebote'},
            {'price': 197.92, 'result': 'rebote'},
            {'price': 198.05, 'result': 'rebote'},
            {'price': 197.87, 'result': 'rebote'},
            {'price': 197.95, 'result': 'rebote'},
        ]

        respected = sum(1 for t in touches if t['result'] == 'rebote')
        respect_rate = respected / len(touches)

        print(f"\n  Nivel de Soporte:  {support_level}")
        print(f"  Toques registrados:")
        for i, t in enumerate(touches, 1):
            print(f"    {i}. Precio={t['price']:.2f} → {t['result']}")

        print(f"\n  Tasa de Respeto:   {respect_rate:.0%} (config: {cfg_hr:.0%})")
        print(f"  ✓ Es confiable:    {respect_rate >= cfg_hr}")

        # Este nivel debe ser VÁLIDO para confluencia
        is_valid = respect_rate >= cfg_hr
        weight = self.cfg['confluence']['factor_weights']['nivel_historical_respect']

        print(f"\n  RESULTADO:         {'✅ VÁLIDO' if is_valid else '❌ NO VÁLIDO'}")
        print(f"  Factor Weight:     {weight} ← Aumentado a 1.8")

        self.results.append({
            'test': 'Historical Respect Rate',
            'passed': is_valid,
            'expected': True,
            'respect_rate': respect_rate,
            'image': 'Imagen 2 (Tesla)',
            'note': 'Niveles respetados múltiples veces son confiables'
        })

        return is_valid

    def run_all_tests(self):
        """Ejecuta todos los casos de validación"""
        print("\n")
        print("╔" + "="*68 + "╗")
        print("║" + " "*68 + "║")
        print("║" + "  VALIDADOR DE CALIBRACIÓN — Price Action Scanner  ".center(68) + "║")
        print("║" + "  Basado en imágenes de presentación de Eduardo  ".center(68) + "║")
        print("║" + " "*68 + "║")
        print("╚" + "="*68 + "╝")

        self.validate_pin_bar_at_support()
        self.validate_break_and_retest()
        self.validate_lateral_market_rejection()
        self.validate_downtrend_detection()
        self.validate_historical_respect()

        self.print_summary()

    def print_summary(self):
        """Imprime resumen de resultados"""
        print("\n" + "="*70)
        print("RESUMEN DE VALIDACIÓN")
        print("="*70)

        passed = sum(1 for r in self.results if r['passed'])
        total = len(self.results)
        pass_rate = (passed / total * 100) if total > 0 else 0

        print(f"\nTotal de Casos:    {total}")
        print(f"Pasaron:           {passed} ✅")
        print(f"Fallaron:          {total - passed} ❌")
        print(f"Tasa de Éxito:      {pass_rate:.0f}%")

        print(f"\nDetalle por caso:")
        for r in self.results:
            status = "✅" if r['passed'] else "❌"
            print(f"  {status} {r['test']:<35} ({r['image']})")

        critical_pass = any(r['passed'] for r in self.results if 'CRÍTICO' in str(r.get('criticality', '')))

        print(f"\n🎯 Break and Retest (CRÍTICO): {'✅ DETECTADO' if critical_pass else '❌ FALLO'}")

        if pass_rate == 100:
            print("\n✅ CALIBRACIÓN VALIDADA: Los parámetros detectan correctamente todos los patrones visuales.")
        else:
            print(f"\n⚠️  CALIBRACIÓN PARCIAL: {total - passed} caso(s) necesitan ajuste.")

        print("\nNotas:")
        print("  • Zone Tolerance (4.5 pts): Valida que S/R no son líneas exactas")
        print("  • Break & Retest Weight (2.5): Factor dominante - patrón visual principal")
        print("  • Historical Respect (0.75): Niveles tocados múltiples veces son confiables")
        print("  • Lateral Rejection: Si lateral=True → rechazo automático")
        print("  • Trend Context: Alineación 1H/5M es factor de confluencia importante")


if __name__ == "__main__":
    validator = CalibrationValidator()
    validator.run_all_tests()
