#!/usr/bin/env python3
"""
SPX Scanner Infinito — Cada 30 segundos
Ejecutar en background: python scanner_infinite_30s.py &
"""
import sys
import time
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Simular datos en vivo con actualización dinámica
class TVData:
    """Simula datos de TradingView con cambios realistas"""

    prices = [6598.84, 6599.33, 6596.82, 6600.21, 6600.41, 6600.41, 6596.14, 6596.55, 6596.72]
    idx = 0

    @classmethod
    def next_price(cls):
        if cls.idx < len(cls.prices):
            price = cls.prices[cls.idx]
            cls.idx += 1
            return price
        # Loop: generar precio aleatorio cercano al último
        import random
        last = cls.prices[-1]
        change = random.uniform(-1, 1)
        return round(last + change, 2)

def analyze_signal(price, entry=6598.84):
    """Análisis simple del scanner"""
    pnl = price - entry
    target1, target2 = 6608, 6615
    stop_loss = 6594

    status = "✅ SAFE"
    if pnl < -2:
        status = "⚠️  RIESGO"
    if pnl < -4:
        status = "🔴 CRÍTICO"

    return {
        "entry": entry,
        "current": price,
        "pnl": pnl,
        "t1": target1,
        "t2": target2,
        "sl": stop_loss,
        "status": status,
    }

def main():
    print("\n" + "="*70)
    print("🚀 SPX SCANNER INFINITO — CADA 30 SEGUNDOS")
    print("="*70)
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Modo: Ejecutando indefinidamente hasta Ctrl+C\n")

    scan = 0
    last_report = None

    try:
        while True:
            scan += 1
            ts = datetime.now().strftime("%H:%M:%S")
            price = TVData.next_price()
            analysis = analyze_signal(price)

            # Reportar solo si hay cambio significativo (>= 0.5pts)
            price_changed = last_report and abs(price - last_report['current']) >= 0.5
            pnl_critical = analysis['pnl'] <= -2

            if price_changed or pnl_critical or scan <= 2:
                print(f"[SCAN #{scan} — {ts}]")
                print(f"  📈 Precio: {price:.2f}")
                print(f"  💰 P&L: {analysis['pnl']:+.2f}pts {analysis['status']}")

                if analysis['pnl'] > 0:
                    print(f"  🎯 Target 1: {analysis['t1']} (+{analysis['t1'] - price:.2f}pts)")
                    print(f"  🎯 Target 2: {analysis['t2']} (+{analysis['t2'] - price:.2f}pts)")
                else:
                    print(f"  ⚠️  Stop Loss: {analysis['sl']} ({analysis['sl'] - price:.2f}pts)")
                print()
            else:
                print(f"[SCAN #{scan} — {ts}] {price:.2f} | P&L: {analysis['pnl']:+.2f}pts")

            last_report = analysis

            # Esperar 30 segundos
            for i in range(30):
                time.sleep(1)
                if i == 0 or i == 14 or i == 29:
                    remaining = 30 - i
                    if remaining > 0:
                        pass  # Silent countdown

    except KeyboardInterrupt:
        print(f"\n\n🛑 Scanner detenido después de {scan} ciclos")
        print(f"Último precio: {TVData.prices[-1] if TVData.prices else 'N/A'}")
        print(f"Duración: {scan * 30 // 60}m {scan * 30 % 60}s")

if __name__ == "__main__":
    main()
