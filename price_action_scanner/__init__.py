"""
Price Action Scanner — SPX 0DTE Options
Metodología: Eduardo (PRN-Million plus)

Core Components:
  • Detector: Detección automática de patrones de price action
  • Scanner: Motor principal de análisis
  • SignalGenerator: Generación de señales de trading
  • ConfluenceChecker: Validación de confluencia múltiple

Calibration & Analysis:
  • Calibrator: Optimización automática de parámetros
  • LabelingTool: Etiquetado manual de patrones
  • CalibrationValidator: Validación de configuración

Backtesting & Simulation:
  • Backtester: Motor de backtesting histórico
  • MonteCarloSimulator: Simulación Monte Carlo
  • CompoundMonteCarloSimulator: Simulación compuesta
  • Optimizer: Optimización de estrategia

Reporting:
  • ReportGenerator: Generación de reportes visuales

Data Classes:
  • Signal schemas: PatternData, TrendContext, ConfluenceData, OrderData, etc.
"""

# Core signal/pattern data structures
from .pa_signal_schema import (
    PatternData,
    TrendContext,
    ConfluenceData,
    OrderData,
    PriceActionSignal,
    CalibrationLabel,
)

# Core analysis engines
from .pa_detector import PriceActionDetector
from .confluence_checker import ConfluenceChecker
from .signal_generator import SignalGenerator
from .pa_scanner import PriceActionScanner

# Calibration & validation
from .calibration_validator import CalibrationValidator
from .pa_calibrator import PriceActionCalibrator
from .pa_labeling_tool import PriceActionLabelingTool

# Backtesting & simulation (lazy — require pandas/yfinance)
try:
    from .pa_backtester import PriceActionBacktester, Trade, BacktestResult
    from .pa_montecarlo import MonteCarloSimulator, MonteCarloResult
    from .pa_montecarlo_compound import CompoundMonteCarloSimulator, CompoundResult
    from .pa_optimizer import PriceActionOptimizer, OptimizationResult
except ImportError:
    pass

# Reporting
try:
    from .pa_report_generator import PriceActionReportGenerator
except ImportError:
    pass

__all__ = [
    # Signal schemas
    "PatternData",
    "TrendContext",
    "ConfluenceData",
    "OrderData",
    "PriceActionSignal",
    "CalibrationLabel",
    # Core engines
    "PriceActionDetector",
    "ConfluenceChecker",
    "SignalGenerator",
    "PriceActionScanner",
    # Calibration & validation
    "CalibrationValidator",
    "PriceActionCalibrator",
    "PriceActionLabelingTool",
    # Backtesting
    "PriceActionBacktester",
    "Trade",
    "BacktestResult",
    # Monte Carlo
    "MonteCarloSimulator",
    "MonteCarloResult",
    "CompoundMonteCarloSimulator",
    "CompoundResult",
    # Optimization
    "PriceActionOptimizer",
    "OptimizationResult",
    # Reporting
    "PriceActionReportGenerator",
]
