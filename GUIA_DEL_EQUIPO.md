# GUÍA DEL EQUIPO — Bot Alfa Price Action Scanner
**Sistema SPX 0DTE | Metodología: Eduardo (PRN-Million plus)**
**Versión: 2.0 | Actualizada: 2026-04-06**

---

## ¿Qué es este sistema?

Un scanner automático que detecta patrones de price action en SPX y ejecuta órdenes de opciones 0DTE directamente en Interactive Brokers (IBKR/TWS).

**Dos piezas que trabajan juntas:**

| Pieza | Qué hace | Dónde vive |
|-------|----------|-----------|
| **Price Action Scanner** | Detecta patrones, valida confluencia, genera señales | `C:\Users\gecor\price-action-scanner\` / GitHub |
| **Bot Alfa** | Conecta con IBKR, selecciona contratos, ejecuta órdenes, muestra dashboard | `C:\TV-BOT-TWS...\Bot Alfa\` |

---

## ÍNDICE

1. [Configuración inicial (primera vez)](#1-configuración-inicial-primera-vez)
2. [Cómo arrancar el sistema cada día](#2-cómo-arrancar-el-sistema-cada-día)
3. [Entender las dos ventanas](#3-entender-las-dos-ventanas)
4. [El Dashboard Web](#4-el-dashboard-web)
5. [Modos de operación: DRY-RUN vs PRODUCCIÓN](#5-modos-de-operación-dry-run-vs-producción)
6. [Cómo actualizar cuando hay cambios del equipo](#6-cómo-actualizar-cuando-hay-cambios-del-equipo)
7. [Parámetros clave — qué tocar y qué no](#7-parámetros-clave--qué-tocar-y-qué-no)
8. [Reglas de trading que el scanner aplica](#8-reglas-de-trading-que-el-scanner-aplica)
9. [Post-sesión — qué hacer al cerrar el mercado](#9-post-sesión--qué-hacer-al-cerrar-el-mercado)
10. [Solución de problemas comunes](#10-solución-de-problemas-comunes)
11. [Arquitectura del sistema (para el equipo técnico)](#11-arquitectura-del-sistema-para-el-equipo-técnico)

---

## 1. Configuración inicial (primera vez)

> Hacer **una sola vez** por computadora. Si ya lo hiciste, ve al paso 2.

### Paso 1.1 — Instalar Python 3.12+

Descarga desde https://python.org (versión 3.12 o superior).
Durante la instalación marca: ✅ **"Add Python to PATH"**

Verifica que quedó bien:
```
python --version
```
Debe mostrar: `Python 3.12.x`

---

### Paso 1.2 — Clonar el repositorio del scanner

Abre una ventana de **CMD** (no PowerShell) y ejecuta:

```cmd
cd C:\Users\%USERNAME%
git clone https://github.com/Gerardocorona/price-action-scanner.git
cd price-action-scanner
```

> ⚠️ La carpeta debe quedar exactamente en `C:\Users\TU_USUARIO\price-action-scanner\`
> El archivo `INICIAR_SISTEMA.bat` busca el scanner en esa ruta.

---

### Paso 1.3 — Instalar dependencias del scanner

Dentro de la carpeta `price-action-scanner`, ejecuta:

```cmd
pip install -r requirements.txt
```

Esto instala: `pyyaml`, `numpy`, `pandas`, `aiohttp`, `scipy`.

---

### Paso 1.4 — Verificar que el Bot Alfa está disponible

El Bot Alfa ya debe estar instalado en tu máquina en:
```
C:\TV-BOT-TWS - Confirmar orden hija - Criterios contratos\Bot Alfa\
```

Si no lo tienes, pídele a Gerardo el ZIP de instalación.

---

### Paso 1.5 — Verificar instalación completa

```cmd
cd C:\Users\%USERNAME%\price-action-scanner
python -c "from price_action_scanner.signal_router import SignalRouter; print('OK — Todo instalado correctamente')"
```

Debe mostrar: `OK — Todo instalado correctamente`

---

## 2. Cómo arrancar el sistema cada día

### ✅ Checklist antes de abrir el .bat

Antes de hacer doble click en `INICIAR_SISTEMA.bat`, verifica:

- [ ] **TWS / IB Gateway abierto** y logueado (paper o live según el día)
- [ ] **API habilitada en TWS**: File → Global Configuration → API → Enable ActiveX and Socket Clients ✅
- [ ] **Puerto 7497** para Paper Trading / **7496** para Live Trading
- [ ] Conexión a internet estable

---

### ▶️ Arrancar el sistema

1. Navega a la carpeta del Bot Alfa:
   ```
   C:\TV-BOT-TWS - Confirmar orden hija - Criterios contratos\Bot Alfa\
   ```

2. Haz **doble click** en:
   ```
   INICIAR_SISTEMA.bat
   ```

Eso es todo. El archivo hace esto automáticamente:
1. Verifica que TWS está corriendo
2. Abre el **Bot Alfa** (ventana azul) — conecta con IBKR
3. Abre el **PA Scanner** (ventana amarilla) — empieza a analizar
4. Abre el **Dashboard** en tu navegador (`http://localhost:8001`)

---

## 3. Entender las dos ventanas

### Ventana AZUL — "BOT ALFA — IBKR Server"

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
IBKR Connected successfully.
```

**Qué hace:** Mantiene la conexión con IBKR/TWS, sirve el dashboard web,
y recibe las órdenes del scanner cuando detecta señales.

**Mensajes normales:**
- `IBKR Connected successfully.` ✅ Conectado con TWS
- `Engine Error: ...` ⚠️ Problema de conexión — revisar TWS
- `WATCHDOG TRIGGER: +10% profit` ✅ Trailing stop activado automáticamente

---

### Ventana AMARILLA — "PA SCANNER — SPX 0DTE"

```
10:24:15 [ibg.price_action.scanner] INFO: Patrón encontrado: engulfing(bullish) conf=0.82 @ 6589.00
10:24:15 [ibg.price_action.scanner] INFO: ✅ engulfing(bullish) | 4 factores | CALL @ 6589.00
10:24:15 [ibg.price_action.signal_router] INFO: Sizing: Cash=$5,000 | Risk=20% ($1,000) | Ask=$4.50 | Qty=2c
10:24:15 [ibg.price_action.scanner] INFO: 🚀 IBKR [DRY-RUN]: CALL 2c @ $4.50 | Arriesgado: $900.00
```

**Qué hace:** Analiza barras de 2m cada ciclo, detecta patrones de price action,
valida confluencia y (en modo PRODUCCIÓN) envía órdenes al Bot Alfa.

**Mensajes importantes:**
- `⚠️ LATERAL detectado` → El mercado está en rango. No opera. Correcto.
- `✅ Señal generada: CALL` → Señal válida encontrada
- `🚀 IBKR: CALL 2c @ $4.50` → Orden enviada a IBKR
- `[DRY-RUN]` → Calculó pero NO envió orden (modo seguro)
- `Confluencia insuficiente` → Patrón encontrado pero sin suficiente confirmación. Normal.

---

## 4. El Dashboard Web

Accede desde cualquier navegador en la misma red:

| Desde | URL |
|-------|-----|
| La misma PC | http://localhost:8001 |
| Celular / otra PC en la red | http://[IP-DE-LA-PC]:8001 |

**Credenciales:**
- Usuario: `admin`
- Contraseña: (preguntar a Gerardo)

**Qué muestra:**
- Balance de cuenta en tiempo real
- Número de posiciones abiertas SPX
- Estado de conexión IBKR (ONLINE / OFFLINE)
- Botones manuales CALL / PUT (si necesitas operar manualmente)

---

## 5. Modos de operación: DRY-RUN vs PRODUCCIÓN

Este es el control más importante del sistema.

### 🟡 DRY-RUN (default — configuración actual)

El scanner **detecta, analiza y calcula** cuántos contratos compraría,
pero **NO envía ninguna orden** a IBKR.

Lo verás así en la ventana amarilla:
```
🚀 IBKR [DRY-RUN]: CALL 2c @ $4.50 | Arriesgado: $900.00
```

**Cuándo usar:** Días nuevos, al probar cambios, cuando quieres observar
sin arriesgar dinero real.

---

### 🔴 PRODUCCIÓN (envía órdenes reales)

El scanner envía órdenes directamente a IBKR en cuanto detecta una señal válida.

Lo verás así en la ventana amarilla:
```
🚀 IBKR: CALL 2c @ $4.50 | Arriesgado: $900.00 | SPXW 20260406 C6590
```

**Cómo activarlo:**

1. Cierra el sistema (cierra las ventanas)
2. Abre `INICIAR_SISTEMA.bat` con el Bloc de notas (click derecho → Editar)
3. Encuentra esta línea (aproximadamente línea 45):
   ```bat
   set PA_DRY_RUN=1
   ```
4. Cámbiala a:
   ```bat
   set PA_DRY_RUN=0
   ```
5. Guarda el archivo
6. Haz doble click en `INICIAR_SISTEMA.bat` para arrancar en modo producción

> ⚠️ **IMPORTANTE**: Antes de poner `PA_DRY_RUN=0`, confirma que:
> - TWS está en cuenta LIVE (no paper)
> - El balance de cuenta es correcto
> - Revisaste al menos 1 sesión completa en DRY-RUN sin problemas

---

### Plan de riesgo por trade

El sistema calcula automáticamente cuántos contratos comprar:

```
Contratos = floor(Balance × 20% / (Ask × 100))

Ejemplo:
  Balance = $5,000
  Ask     = $4.50 por contrato
  Riesgo  = $5,000 × 20% = $1,000
  Contratos = floor($1,000 / $450) = 2 contratos
```

Para cambiar el porcentaje de riesgo, edita en `INICIAR_SISTEMA.bat`:
```bat
set PA_RISK_PCT=0.20    ← 20% (actual)
set PA_RISK_PCT=0.10    ← 10% (más conservador)
```

---

## 6. Cómo actualizar cuando hay cambios del equipo

Cuando Gerardo u otro miembro del equipo actualiza el scanner,
necesitas bajar los cambios a tu máquina.

### Actualización normal (más común)

```cmd
cd C:\Users\%USERNAME%\price-action-scanner
git pull origin main
```

Si agrega nuevas dependencias:
```cmd
pip install -r requirements.txt
```

Luego reinicia el sistema (cierra ventanas y vuelve a abrir `INICIAR_SISTEMA.bat`).

---

### Si `git pull` da error de conflicto

Significa que tienes cambios locales que chocan con los del equipo.
En casi todos los casos lo correcto es quedarse con la versión del equipo:

```cmd
git stash
git pull origin main
```

Si tienes cambios propios que quieres conservar, **avisa a Gerardo primero**
antes de hacer cualquier cosa.

---

### Si modificaste `pa_config.yaml` localmente

El archivo de configuración a veces genera conflictos porque cada miembro
puede haberlo ajustado. La regla del equipo es:

> **Los parámetros oficiales del equipo siempre están en GitHub.**
> Si hiciste ajustes locales para pruebas, no hagas `git push` sin
> coordinar con Gerardo.

Para ver qué cambió antes de hacer pull:
```cmd
git diff price_action_scanner/pa_config.yaml
```

---

## 7. Parámetros clave — qué tocar y qué no

### ✅ Puedes ajustar libremente

Estos son los únicos parámetros que el equipo ajusta día a día:

| Parámetro | Dónde | Qué hace |
|-----------|-------|----------|
| `PA_DRY_RUN` | `INICIAR_SISTEMA.bat` línea ~45 | 1=sin órdenes / 0=producción real |
| `PA_RISK_PCT` | `INICIAR_SISTEMA.bat` línea ~46 | % del balance por trade (0.20 = 20%) |
| Puerto IBKR | `INICIAR_SISTEMA.bat` o `AppTWS/config.py` | 7497=Paper, 7496=Live |

---

### ⚠️ Solo tocar en calibración post-sesión (coordinado con Gerardo)

Estos parámetros están en `price_action_scanner/pa_config.yaml`.
**No cambiar sin validar en backtest primero:**

```yaml
pattern_detectors:
  engulfing:
    body_ratio: 0.55        # ← calibrado visualmente 2026-04-06
    confidence_weight: 1.8

lateral_market:
  max_range_points: 10.0    # ← calibrado con zona real de 6.86pts
  lookback_bars: 10         # ← 10 barras × 2m = 20 min de ventana

confluence:
  min_factors_to_trade: 3   # ← mínimo de factores para generar señal
```

---

### 🚫 No tocar (requieren validación técnica completa)

- `confluence.factor_weights.*` — los pesos de confluencia
- `pattern_detectors.*.enabled` — habilitar/deshabilitar patrones
- Cualquier cosa en `signal_router.py`, `confluence_checker.py`, `signal_generator.py`

---

## 8. Reglas de trading que el scanner aplica

Estas reglas están programadas y **no se pueden saltear:**

### Regla 1 — No operar en mercado lateral

```
Si el precio oscila en un rango ≤ 10 puntos por 20+ minutos (10 barras de 2m)
→ El scanner RECHAZA todas las señales automáticamente.
→ Espera ruptura confirmada + vela envolvente post-ruptura.
```

Lo verás en la ventana amarilla como:
```
⚠️ LATERAL detectado en 5m (rango=8.2pts). No operar sin ruptura.
```
Esto es **correcto**. Razón: en lateralidad los contratos se desvalorizan
rápidamente por time decay y los SL se activan por ruido.

---

### Regla 2 — Horario de operación

```
Inicio:  09:45 ET (15 min después de apertura — evitar volatilidad inicial)
Cierre:  15:50 ET (10 min antes del cierre — evitar spike final)
```

Fuera de ese horario, el scanner no genera señales.

---

### Regla 3 — Confluencia mínima

Para que el scanner genere una señal, se necesitan **mínimo 3 factores**:

| Factor | Peso | Descripción |
|--------|------|-------------|
| Precio en zona S/R | 2.0 | Precio dentro del rango de un nivel clave |
| Nivel con historial | 2.5 | El nivel fue respetado 70%+ de veces anteriores |
| Break and Retest | 3.0 | ← **MÁS CRÍTICO**: rompió nivel, volvió a testearlo |
| Precio en extremo | 2.8 | Precio en techo o piso del rango operativo |
| Tendencia 1H | 1.5 | La operación va a favor de la tendencia de 1 hora |
| Tendencia 5M | 1.2 | La operación va a favor de la tendencia de 5 minutos |
| Volumen | 1.2 | El volumen apoya el movimiento |
| Medias móviles | 0.7 | Precio bien posicionado vs MA20/MA200 |

---

### Regla 4 — SL/TP automáticos (puntos del subyacente SPX)

```
Stop Loss:    12 puntos desde entrada
Take Profit 1: 20 puntos desde entrada
Take Profit 2: 35 puntos desde entrada

Trailing Stop: activa cuando la operación llega a +8 pts de ganancia
               sigue el precio a 5 pts de distancia (protege ganancias)
```

El Bot Alfa gestiona el trailing stop automáticamente mediante su **Watchdog**
(visible en la ventana azul).

---

## 9. Post-sesión — qué hacer al cerrar el mercado

Cuando cierra el mercado (4:00 PM ET), el scanner se detiene solo.

### Revisión diaria (5 minutos)

1. **Revisa la ventana amarilla** — busca el resumen final:
   ```
   SESIÓN FINALIZADA
   Duración:   390 minutos
   Detectadas: 12 señales
   Enviadas:   4 órdenes
   Rechazadas: 8
   ```

2. **Revisa el log completo** en:
   ```
   C:\Users\%USERNAME%\price-action-scanner\scanner_output.log
   ```

3. **¿Las señales enviadas fueron correctas?** — Compara con el gráfico en TradingView.

---

### Si encontraste algo raro o una mejora

1. Anota el horario y descripción del patrón que viste
2. Avísale a Gerardo con screenshot de TradingView
3. **No modifiques `pa_config.yaml` directamente** — los cambios se validan
   en backtest antes de aplicarse al sistema del equipo

---

## 10. Solución de problemas comunes

### ❌ "No se puede conectar con Bot Alfa en localhost:8001"
**Causa:** El servidor del Bot Alfa (ventana azul) no está corriendo o tardó en arrancar.

**Solución:**
1. Verifica que la ventana azul dice `Uvicorn running on http://0.0.0.0:8001`
2. Si no, cierra todo y vuelve a ejecutar `INICIAR_SISTEMA.bat`
3. Si la ventana azul da error, verifica que TWS está abierto en puerto 7497

---

### ❌ "TWS / IB Gateway no detectado en puerto 7497"
**Causa:** TWS no está abierto, o está en puerto diferente.

**Solución:**
1. Abre TWS y loguéate
2. En TWS: File → Global Configuration → API → Settings
3. Verifica: ✅ Enable ActiveX and Socket Clients
4. Socket port: `7497` (Paper) o `7496` (Live)
5. ✅ Allow connections from localhost only
6. Haz click en Apply → OK
7. Vuelve a ejecutar `INICIAR_SISTEMA.bat`

---

### ❌ El scanner dice "Sin IBClient - modo demo desactivado" y no analiza
**Causa:** El scanner no tiene conexión de datos en vivo de IBKR.

**Estado actual del sistema:** El scanner obtiene datos directamente de IBKR
a través del `ib_client`. Si no hay `ib_client` conectado, el scanner no analiza.

**Solución:** Asegúrate de que el Bot Alfa está conectado a TWS primero
(ver ventana azul → `IBKR Connected successfully.`).

---

### ❌ `git pull` da error: "Your local changes would be overwritten"
**Causa:** Modificaste algún archivo localmente.

**Solución:**
```cmd
git stash
git pull origin main
```
Si quieres recuperar tus cambios locales después: `git stash pop`

---

### ❌ El dashboard no carga en el navegador
**Causa:** El servidor no arrancó o el puerto 8001 está ocupado.

**Solución:**
1. Verifica que la ventana azul dice `Uvicorn running on http://0.0.0.0:8001`
2. Intenta: http://127.0.0.1:8001 (mismo que localhost)
3. Si el puerto está ocupado, en la ventana azul verás un mensaje.
   Cierra el sistema, espera 10 segundos y vuelve a abrir.

---

### ❌ "Rejected (lateral): 0" en backtest — el detector lateral no funciona
**Estado:** Este bug fue corregido en commit `562039c` (2026-04-06).

Si ves este problema, significa que tienes una versión vieja. Actualiza:
```cmd
cd C:\Users\%USERNAME%\price-action-scanner
git pull origin main
```

---

### ❌ `pip install -r requirements.txt` da error con `aiohttp`
**Solución:**
```cmd
pip install --upgrade pip
pip install aiohttp>=3.9
```

---

## 11. Arquitectura del sistema (para el equipo técnico)

```
┌─────────────────────────────────────────────────────────────┐
│                    INICIAR_SISTEMA.bat                       │
│              (punto de entrada — 1 doble click)              │
└────────────────┬────────────────────┬───────────────────────┘
                 │                    │
    ┌────────────▼──────┐   ┌────────▼──────────────────────┐
    │   server.py       │   │   run_live.py                  │
    │   (Bot Alfa)      │   │   (PA Scanner)                 │
    │   Puerto 8001     │   │   auto_execute=True            │
    │                   │   │   dry_run=1/0 (configurable)  │
    │  FastAPI REST API │   └────────────┬──────────────────┘
    │  IBKR Connection  │                │
    │  Dashboard Web    │   ┌────────────▼──────────────────┐
    │                   │   │   PriceActionScanner           │
    │  Endpoints:       │   │                                │
    │  GET /api/status  │   │  pa_detector.py                │
    │  GET /api/scan    │◄──│  → detecta patrones 2m         │
    │  POST /api/execute│   │                                │
    └───────────────────┘   │  confluence_checker.py         │
              ▲             │  → valida 8 factores           │
              │             │  → filtra mercado lateral      │
              │             │                                │
              │             │  signal_generator.py           │
              │             │  → calcula SL/TP/Trail         │
              │             │  → guarda en DB                │
              │             │                                │
              └─────────────│  signal_router.py  ◄── NUEVO  │
                            │  → GET /api/status (balance)  │
                            │  → GET /api/scan (ask price)  │
                            │  → calcula qty (20% risk)     │
                            │  → POST /api/execute/CALL|PUT │
                            └───────────────────────────────┘
```

### Flujo de una señal (de inicio a orden)

```
Cada 2 minutos (vela cerrada):
  1. Scanner analiza última vela de 2m
  2. pa_detector → ¿hay patrón? (engulfing, pin bar, etc.)
  3. confluence_checker → ¿están los factores de confluencia?
  4. Si lateral → RECHAZAR (regla crítica)
  5. Si < 3 factores → RECHAZAR
  6. signal_generator → calcula SL/TP/Trail → PriceActionSignal
  7. signal_router → GET /api/status → obtiene balance
  8. signal_router → GET /api/scan → obtiene ask del contrato
  9. signal_router → calcula qty = floor(balance × 20% / (ask × 100))
 10. signal_router → POST /api/execute/CALL?qty=2
 11. Bot Alfa (server.py) → scanner.get_best_contracts()
 12. Bot Alfa → order_manager.place_spx_order()
 13. IBKR → Bracket Order (BUY LIMIT + Stop Loss -20%)
 14. Watchdog en Bot Alfa → si profit ≥ 10% → activa Trailing 5%
```

### Archivos del repositorio

```
price-action-scanner/
├── run_live.py                  ← Lanzador de producción (auto_execute)
├── requirements.txt             ← Dependencias del scanner
├── pa_config.yaml               ← Configuración de parámetros
├── price_action_scanner/
│   ├── pa_scanner.py            ← Orquestador principal
│   ├── pa_detector.py           ← Detecta patrones de velas
│   ├── confluence_checker.py    ← Valida confluencia + lateral
│   ├── signal_generator.py      ← Genera señales con SL/TP
│   ├── signal_router.py         ← Puente → Bot Alfa (IBKR)  ← NUEVO
│   ├── pa_signal_schema.py      ← Esquemas de datos
│   ├── pa_backtester.py         ← Backtest histórico
│   └── pa_montecarlo.py         ← Simulación Monte Carlo

Bot Alfa/
├── INICIAR_SISTEMA.bat          ← ARRANQUE PRINCIPAL ← NUEVO
├── AppTWS/
│   ├── server.py                ← API REST + Dashboard (puerto 8001)
│   ├── scanner.py               ← Selección de contratos SPX 0DTE
│   ├── trading.py               ← Ejecución de órdenes bracket
│   └── config.py                ← Parámetros IBKR y trading
```

---

## Resumen ejecutivo — Lo mínimo que necesitas saber

| Acción | Cómo |
|--------|------|
| **Arrancar el sistema** | Doble click en `INICIAR_SISTEMA.bat` |
| **Ver señales en vivo** | Ventana amarilla ("PA SCANNER") |
| **Ver dashboard** | http://localhost:8001 en el navegador |
| **Activar órdenes reales** | Editar `INICIAR_SISTEMA.bat` → `PA_DRY_RUN=0` |
| **Cambiar % de riesgo** | Editar `INICIAR_SISTEMA.bat` → `PA_RISK_PCT=0.15` |
| **Actualizar el sistema** | `git pull origin main` en la carpeta del scanner |
| **Algo no funciona** | Ver sección [10. Solución de problemas](#10-solución-de-problemas-comunes) |

---

*Dudas o problemas no documentados aquí: contactar a Gerardo.*
