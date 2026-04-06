# Estando en la carpeta del scanner
cd C:\Users\gecor\price-action-scanner

# Crea el archivo README.md
cat > README.md << 'EOF'
# Price Action Scanner — SPX 0DTE Options

**Detector automático de patrones price action para opciones SPX 0DTE**

Metodología: Eduardo (PRN-Million plus)

## 📋 Descripción

Este scanner implementa un sistema completo para:
- Detección automática de patrones price action (pin bars, break & retest, laterales, etc.)
- Validación de confluencia múltiple
- Generación de señales de trading
- Calibración automática de parámetros
- Backtesting histórico
- Simulación Monte Carlo
- Optimización de estrategia
- Reportes visuales de performance

## 🚀 Instalación

### Opción 1: Clonar e instalar
```bash
git clone https://github.com/Gerardocorona/price-action-scanner.git
cd price-action-scanner
pip install -e .

Opción 2: Requisitos manuales
pip install pandas yfinance pyyaml numpy

💻 Uso Básico
from price_action_scanner import (
    PriceActionScanner,
    PriceActionBacktester,
    MonteCarloSimulator,
)

# Crear scanner
scanner = PriceActionScanner()

# Analizar barras
signal = scanner.analyze(bars)

# Ver señal
if signal:
    print(f"Patrón: {signal.pattern.name}")
    print(f"Confluencia: {signal.confluence.score}")

📦 Módulos Principales
Core Engines
PriceActionScanner - Motor principal de análisis
PriceActionDetector - Detección de patrones
ConfluenceChecker - Validación de confluencia
SignalGenerator - Generación de señales
Calibration & Optimization
PriceActionCalibrator - Grid search automático de parámetros
PriceActionOptimizer - Optimización de estrategia
PriceActionLabelingTool - Etiquetado manual de patrones
CalibrationValidator - Validación de configuración
Backtesting & Simulation
PriceActionBacktester - Backtesting histórico con datos de yfinance
MonteCarloSimulator - Simulación probabilística
CompoundMonteCarloSimulator - Simulación compuesta
Reporting
PriceActionReportGenerator - Generación de reportes HTML
🔧 Data Schemas
from price_action_scanner import (
    PatternData,
    TrendContext,
    ConfluenceData,
    OrderData,
    PriceActionSignal,
    CalibrationLabel,
)

📊 Ejemplo: Backtesting
from price_action_scanner import PriceActionBacktester

backtester = PriceActionBacktester()
result = backtester.backtest(
    symbol="SPX",
    start_date="2026-03-01",
    end_date="2026-04-06"
)

print(f"Win Rate: {result.win_rate:.2%}")
print(f"Total Trades: {result.total_trades}")
print(f"P&L: ${result.total_pnl:.2f}")

📈 Ejemplo: Monte Carlo Simulation
from price_action_scanner import MonteCarloSimulator

mc = MonteCarloSimulator()
result = mc.simulate(
    historical_trades=trades,
    simulations=10000,
    confidence_level=0.95
)

print(f"Expected Return: {result.expected_return:.2%}")
print(f"Max Drawdown (95%): {result.max_drawdown_ci95:.2%}")

⚙️ Configuración
Los parámetros del scanner se definen en pa_config.yaml:

Umbrales de detección de patrones
Criterios de confluencia
Parámetros de señal
Edita este archivo para ajustar la sensibilidad del detector.

📝 Requisitos
Python >= 3.8
pandas
yfinance
pyyaml
numpy

🤝 Contribuir
Para mejorar el scanner:

Crea una rama nueva: git checkout -b feature/mi-mejora
Haz tus cambios y commits
Sube la rama: git push origin feature/mi-mejora
Abre un Pull Request
📚 Estructura del Proyecto
price-action-scanner/
├── __init__.py                    # Exportaciones principales
├── pa_signal_schema.py            # Esquemas de datos
├── pa_detector.py                 # Detector de patrones
├── confluence_checker.py          # Validador de confluencia
├── signal_generator.py            # Generador de señales
├── pa_scanner.py                  # Motor principal
├── pa_calibrator.py              # Calibración automática
├── pa_backtester.py              # Backtesting histórico
├── pa_montecarlo.py              # Simulación Monte Carlo
├── pa_montecarlo_compound.py     # Simulación compuesta
├── pa_optimizer.py               # Optimización
├── pa_report_generator.py        # Reportes
├── pa_labeling_tool.py           # Herramienta de etiquetado
├── calibration_validator.py      # Validación de config
├── pa_config.yaml                # Configuración
└── README.md                      # Este archivo

📄 Licencia
MIT License - Ver LICENSE para detalles

👤 Autor
Gerardo Corona
GitHub: @Gerardocorona

Nota: Este scanner está basado en la metodología de Eduardo (PRN-Million plus) para trading de opciones SPX 0DTE.
EOF


Ahora guarda y sube a GitHub:

```bash
# Añadir el archivo
git add README.md

# Guardar con mensaje
git commit -m "Add comprehensive README documentation"

# Subir a GitHub
git push origin main

