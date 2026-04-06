import asyncio
import sys
import os
from datetime import datetime

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print('Instala dependencias: pip install yfinance pandas')
    sys.exit(1)

from price_action_scanner import PriceActionScanner


def download_bars(symbol, period, interval):
    df = yf.download(symbol, period=period, interval=interval, progress=False)
    if df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            'datetime': str(idx),
            'open': float(row['Open']),
            'high': float(row['High']),
            'low': float(row['Low']),
            'close': float(row['Close']),
            'volume': int(row['Volume']) if pd.notna(row['Volume']) else 0,
        })
    return bars


async def scan_once(scanner):
    print('  Descargando datos SPX...')
    bars_1h = download_bars('^GSPC', '5d', '1h')
    bars_5m = download_bars('^GSPC', '5d', '5m')
    bars_2m = download_bars('^GSPC', '5d', '2m')

    if not bars_1h or not bars_5m or not bars_2m:
        print('  No hay datos disponibles')
        return None

    current_price = bars_2m[-1]['close']

    print(f'  1H: {len(bars_1h)} barras | 5m: {len(bars_5m)} barras | 2m: {len(bars_2m)} barras')
    print(f'  Precio actual: ')
    print(f'  Ultima vela 2m: {bars_2m[-1]["datetime"]}')
    print()
    print('  Analizando price action...')

    signal = await scanner.analyze(
        bars_1h=bars_1h,
        bars_5m=bars_5m,
        bars_2m=bars_2m,
        current_price=current_price,
        send_order=False,
    )

    print('-' * 64)
    if signal:
        print()
        print(f'  SENAL DETECTADA')
        print(f'  Patron:      {signal.pattern.pattern_type}')
        print(f'  Direccion:   {signal.pattern.direction}')
        print(f'  Confianza:   {signal.pattern.confidence:.0%}')
        print(f'  Confluencia: {signal.confluence.factors_count} factores')
        print(f'  Precio:      ')
        if signal.order:
            print(f'  ACCION:    {signal.order.side}')
            print(f'  Entry:     ')
            print(f'  Stop:      ')
            print(f'  Target:    ')
        if signal.confluence.meets_minimum:
            print(f'  CONFLUENCIA VALIDA - CONSIDERAR ENTRADA EN IBKR')
        else:
            print(f'  Confluencia insuficiente - NO ENTRAR')
    else:
        print()
        print('  Sin senal - esperando setup...')
        last = bars_2m[-1]
        print(f'    O: {last["open"]:.2f}  H: {last["high"]:.2f}  L: {last["low"]:.2f}  C: {last["close"]:.2f}')
    print()
    return signal


async def main():
    print('=' * 64)
    print('  PRICE ACTION SCANNER - SPX 0DTE')
    print('  Metodologia: Eduardo (PRN-Million plus)')
    print('  Modo: MANUAL (senales en pantalla, ejecutar en IBKR)')
    print('=' * 64)

    scanner = PriceActionScanner()
    cycle = 0

    while True:
        cycle += 1
        print(f'\n  Ciclo #{cycle} | {datetime.now().strftime("%H:%M:%S")}')
        print()
        await scan_once(scanner)

        stats = scanner.get_session_stats()
        print(f'  Sesion: {stats["signals_detected"]} detectadas | {stats["signals_sent"]} enviadas | {stats["signals_rejected"]} rechazadas')
        print(f'  Proximo escaneo en 120s... (Ctrl+C para detener)')

        try:
            await asyncio.sleep(120)
        except asyncio.CancelledError:
            break


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n  Scanner detenido.')
