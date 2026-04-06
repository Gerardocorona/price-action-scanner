# Price Action Trading System — System Guide

**Metodología**: Eduardo (PRN-Million plus) — SPX 0DTE Options
**Última actualización**: 2026-04-06

---

## Quick Start

```bash
# 1. Crear tablas de base de datos
python TradingEngine/db/pa_db_tables.py
# Opción 1: Crear tablas

# 2. Ejecutar scanner en vivo
python TradingEngine/scanners/price_action_scanner/pa_scanner.py

# 3. Post-sesión: etiquetar señales
python TradingEngine/scanners/price_action_scanner/pa_labeling_tool.py

# 4. Calibrar parámetros
python TradingEngine/scanners/price_action_scanner/pa_calibrator.py

# 5. Generar reportes
python TradingEngine/scanners/price_action_scanner/pa_report_generator.py
```

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         pa_scanner.py                         │
│                    (Main Orchestrator)                        │
│  Calls: IBClient.get_historical_bars() + analyze()          │
└────┬─────────────────────────────────────────────────────────┘
     │
     ├─→ pa_detector.py (Pattern Detection)
     │   Detects: Pin Bar, Engulfing, Inside Bar, Shooting Star, Hammer
     │
     ├─→ confluence_checker.py (Confluence Verification)
     │   Validates: 8 weighted factors + Lateral Market filter
     │
     └─→ signal_generator.py (Order Generation)
         ├─ Calculates: SL/TP/Trail
         ├─ Persists: price_action_signals (DB)
         └─ Sends: BrokerService.send_order() [if enabled]

┌──────────────────────────────────────────────────────────────┐
│                    Post-Session Workflow                      │
└──────────────────────────────────────────────────────────────┘

pa_labeling_tool.py     → Creates ground truth labels
                          (setup_valid, pattern_correct, confluencia_correct)
                          Saves to: price_action_labels table

pa_calibrator.py        → Grid search optimization
                          Uses: price_action_labels as feedback
                          Finds: Best parameters
                          Saves to: price_action_calibration_runs table

pa_report_generator.py  → HTML visualization
                          Charts: PnL, Win Rate, Accuracy
                          Exports: CSV, HTML reports
```

---

## Data Schemas

### 1. PriceActionSignal (price_action_signals table)

```
✓ Identification
  - signal_id: UUID
  - timestamp: ISO format
  - session_date: YYYY-MM-DD

✓ Pattern Detected (2m timeframe)
  - pattern_type: 'pin_bar', 'engulfing', etc.
  - pattern_direction: 'bullish', 'bearish'
  - pattern_confidence: 0.0-1.0
  - wick_ratio, body_ratio, volume_ratio: Technical metrics

✓ Trend Context (Multi-timeframe)
  - trend_1h, trend_5m, trend_2m: 'bullish', 'bearish', 'lateral'
  - is_lateral_market: Boolean (rejection filter)
  - break_and_retest_detected: Boolean (CRITICAL factor)

✓ Confluence Verification
  - confluence_factors: ["nivel_en_zona", "trend_1h_bullish", ...]
  - confluence_score: Float (weighted sum)
  - confluence_count: Integer (number of factors met)

✓ Order Generated (if confluencia passed)
  - order_generated: 0/1
  - order_direction: 'CALL' or 'PUT'
  - order_contracts: Integer
  - entry_price, stop_loss, take_profit_1, take_profit_2
  - broker_order_id: Reference to broker

✓ Exit Results (filled post-trade)
  - exit_price: Exit level
  - exit_time: When closed
  - pnl_points: Profit/Loss in SPX points
  - pnl_usd: Profit/Loss in USD
  - exit_reason: 'tp1', 'tp2', 'sl', 'trail', 'manual'

✓ Status
  - status: 'detected', 'order_ready', 'order_sent', 'filled', 'closed', 'rejected'
```

### 2. CalibrationLabel (price_action_labels table)

```
✓ Ground Truth Validation (user input post-session)
  - signal_id: References price_action_signals
  - setup_valid: 1 (correct) / 0 (false positive)
  - pattern_correct: 1/0
  - confluencia_correct: 1/0

✓ Qualitative Analysis
  - notes: User observations
  - confidence_level: 'alta', 'media', 'baja'
  - labeled_by: Username
  - labeled_at: Timestamp
```

### 3. CalibrationRun (price_action_calibration_runs table)

```
✓ Optimization Results
  - run_id: Unique identifier
  - timestamp: When run executed
  - num_signals: How many signals used
  - best_score: Optimization metric (0.0-1.0)
  - best_params: JSON with optimized parameters
  - results_summary: JSON with detailed metrics
  - notes: User notes
```

---

## Components

### 1. pa_detector.py — Pattern Detection

**Detects:**
- Pin Bar: wick_ratio ≥ 0.65, body_ratio ≤ 0.30
- Engulfing: current body ≥ 115% of previous
- Inside Bar: current range ≤ 80% of previous
- Shooting Star: bearish pin bar (upper wick dominant)
- Hammer: bullish pin bar (lower wick dominant)

**Methods:**
```python
detector = PriceActionDetector(config_path="pa_config.yaml")
pattern = detector.detect_latest(bars_2m)  # Returns PatternData or None
patterns = detector.scan_bars(bars)         # Returns all patterns found
```

### 2. confluence_checker.py — Multi-factor Validation

**Trend Analysis (1H/5M/2M):**
- Compares first_half vs second_half of bars
- Determines direction: bullish, bearish, lateral

**Lateral Market Detection:**
- If total_range ≤ 15 pts AND directional_bars < 50%
- Rejects signal immediately (primary filter)

**8 Confluence Factors (weighted):**

| Factor | Weight | Validation |
|--------|--------|------------|
| nivel_en_zona | 2.0 | Price within zone_tolerance of S/R |
| nivel_historical_respect | 1.8 | Level respected ≥ 70% historically |
| trend_alignment_1h | 1.5 | Pattern direction matches 1H trend |
| trend_alignment_5m | 1.2 | Pattern direction matches 5M trend |
| pattern_detected | 1.3 | Pattern confidence ≥ threshold |
| **break_and_retest** | **2.5** | **CRITICAL: Ruptura + Retroceso + Entrada** |
| volume_confirmation | 0.8 | Volume ratio ≥ 0.80 of average |
| ma_positioning | 0.7 | Price aligned with 20/200 EMAs |

**Minimum requirement:** ≥ 3 factors (meets_minimum = True)

**Methods:**
```python
checker = ConfluenceChecker(config_path="pa_config.yaml")
trend = checker.build_trend_context(bars_1h, bars_5m, bars_2m)
confluence = checker.check(pattern, trend, current_price, bars_5m)
```

### 3. signal_generator.py — Order Execution

**Risk Management Defaults:**
```
Bullish (CALL):
  SL = entry - 12 pts
  TP1 = entry + 20 pts
  TP2 = entry + 35 pts

Bearish (PUT):
  SL = entry + 12 pts
  TP1 = entry - 20 pts
  TP2 = entry - 35 pts

Trailing Stop:
  Activates after: 8 pts gain
  Trail distance: 5 pts
```

**Methods:**
```python
generator = SignalGenerator(db_manager=db, broker_service=broker)
signal = await generator.generate(pattern, trend, confluence, current_price, send_order=True)
generator.update_result(signal_id, exit_price, exit_reason, pnl_points, pnl_usd)
```

### 4. pa_scanner.py — Main Orchestrator

**Async Loop (120-second cycle):**

```python
async def _scan_cycle():
    # Get 1H/5M/2M data
    bars_1h = await ib.get_historical_bars("SPX", "1h", count=30)
    bars_5m = await ib.get_historical_bars("SPX", "5m", count=50)
    bars_2m = await ib.get_historical_bars("SPX", "2m", count=30)

    # Get current price
    quote = await ib.get_quote("SPX")
    current_price = quote['last']

    # Full analysis
    signal = await self.analyze(bars_1h, bars_5m, bars_2m, current_price, send_order=True)
```

**Statistics:**
```python
stats = scanner.get_session_stats()
# Returns: {
#   'signals_detected': int,
#   'signals_sent': int,
#   'signals_rejected': int,
#   'conversion_rate': float,
#   'elapsed_minutes': int
# }
```

### 5. pa_labeling_tool.py — Ground Truth Validation

**Interactive post-session labeling:**

```bash
python pa_labeling_tool.py

# For each unlabeled signal:
Setup valid (1=yes, 0=no): 1
Pattern correct (1=yes, 0=no): 1
Confluencia correct (1=yes, 0=no): 1
Notes: Excellent ruptura+retroceso pattern
Confianza (alta/media/baja): alta

# Exports to: labels_SESSION_DATE.csv
```

**Statistics:**
```python
labeler = PriceActionLabelingTool()
stats = labeler.get_session_stats('2026-04-06')
# Returns: {
#   'total_signals': int,
#   'labeled_signals': int,
#   'accuracy': float (% correct),
#   'precision': float,
#   'avg_confidence': str
# }
```

### 6. pa_calibrator.py — Parameter Optimization

**Grid Search:**

```bash
python pa_calibrator.py

# Default search space:
zone_tolerance:           [3.0, 3.5, 4.0, 4.5, 5.0]
historical_respect:       [0.65, 0.70, 0.75, 0.80]
lateral_range_threshold:  [12, 15, 18]
lateral_directional_pct:  [0.40, 0.50, 0.60]

# Evaluates combinations against labeled signals
# Finds: best_params with highest score
```

**Apply Results:**

```python
calibrator = PriceActionCalibrator()
result = calibrator.run_grid_search('2026-04-06')
calibrator.apply_best_params(result['best_params'])
calibrator.save_calibration_run(result, notes="Post-session optimization")
```

### 7. pa_report_generator.py — HTML Reports

**Generate Reports:**

```bash
python pa_report_generator.py

# Options:
1. Session report     (signals, PnL, win rate by date)
2. Summary report     (last N days overview)
3. Calibration report (grid search results)

# Output: HTML files in TradingEngine/reports/
```

---

## Configuration (pa_config.yaml)

### Key Parameters

```yaml
# Timeframes
timeframes:
  macro: '1h'
  structure: '5m'
  entry: '2m'

# Support/Resistance levels (configurable per asset)
support_resistance_levels:
  - price: 6583.89
    zone_tolerance: 4.5          # ± 4.5 points
    historical_respect_rate: 0.75

# Pattern detectors
pattern_detectors:
  pin_bar:
    enabled: true
    wick_ratio: 0.65
    body_ratio: 0.30
    wick_to_body_ratio: 2.0
    confidence_weight: 0.85

  engulfing:
    enabled: true
    body_ratio: 1.15              # Current body ≥ 115% of previous
    confidence_weight: 0.90

  # ... other patterns

# Confluence factors (weights)
confluence:
  min_factors_required: 3
  zone_tolerance: 4.5
  historical_respect_threshold: 0.70

  weights:
    nivel_en_zona: 2.0
    nivel_historical_respect: 1.8
    trend_alignment_1h: 1.5
    trend_alignment_5m: 1.2
    pattern_detected: 1.3
    break_and_retest: 2.5         # CRITICAL
    volume_confirmation: 0.8
    ma_positioning: 0.7

# Lateral market detection
session_rules:
  lateral_market:
    range_threshold: 15           # Points
    directional_pct: 0.50         # 50% directional bars

  # Trading hours (ET)
  market_open_time: '09:30'
  market_close_time: '16:00'
  avoid_first_minutes_open: 15
  avoid_last_minutes_close: 10

# Execution (risk management)
execution:
  stop_loss_distance: 12
  take_profit_1_distance: 20
  take_profit_2_distance: 35
  max_contracts_per_signal: 1

  trailing_stop:
    enabled: true
    activate_after_points: 8
    trail_distance: 5
```

---

## Workflow — Complete Session

### Morning
1. **Review calibration results** from previous day
   ```bash
   python pa_report_generator.py  # Generate summary
   ```

2. **Start scanner** (runs until market close)
   ```bash
   python pa_scanner.py
   ```

### Evening (Post-Session)
3. **Label signals** for ground truth
   ```bash
   python pa_labeling_tool.py
   # Validates: setup_valid, pattern_correct, confluencia_correct
   ```

4. **Run calibrator** to optimize parameters
   ```bash
   python pa_calibrator.py
   # Finds best_params for pa_config.yaml
   ```

5. **Generate reports** for analysis
   ```bash
   python pa_report_generator.py
   # Generates: session_DATE.html, summary_7days.html
   ```

### Next Day
6. **Apply optimized parameters** (from calibration)
   - Calibrator auto-applies to pa_config.yaml
   - Scanner reloads on startup

---

## Integration with IBClient + BrokerService

### IBClient Requirements

```python
# pa_scanner.py expects:
await ib_client.get_historical_bars(symbol="SPX", timeframe="1h", count=30)
# Returns: List[{'open': float, 'high': float, 'low': float, 'close': float, 'volume': int}]

await ib_client.get_quote(symbol="SPX")
# Returns: {'last': float, 'close': float, ...}
```

### BrokerService Integration

```python
# signal_generator.py calls:
await broker_service.send_order(
    symbol="SPX",
    expiration="0DTE",
    option_type="CALL" or "PUT",
    contracts=1,
    entry_price=6595.50,
    stop_loss=6583.50,
    take_profit_1=6615.50,
    take_profit_2=6630.50,
    trailing_stop_enabled=True,
    trail_activate_at=6603.50,
    trail_distance=5.0,
    signal_id="uuid"
)
# Expected return: {'order_id': '...', 'status': 'sent'}
```

---

## Database Initialization

```bash
# Create all required tables
python TradingEngine/db/pa_db_tables.py
# Option 1: Create tables

# Verify
python TradingEngine/db/pa_db_tables.py
# Option 2: Verify tables
```

**Tables Created:**
- `price_action_signals` — All signals detected
- `price_action_labels` — Ground truth validation
- `price_action_calibration_runs` — Calibration history

---

## Troubleshooting

### Issue: "IBClient no tiene método esperado"
**Solution**: Verify IBClient has:
- `async get_historical_bars(symbol, timeframe, count)`
- `async get_quote(symbol)`

### Issue: "BrokerService not available"
**Solution**: Orders will not send. Check:
- Is broker_service passed to pa_scanner?
- Is send_order=True in analyze() call?

### Issue: "Tables already exist"
**Solution**: Safe to re-run pa_db_tables.py — uses `CREATE TABLE IF NOT EXISTS`

### Issue: "No signals detected"
**Solution**: Check:
- Is 2m data being received? (detector requires ≥2 bars)
- Are patterns enabled in pa_config.yaml?
- Is lateral market filter too aggressive?

---

## Performance Tips

1. **Optimize Grid Search**: Limit search space to 2-3 parameters
2. **Cache S/R Levels**: Pre-define high-probability zones
3. **Label Consistently**: Use same confidence criteria each session
4. **Review Monthly**: Generate 30-day reports for trend analysis
5. **Adjust SL/TP**: Monitor PnL distribution, adjust targets as needed

---

## Next Steps

- [ ] Run database migration (`pa_db_tables.py`)
- [ ] Connect IBClient with live data
- [ ] Connect BrokerService for order execution
- [ ] Run first backtesting session with historical data
- [ ] Label signals post-session
- [ ] Run calibrator to optimize parameters
- [ ] Generate reports and analyze

---

**System**: Price Action Trading System
**Methodology**: Eduardo (PRN-Million plus)
**Target**: SPX 0DTE Options
**Status**: Production-Ready

For detailed code documentation, see individual module docstrings.
