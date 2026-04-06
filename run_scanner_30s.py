#!/usr/bin/env python3
"""
SPX 30-segundo scanner — Loop en vivo
Uso: python run_scanner_30s.py
Ctrl+C para detener
"""

import sys
import time
from datetime import datetime
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

# Datos simulados de TradingView (en producción: usar IBClient real)
# Para ahora, usamos datos mock basados en la secuencia observada
MOCK_BARS_2M = [
    # Bar 28 (rechazo)
    {"open": 6600.06, "high": 6600.06, "low": 6595.53, "close": 6595.95, "volume": 4317210},
    # Bar 29 (segunda vela - confirmación)
    {"open": 6596.23, "high": 6598.93, "low": 6594.97, "close": 6598.84, "volume": 5053590},
    # Bar 30 (continuación)
    {"open": 6598.83, "high": 6599.75, "low": 6598.2, "close": 6599.45, "volume": 2707940},
]

CURRENT_PRICE = 6599.45


def simulate_quote_get():
    """Simula quote_get de TradingView"""
    return {"symbol": "SPX", "last": CURRENT_PRICE}


def simulate_data_get_ohlcv():
    """Simula data_get_ohlcv de TradingView"""
    return MOCK_BARS_2M


def run_scanner_cycle(iteration: int) -> dict:
    """
    Ejecuta un ciclo de escaneo completo.
    En producción: usar IBClient real para obtener datos.
    """
    try:
        # Paso 1: Obtener precio
        quote = simulate_quote_get()
        price = quote["last"]

        # Paso 2: Obtener barras
        bars = simulate_data_get_ohlcv()

        # Paso 3: Importar y ejecutar scanner
        from price_action_scanner import PriceActionScanner

        scanner = PriceActionScanner()

        # Ejecutar análisis
        signal = scanner.analyze(
            bars_1h=bars,  # Mock
            bars_5m=bars,  # Mock
            bars_2m=bars,  # Real data
            current_price=price,
            send_order=False,  # No enviar órdenes en demo
        )

        return {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "price": price,
            "signal": signal,
            "error": None,
        }

    except Exception as e:
        return {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
        }


def format_signal_summary(signal) -> str:
    """Formatea la señal para output conciso"""
    if not signal:
        return "sin señal"

    parts = []
    if hasattr(signal, "direction"):
        parts.append(f"Dirección: {signal.direction.upper()}")
    if hasattr(signal, "order_generated"):
        parts.append(f"Orden: {'SÍ' if signal.order_generated else 'NO'}")
    if hasattr(signal, "summary"):
        return signal.summary()

    return " | ".join(parts) if parts else str(signal)


def main():
    """Loop principal: 30 segundos entre scans"""

    print("\n" + "=" * 70)
    print("🚀 SPX 30-SEGUNDO SCANNER")
    print("=" * 70)
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Intervalo: 30 segundos")
    print("Ctrl+C para detener\n")

    iteration = 0
    last_signal = None
    scan_count = 0

    try:
        while True:
            iteration += 1
            scan_start = time.time()

            # Ejecutar ciclo de escaneo
            result = run_scanner_cycle(iteration)

            # Mostrar resultado
            ts = result["timestamp"]

            if result.get("error"):
                print(f"[{ts}] SCAN #{iteration}")
                print(f"  ❌ Error: {result['error']}\n")
            else:
                signal = result.get("signal")
                price = result.get("price")

                # Solo reportar si hay cambio significativo
                signal_changed = (signal is not None) != (last_signal is not None)

                if signal_changed or price != CURRENT_PRICE:
                    print(f"[{ts}] SCAN #{iteration}")
                    print(f"  📈 Precio: {price:.2f}")

                    if signal:
                        print(f"  ✅ SEÑAL: {format_signal_summary(signal)}")
                        last_signal = signal
                    else:
                        if last_signal is not None:
                            print(f"  ⚠️  Señal perdida")
                            last_signal = None

                    print()

                scan_count += 1

            # Esperar 30 segundos
            elapsed = time.time() - scan_start
            sleep_time = max(0.1, 30 - elapsed)

            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n\n🛑 Escaneo detenido")
        print(f"Total de scans ejecutados: {scan_count}")
        if last_signal:
            print(f"Última señal: {format_signal_summary(last_signal)}")


if __name__ == "__main__":
    main()
