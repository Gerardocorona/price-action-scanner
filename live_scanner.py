#!/usr/bin/env python3
"""
SPX Live Scanner — Loop cada 30 segundos
Ejecutar: python live_scanner.py
"""
import sys
import time
import json
from datetime import datetime
from pathlib import Path

# Setup
sys.path.insert(0, str(Path(__file__).parent))

# Datos en vivo simulados desde TradingView (actualizar manualmente o vía API)
class LiveData:
    """Simula datos en vivo de TradingView"""

    # Precio actual actualizado
    current_price = 6600.73
    current_time = 1775488080

    # Barras 2m más recientes (últimas 5)
    recent_bars = [
        # Bar 16: Second candle (confirmación)
        {"t": 1775487840, "o": 6596.23, "h": 6598.93, "l": 6594.97, "c": 6598.84, "v": 5053590},
        # Bar 17
        {"t": 1775487900, "o": 6598.83, "h": 6600.81, "l": 6598.2,  "c": 6599.33, "v": 4222300},
        # Bar 18
        {"t": 1775487960, "o": 6599.3,  "h": 6599.78, "l": 6596.81, "c": 6596.82, "v": 3289520},
        # Bar 19
        {"t": 1775488020, "o": 6597.03, "h": 6600.3,  "l": 6595.37, "c": 6600.21, "v": 3763920},
        # Bar 20 (actual)
        {"t": 1775488080, "o": 6600.36, "h": 6600.98, "l": 6600.36, "c": 6600.73, "v": 903020},
    ]

def get_quote():
    """Simula quote_get"""
    return {
        "symbol": "SPX",
        "last": LiveData.current_price,
        "time": LiveData.current_time,
    }

def get_bars():
    """Simula data_get_ohlcv"""
    return LiveData.recent_bars

def analyze_bars(bars, price):
    """Análisis simple del scanner"""
    if len(bars) < 2:
        return None

    last_bar = bars[-1]
    prev_bar = bars[-2]

    # Detectar segunda vela después de rechazo
    is_second_candle = False
    if prev_bar['c'] < prev_bar['o']:  # Barra anterior bajista
        if last_bar['c'] > last_bar['o']:  # Barra actual alcista
            body_last = abs(last_bar['c'] - last_bar['o'])
            if body_last > (last_bar['h'] - last_bar['l']) * 0.40:
                is_second_candle = True

    # Estado general
    entry_price = 6598.84
    pnl = price - entry_price
    target1 = 6608
    target2 = 6615

    return {
        "pattern": "SECOND_CANDLE" if is_second_candle else None,
        "entry": entry_price,
        "current": price,
        "pnl": pnl,
        "direction": "CALL",
        "target1": target1,
        "target2": target2,
        "distance_t1": target1 - price,
        "active": pnl > -2,  # Sigue activo si no está mas de 2pts en pérdida
    }

def format_report(scan_num, result):
    """Formatea output conciso"""
    ts = datetime.now().strftime("%H:%M:%S")

    print(f"\n[SCAN #{scan_num} — {ts}]")
    print(f"  📈 Precio: {result['current']:.2f}")
    print(f"  💰 P&L: {result['pnl']:+.2f}pts")

    if result['active']:
        print(f"  ✅ CALL activa")
        print(f"     Target 1: {result['target1']} ({result['distance_t1']:+.2f}pts)")
        print(f"     Target 2: {result['target2']} ({result['target2'] - result['current']:+.2f}pts)")
    else:
        print(f"  ⚠️  CALL riesgo — SL: 6594")

    if result['pattern']:
        print(f"  🎯 Patrón: {result['pattern']}")

def main():
    print("\n" + "="*70)
    print("🚀 SPX LIVE SCANNER — 30 SEGUNDOS")
    print("="*70)
    print("Ejecutando cada 30 segundos | Ctrl+C para detener\n")

    scan_count = 0
    last_price = None
    last_signal = None

    try:
        while True:
            scan_count += 1

            # Obtener datos
            quote = get_quote()
            bars = get_bars()

            # Analizar
            result = analyze_bars(bars, quote['last'])

            # Reportar solo si hay cambio significativo
            price_changed = (last_price is not None and
                           abs(quote['last'] - last_price) >= 0.5)
            signal_changed = (result['pattern'] if result else None) != last_signal

            if price_changed or signal_changed or scan_count <= 2:
                format_report(scan_count, result)
            else:
                # Sin cambios significativos
                print(f"[SCAN #{scan_count} — {datetime.now().strftime('%H:%M:%S')}] Sin cambios | {quote['last']:.2f}")

            last_price = quote['last']
            last_signal = result['pattern'] if result else None

            # Esperar 30 segundos
            print("⏳ Esperando 30 segundos...", end="", flush=True)
            for i in range(30):
                time.sleep(1)
                if i % 10 == 9:
                    print(f" {30-i-1}s", end="", flush=True)
            print()

    except KeyboardInterrupt:
        print("\n\n🛑 Scanner detenido")
        print(f"Total scans: {scan_count}")

if __name__ == "__main__":
    main()
