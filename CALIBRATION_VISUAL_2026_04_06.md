# Calibración Visual Post-Mercado — SPX 2m (2026-04-06)

## Análisis Visual del Usuario: Entradas Correctas vs Incorrectas

### ✅ ENTRADAS VÁLIDAS (Debería emitir señal)

**1. 9:56 AM** — **CALL**
- Patrón: A favor de pendiente tendencia alcista
- Confluencia: Impulso en tendencia clara
- Estado: GANADORA

**2. 10:12 AM** — **PUT** (NO a las 10:00 AM)
- Patrón: Vela de confirmación después de rebote en zona de techo
- Confluencia: Segunda vela envolvente (la vela 10:12 y la anterior envuelven vela anterior)
- Estado: GANADORA
- **Nota crítica:** Había que esperar a 10:02 PM para la vela de confirmación, no actuar en 10:00 AM

**3. 10:10 AM** — Entrada válida CUANDO supera cierre de vela anterior
- Patrón: A favor de pendiente
- Confluencia: Segunda vela de confirmación
- Estado: Validar en gráfico

**4. 10:38 AM** — Entrada válida
- Patrón: Cuando precio baja el mínimo de la vela anterior
- Estado: Validar

**5. 10:54 AM** — Entrada válida
- Estado: Validar en gráfico

**6. 1:02 PM** — **Buena entrada**
- Patrón: Válida
- Estado: GANADORA

**7. 1:24 PM** — **Buena entrada**
- Patrón: A favor de tendencia
- State: GANADORA

**8. 2:08 PM** — **CALL - Rompe lateralidad**
- Patrón: Ruptura de zona lateral con confirmación
- Confluencia: Break & Retest de lateralidad
- Estado: GANADORA
- **Nota crítica:** Lateral desde 11:16 AM hasta 2:08 PM — NO ENTRADAS DURANTE

**9. 2:10 PM** — **EXCELENTE entrada**
- Patrón: Excelente
- Estado: GANADORA (Barra #368 del análisis anterior)

---

### ❌ ENTRADAS INVÁLIDAS (NO debería emitir señal)

**1. 10:20 AM** — RECHAZAR
- Razón: **En zona de rebote en techo respetada múltiples veces**
- Lógica: "en el extremo no se debe operar, se debe esperar rebote o esperar que rompa esa zona"
- Implicación: Detector debe **rechazar entradas en zonas de resistance que han sido testeadas**
- Parámetro afectado: `range_at_extreme` weight, `historical_respect_rate` filter

**2. 10:40 AM** — RECHAZAR
- Razón: **Retesteando zona de techo de rebote**
- Lógica: Si hubiera cambio de tendencia, esperar segunda vela que borre cuerpo de vela contraria
- Implicación: Detector debe **esperar confirmación de ruptura antes de operar**
- Parámetro afectado: `second_candle_confirm` requirement, `retest_after_break` weight

---

## 📊 Extracción de Reglas de Calibración

### REGLA 1: Zonas de Techo/Piso Respetadas
```
SI precio está en zona de resistencia/soporte respetada múltiples veces (historical_respect_rate > 75%):
  - NO operar mientras está al extremo
  - ESPERAR rebote O ruptura con segunda vela de confirmación
  - Aumentar weight de "nivel_historical_respect" en confluencia
```
**Impacto:** Rechaza 10:20 AM, valida 2:08 PM (ruptura clara)

### REGLA 2: Segunda Vela de Confirmación
```
SI vela anterior fue rechazo/rebote en techo:
  - REQUIERE segunda vela que confirme dirección
  - Segunda vela debe cerrar fuera del cuerpo de vela anterior
  - Aumentar weight de "second_candle_confirm" en confluencia
```
**Impacto:** Rechaza 10:00 AM, valida 10:12 AM (segunda vela envolvente)

### REGLA 3: Respeto de Niveles en Retesteado
```
SI precio retestea zona de techo que acaba de rechazar:
  - SI retestea el nivel → ESPERAR siguiente movimiento
  - SI rompe el nivel → Requiere segunda vela de confirmación de continuidad
```
**Impacto:** Rechaza 10:40 AM (retest sin confirmación), valida 2:08-2:10 PM (ruptura + confirmación)

### REGLA 4: Lateralidad Estricta
```
Periodo: 11:16 AM → 2:08 PM (identificada como lateralidad)
- NO ENTRADAS durante periodo lateral
- PRIMERA RUPTURA con confirmación de vela envolvente = ENTRADA VÁLIDA
- Timestamps: 2:08 PM (ruptura) y 2:10 PM (confirmación) = VÁLIDAS
```
**Impacto:** Rechaza todas las entradas 11:16 AM a 2:08 PM, valida post-2:08 PM

---

## 🎯 Parámetros a Ajustar en pa_config.yaml

### PRIORIDAD 1: Histórico de Respeto de Niveles
```yaml
# AUMENTAR el weight de niveles que son respetados múltiples veces
confluence:
  factor_weights:
    nivel_historical_respect: 1.8 → 2.5  # Más importante
    range_at_extreme: 1.8 → 2.8  # CRÍTICO: detectar zonas extremas
```

### PRIORIDAD 2: Segunda Vela de Confirmación
```yaml
confluence:
  factor_weights:
    second_candle_confirm: 1.8 → 2.5  # Más importante aún

pattern_detectors:
  second_candle:
    min_body_ratio: 0.40 → 0.50  # Vela más decisiva
    confidence_weight: 1.5 → 2.0  # Patrón crítico
```

### PRIORIDAD 3: Engulfing Mejorado
```yaml
pattern_detectors:
  engulfing:
    body_ratio: 0.60 → 0.55  # Más flexible aún (Barra #368)
    confidence_weight: 1.3 → 1.8  # Más confianza en engulfing
```

### PRIORIDAD 4: Lateral Breakout Refinado
```yaml
lateral_market:
  max_range_points: 15.0 → 12.0  # Detectar lateral más sensible
  lookback_bars: 20 → 15  # Menos barras necesarias

break_and_retest:
  confidence_weight: 2.5 → 3.0  # CRÍTICO: ruptura de lateral
```

---

## 📈 Matriz de Validación

| Hora | Patrón | Usuario dice | Razón | Parámetro Clave |
|------|--------|--------------|-------|-----------------|
| 9:56 | Impulso alcista | ✅ CALL | A favor tendencia | trend_alignment |
| 10:00 | Rebote en techo | ❌ ESPERAR | Falta 2da vela | second_candle |
| 10:12 | Engulfing confirmador | ✅ PUT | 2da vela envolvente | engulfing + 2nd_candle |
| 10:20 | Extremo de techo | ❌ RECHAZAR | Techo respetado | historical_respect |
| 10:40 | Retest sin confirmación | ❌ RECHAZAR | Vela sin cuerpo fuerte | 2nd_candle + break_retest |
| 11:16-2:08 | Lateralidad | ❌ NO ENTRADAS | Consolidación | lateral_market |
| 2:08 | Ruptura de lateral | ✅ CALL | Rompe + confirma | break_and_retest |
| 2:10 | Confirmación | ✅ CALL | Engulfing post-ruptura | engulfing |

---

## ✅ Plan de Implementación

1. **Aumentar weights de validación histórica** → Rechaza extremos de zonas respetadas
2. **Aumentar weight de segunda vela** → Requiere confirmación antes de actuar
3. **Afinar engulfing detection** → Aceptar patrones más naturales
4. **Mejorar lateral detection** → Ser más sensible, confiar en ruptura clara
5. **Validar post-mercado** → Ejecutar backtester con parámetros nuevos

---

## 📍 Observación Crítica del Usuario

> "en el extremo no se debe operar, se debe esperar rebote o esperar que rompa esa zona y continue el moviemiento ascendente y esperar una vela de confirmacion de continuidad del movimiento una ves sobrepase esa zona de techo si es que lo hace"

**Esto define la lógica del scanner:**
- Detectar extremos de zonas respetadas (range_at_extreme)
- Rechazar entrada mientras está en extremo
- Esperar: (a) rebote + 2da vela, O (b) ruptura + 2da vela
- **Nunca entrar en extremo sin confirmación**

