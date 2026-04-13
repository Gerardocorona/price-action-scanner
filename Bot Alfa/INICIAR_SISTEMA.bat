@echo off
title BOT ALFA — Sistema de Operacion SPX 0DTE
color 0A
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo  ============================================================
echo   BOT ALFA — SISTEMA COMPLETO SPX 0DTE
echo   Price Action Scanner + IBKR Execution + Dashboard
echo   Pipeline: TradingView Alert -^> Cloudflare Tunnel -^> Bot Alfa -^> IBKR
echo  ============================================================
echo.

:: ── CONFIGURACION DE RUTAS ────────────────────────────────────────────────────
set BOT_DIR=%~dp0
set SCANNER_DIR=%USERPROFILE%\price-action-scanner
set CLOUDFLARED="C:\Program Files (x86)\cloudflared\cloudflared.exe"

:: ── PYTHON: Usar venv del Bot Alfa si existe ──────────────────────────────────
if exist "%BOT_DIR%venv\Scripts\python.exe" (
    set PYTHON=%BOT_DIR%venv\Scripts\python.exe
    echo  [OK] Python venv encontrado.
) else (
    set PYTHON=python
    echo  [WARN] Venv no encontrado. Usando Python del sistema.
)

:: ── VERIFICAR QUE TWS / IB GATEWAY ESTA CORRIENDO ────────────────────────────
echo  [CHECK] Verificando IBKR Gateway en puerto 7497...
"%PYTHON%" -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('127.0.0.1',7497)); s.close(); exit(r)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ============================================================
    echo   ADVERTENCIA: TWS / IB Gateway no detectado en puerto 7497
    echo   Abre TWS o IB Gateway primero y habilita la API.
    echo   Presiona cualquier tecla para continuar de todas formas...
    echo  ============================================================
    pause >nul
) else (
    echo  [OK] IBKR Gateway detectado en puerto 7497.
)
echo.

:: ── MODO DE EJECUCION ─────────────────────────────────────────────────────────
set PA_DRY_RUN=0
set PA_RISK_PCT=0.20
set BOT_BASE_URL=http://localhost:8001

echo  [INFO] Modo: %PA_DRY_RUN% (1=DRY-RUN / 0=PRODUCCION REAL)
echo  [INFO] Riesgo por trade: %PA_RISK_PCT% (20%%)
echo.

:: ── PASO 1: INICIAR CLOUDFLARE TUNNEL ────────────────────────────────────────
echo  [1/4] Iniciando Cloudflare Tunnel (webhook.bitunixgpt.com)...
if exist %CLOUDFLARED% (
    start "CLOUDFLARE TUNNEL" cmd /c "title CLOUDFLARE TUNNEL — webhook.bitunixgpt.com && color 0D && %CLOUDFLARED% tunnel run bitunixgpt"
    echo  [OK] Cloudflare Tunnel iniciado. URL: https://webhook.bitunixgpt.com
) else (
    echo  [WARN] cloudflared no encontrado. Webhooks de TradingView no funcionaran.
    echo         Instala: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/
)
timeout /t 3 /nobreak >nul
echo.

:: ── PASO 2: INICIAR BOT ALFA (API + IBKR) ────────────────────────────────────
echo  [2/4] Iniciando Bot Alfa (API + conexion IBKR)...
start "BOT ALFA — IBKR Server" cmd /k "title BOT ALFA — IBKR Server && color 0B && cd /d "%BOT_DIR%" && echo. && echo  Servidor Bot Alfa iniciando en http://localhost:8001 && echo  Dashboard: http://localhost:8001/dashboard && echo  Webhook:   https://webhook.bitunixgpt.com/tv-alert && echo. && "%PYTHON%" -m app"

:: Esperar 5 segundos para que el servidor levante y conecte con IBKR
timeout /t 5 /nobreak >nul

:: Verificar que Bot Alfa respondio
"%PYTHON%" -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('127.0.0.1',8001)); s.close(); exit(r)" >nul 2>&1
if errorlevel 1 (
    echo  [WARN] Bot Alfa no responde aun en puerto 8001. Puede tardar unos segundos mas.
) else (
    echo  [OK] Bot Alfa activo en puerto 8001.
)
echo.

:: ── PASO 3: INICIAR PRICE ACTION SCANNER (opcional) ──────────────────────────
echo  [3/4] Verificando Price Action Scanner...
if exist "%SCANNER_DIR%\run_live.py" (
    start "PA SCANNER — SPX 0DTE" cmd /k "title PA SCANNER — SPX 0DTE && color 0E && cd /d "%SCANNER_DIR%" && set PA_DRY_RUN=%PA_DRY_RUN% && set PA_RISK_PCT=%PA_RISK_PCT% && set BOT_BASE_URL=%BOT_BASE_URL% && "%PYTHON%" run_live.py"
    echo  [OK] PA Scanner iniciado.
) else (
    echo  [INFO] PA Scanner no encontrado en %SCANNER_DIR%
    echo         Las senales vienen directamente de TradingView via webhook.
)
timeout /t 2 /nobreak >nul
echo.

:: ── PASO 4: ABRIR DASHBOARD EN EL NAVEGADOR ──────────────────────────────────
echo  [4/4] Abriendo dashboard...
start "" "http://localhost:8001/dashboard"

:: ── PANTALLA FINAL ────────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   SISTEMA INICIADO CORRECTAMENTE
echo.
echo   Ventanas activas:
echo     [Magenta]  CLOUDFLARE     — Tunnel webhook.bitunixgpt.com
echo     [Cyan]     BOT ALFA       — API + IBKR (puerto 8001)
echo     [Amarillo] PA SCANNER     — Deteccion local SPX 0DTE
echo     [Browser]  Dashboard      — http://localhost:8001/dashboard
echo.
echo   Pipeline de senales:
echo     TradingView Alert
echo       -^> https://webhook.bitunixgpt.com/tv-alert
echo       -^> Bot Alfa (localhost:8001)
echo       -^> IBKR Gateway (localhost:7497)
echo       -^> Mercado
echo.
echo   Estado: %PA_DRY_RUN%
if "%PA_DRY_RUN%"=="1" (
    echo     DRY-RUN activado — Analiza pero NO opera
) else (
    echo     PRODUCCION — Las senales se ejecutan en IBKR
)
echo.
echo   Para cerrar todo: cierra cada ventana por separado.
echo  ============================================================
echo.
pause
