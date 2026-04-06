# REGLA FUNDAMENTAL: NO OPERAR EN TENDENCIAS LATERALES

## 📌 La Regla (Sin Excepciones)

```
En lateralidad, JAMÁS se debe tomar posición.
Esperamos ruptura de la lateralidad CON CONFIRMACIÓN de vela envolvente.
```

**Fuente:** Metodología de Eduardo (PRN-Million plus)
**Implementación:** 2026-04-06 Post-Market Calibration
**Estado:** CRÍTICA — ENFORCEMENT OBLIGATORIO

---

## 🎯 Definición de Lateralidad

La lateralidad ocurre cuando:

1. **Precio oscila en rango estrecho** (max_range_points ≤ 12 pts en timeframe 2m)
2. **Sin movimiento direccional claro** (min_directional_bars_pct < 50%)
3. **Sobre período de 15+ barras** (lookback_bars: 15)
4. **Sin ruptura confirmada** hacia arriba o hacia abajo

### Ejemplo Visual (2026-04-06):
```
11:16 AM — Comienza lateralidad
11:17, 11:18, 11:19... precio oscilando entre 6591-6599 (8 pts rango)
...continuando sin dirección clara...
2:08 PM — RUPTURA detectada (precio quiebra nivel superior)
2:10 PM — CONFIRMACIÓN con vela envolvente alcista
         ↳ PRIMERA ENTRADA VÁLIDA POST-LATERAL
```

---

## ⚠️ POR QUÉ NO OPERAR EN LATERAL

**Razones Técnicas:**
1. Los movimientos son **rápidos y cortos** (scalps de 2-3 pts)
2. Los contratos se **desvalorizan rápidamente** por time decay
3. Stop losses se activan fácilmente (ruido)
4. Ratio riesgo/recompensa es **muy desfavorable**

**Ejemplo:**
- Lateral: 6595-6599 (4 pts de rango útil)
- SL típico: 12 pts
- TP típico: 20 pts
- En lateral, tocas SL antes de TP → **Operación perdedora**

---

## ✅ CUÁNDO OPERAR (Post-Lateral)

SOLO cuando:

### Condición 1: Ruptura Confirmada
```
SI precio rompe límite de lateralidad (arriba o abajo):
  → Debe cerrar FUERA del rango lateral
  → No es suficiente tocar el nivel (wick)
```

### Condición 2: Segunda Vela Confirmadora
```
SI vela post-ruptura es engulfing OR second_candle:
  → Cuerpo debe estar COMPLETAMENTE afuera del rango lateral
  → Volumen debe confirmar dirección
  → Cierre debe estar en máximo (CALL) o mínimo (PUT)
```

### Condición 3: Confluencia Adicional
```
Después de ruptura + confirmación, requiere:
  ✓ Align con tendencia macro (1h)
  ✓ Precio en zona válida (no extremo)
  ✓ Volumen anormal (impulso)
```

---

## 🔴 SEÑALES RECHAZADAS EN LATERAL

El scanner DEBE rechazar (0 excepciones):

| Hora | Tipo Señal | Razón | Status |
|------|-----------|-------|--------|
| 11:20 AM | Pin Bar bullish | En zona lateral | ❌ RECHAZAR |
| 11:45 AM | Engulfing | Dentro lateral | ❌ RECHAZAR |
| 12:30 PM | Inside Bar | Lateral persiste | ❌ RECHAZAR |
| 1:15 PM | Hammer | Aún en lateral | ❌ RECHAZAR |
| 2:00 PM | Cualquier patrón | Últimas barras lateral | ❌ RECHAZAR |
| **2:08 PM** | **Ruptura detectada** | **Salida de lateral** | **✅ ANALIZAR** |
| **2:10 PM** | **Engulfing alcista** | **Confirmación ruptura** | **✅ OPERAR** |

---

## 🔧 IMPLEMENTACIÓN EN pa_config.yaml

```yaml
# DETECCIÓN DE MERCADO LATERAL
lateral_market:
  enabled: true                    # OBLIGATORIO: Always enabled
  max_range_points: 12.0           # Rango máximo: 12 pts
  min_directional_bars_pct: 0.50   # Mín 50% de barras direccionales
  lookback_bars: 15                # Analizar últimas 15 barras

# CONFLUENCIA - FILTROS OBLIGATORIOS
confluence:
  filters:
    - lateral_market_detected: false   # SI LATERAL → RECHAZAR (NO EXCEPCIONES)
    - price_within_session: true
    - no_conflicting_signals: true
```

**Interpretación:**
- `lateral_market_detected: false` = NO hay lateral permitido
- Si el scanner detecta lateral, RECHAZA todas las señales
- NO hay excepciones basadas en confianza o confluencia

---

## 📊 VALIDACIÓN EN BACKTEST

El backtest DEBE mostrar:

```
--- SIGNAL FUNNEL ---
Patterns found:             XXX
Signals generated:          YYY
  Rejected (lateral):       ⚠️ DEBE SER > 0  (señales rechazadas por lateral)
  Rejected (zone):          ZZZ
  Rejected (confluence):    AAA
Signals passed:             BBB
```

**Interpretación correcta:**
- Si `Rejected (lateral): 0` → El detector de lateral NO está funcionando
- Debe haber rechazos por lateral en períodos consolidados
- Ejemplo esperado: 10-20% de señales rechazadas por lateral

---

## ⚡ RESUMEN EJECUTIVO

| Aspecto | Regla |
|---------|-------|
| **Operación en lateral** | ❌ PROHIBIDA (0 excepciones) |
| **Razón** | Time decay + ruido = pérdida segura |
| **Qué esperar** | Ruptura + confirmación de vela envolvente |
| **Timing de entrada** | DESPUÉS de confirmación post-ruptura |
| **Confluencia requerida** | Sí, adicional a ruptura + confirmación |
| **Validación** | Backtest debe rechazar señales en lateral |

---

## 📝 NOTAS CRÍTICAS

> "En lateralidad, mientras persista, no se debe tomar posicion porque los movimientos son lestos y cortos, se desvalorizan los contratos."
>
> "Debe respetarla [la zona], tal como lo hizo a continuacion y luego esperar una vela de confirmacion en sentido contrario si fuera el caso."
>
> — Gerardo Corona, 2026-04-06

**Traducción a código:**
1. Detectar período lateral (range + directionalidad)
2. RECHAZAR ALL signals mientras lateral persiste
3. Esperar ruptura (cierre fuera rango)
4. Esperar confirmación (segunda vela envolvente)
5. Operar SOLO post-confirmación con confluencia adicional

---

## 🚀 Checklist de Implementación

- [x] Detectar lateralidad automáticamente
- [x] Rechazar signals durante lateral (filter obligatorio)
- [x] Documentar comportamiento esperado
- [x] Validar en backtest
- [ ] **PENDIENTE:** Verificar que backtest rechaza > 0 signals por lateral
- [ ] **PENDIENTE:** Revisar parámetros de detección si Rejected(lateral) = 0

