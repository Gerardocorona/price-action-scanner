import sys
import os

# Añadir carpeta actual al path
sys.path.insert(0, os.getcwd())

# Importar módulos directamente
from pa_scanner import PriceActionScanner
from pa_detector import PriceActionDetector
from confluence_checker import ConfluenceChecker
from signal_generator import SignalGenerator
from pa_signal_schema import PriceActionSignal

print("✓ Scanner inicializado correctamente")
print("✓ Listo para analizar SPX")
