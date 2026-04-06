#!/usr/bin/env python3
"""
30-segundo loop scanner para SPX — Ejecución en vivo
Ejecutar: python scanner_30s_loop.py
"""

import asyncio
import sys
import time
from datetime import datetime
import json
from pathlib import Path

# Importar el scanner
sys.path.insert(0, str(Path(__file__).parent))
from price_action_scanner import PriceActionScanner

# Simulación de datos de TradingView (en producción, usar IBClient real)
class MockTVData:
    """Mock para simular datos de TradingView via quote_get y data_get_ohlcv"""

    def __init__(self):
        self.last_bars_1h = []
        self.last_bars_5m = []
        self.last_bars_2m = []
        self.last_price = None
        self.last_signal = None

    async def fetch_data(self):
        """Simula obtención de datos de TV (en producción: IBClient)"""
        # En producción: usar IBClient.get_historical_bars()
        # Aquí asumimos que los datos vienen de quote_get + data_get_ohlcv
        pass


async def scan_once(scanner, tv_data, iteration: int) -> dict:
    """
    Ejecuta un ciclo de escaneo.
    Retorna: { 'timestamp': ts, 'signal': signal_obj o None, 'price': float }
    """
    try:
        # Obtener datos actuales (en producción via IBClient)
        # Para demo: usar datos simulados o logs previos

        # En un caso real con IBClient:
        # bars_1h = await ib_client.get_historical_bars("SPX", "1h", 30)
        # bars_5m = await ib_client.get_historical_bars("SPX", "5m", 50)
        # bars_2m = await ib_client.get_historical_bars("SPX", "2m", 30)
        # quote = await ib_client.get_quote("SPX")

        # Por ahora, registrar que escaneo ocurrió
        ts = datetime.now().isoformat()
        result = {
            'timestamp': ts,
            'iteration': iteration,
            'signal': None,
            'price': None,
            'status': 'pending_data'
        }

        return result

    except Exception as e:
        print(f"❌ Error en scan #{iteration}: {e}")
        return {
            'timestamp': datetime.now().isoformat(),
            'iteration': iteration,
            'error': str(e)
        }


async def main():
    """Loop principal: 30 segundos entre scans"""

    print("=" * 70)
    print("SPX 30-SECOND SCANNER — Eduardo's Price Action Methodology")
    print("=" * 70)
    print()
    print("📊 Modo: LIVE SCANNING (requiere IBClient con datos en vivo)")
    print("⏱️  Intervalo: 30 segundos")
    print("📝 Reporta: Solo cambios significativos y nuevas señales")
    print()
    print("Para usar en producción:")
    print("  1. Inicializar IBClient con conexión IBKR")
    print("  2. Pasar ib_client al PriceActionScanner")
    print("  3. El scanner llamará get_historical_bars() automáticamente")
    print()
    print("-" * 70)
    print()

    # Inicializar scanner (sin IBClient por ahora)
    scanner = PriceActionScanner()

    iteration = 0
    last_signal_summary = None
    last_price = None
    scan_times = []

    try:
        while True:
            iteration += 1
            scan_start = time.time()

            # Ejecutar un ciclo de escaneo
            result = await scan_once(scanner, None, iteration)

            scan_time = time.time() - scan_start
            scan_times.append(scan_time)

            # Mostrar resultado solo si hay cambio significativo
            if result.get('signal') or result.get('price') != last_price:
                print(f"[{result['timestamp']}] SCAN #{iteration}")

                if result.get('signal'):
                    print(f"  ✅ SEÑAL DETECTADA: {result['signal']}")
                    last_signal_summary = result['signal']

                if result.get('price'):
                    print(f"  📈 Precio: {result['price']:.2f}")
                    last_price = result['price']

                if result.get('error'):
                    print(f"  ❌ Error: {result['error']}")

                print()

            # Esperar 30 segundos hasta el próximo scan
            elapsed = time.time() - scan_start
            sleep_time = max(0, 30 - elapsed)

            if sleep_time > 0:
                # Mostrar countdown cada 10 segundos
                remaining = sleep_time
                while remaining > 0:
                    wait_time = min(10, remaining)
                    time.sleep(wait_time)
                    remaining -= wait_time

                    if remaining > 0:
                        print(f"⏳ Próximo scan en {remaining:.0f}s...", end='\r')

    except KeyboardInterrupt:
        print("\n\n🛑 Escaneo detenido por el usuario")

        if scan_times:
            avg_scan_time = sum(scan_times) / len(scan_times)
            print(f"\n📊 Estadísticas de {iteration} scans:")
            print(f"   Tiempo promedio: {avg_scan_time:.3f}s")
            print(f"   Total ejecutado: {sum(scan_times):.1f}s")

        if last_signal_summary:
            print(f"\n✅ Última señal detectada: {last_signal_summary}")


if __name__ == "__main__":
    asyncio.run(main())
