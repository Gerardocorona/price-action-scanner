@echo off
title BOT ALFA — Sistema de Operacion SPX 0DTE
color 0A
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo  ============================================================
echo   BOT ALFA — SISTEMA COMPLETO SPX 0DTE
echo   Price Action Scanner + IBKR Execution + Dashboard
echo   Metodologia: Eduardo (PRN-Million plus)
echo  ============================================================
echo.

:: ── CONFIGURACION DE RUTAS ────────────────────────────────────────────────────
:: EDITA ESTAS RUTAS SEGÚN TU INSTALACIÓN:
set BOT_DIR=%~dp0AppTWS
set SCANNER_DIR=%USERPROFILE%\price-action-scanner

:: ── PYTHON: Usar venv del Bot Alfa si existe ──────────────────────────────────
if exist "%~dp0venv\Scripts\python.exe" (
    set PYTHON=%~dp0venv\Scripts\python.exe
    echo  [OK] Python venv encontrado.
) else (
    set PYTHON=python
    echo  [WARN] Venv no encontrado. Usando Python del sistema.
)

:: ── VERIFICAR QUE TWS / IB GATEWAY ESTA CORRIENDO ────────────────────────────
echo  [CHECK] Verificando IBKR TWS en puerto 7497...
%PYTHON% -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('127.0.0.1',7497)); s.close(); exit(r)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ============================================================
    echo   ADVERTENCIA: TWS / IB Gateway no detectado en puerto 7497
    echo   Abre TWS primero y habilita la API (puerto 7497).
    echo   Presiona cualquier tecla para continuar de todas formas...
    echo  ============================================================
    pause >nul
) else (
    echo  [OK] TWS detectado. Listo para operar.
)
echo.

:: ── VERIFICAR QUE EL SCANNER EXISTE ──────────────────────────────────────────
if not exist "%SCANNER_DIR%\run_live.py" (
    echo  [ERROR] No se encontro: %SCANNER_DIR%\run_live.py
    echo  Verifica que price-action-scanner esta en C:\Users\gecor\
    pause
    exit /b 1
)

:: ── MODO DE EJECUCION ─────────────────────────────────────────────────────────
::  PA_DRY_RUN=1  → Solo analiza y loguea, NO envía órdenes (recomendado para nuevos días)
::  PA_DRY_RUN=0  → PRODUCCION — envía órdenes reales a IBKR
::
::  Para cambiar a producción: modifica la línea de abajo a PA_DRY_RUN=0
set PA_DRY_RUN=0
set PA_RISK_PCT=0.20
set BOT_BASE_URL=http://localhost:8001

echo  [INFO] Modo: %PA_DRY_RUN% (1=DRY-RUN / 0=PRODUCCION REAL)
echo  [INFO] Riesgo por trade: %PA_RISK_PCT% (20%%)
echo.

:: ── PASO 1: INICIAR BOT ALFA (API + IBKR) ────────────────────────────────────
echo  [1/3] Iniciando Bot Alfa (API + conexion IBKR)...
start "BOT ALFA — IBKR Server" cmd /k "title BOT ALFA — IBKR Server && color 0B && cd /d "%BOT_DIR%" && echo. && echo  Servidor Bot Alfa iniciando en http://localhost:8001 && echo  Presiona Ctrl+C para detener. && echo. && "%PYTHON%" server.py"

:: Esperar 4 segundos para que el servidor levante
timeout /t 4 /nobreak >nul

:: ── PASO 2: INICIAR PRICE ACTION SCANNER ─────────────────────────────────────
echo  [2/3] Iniciando Price Action Scanner...
start "PA SCANNER — SPX 0DTE" cmd /k "title PA SCANNER — SPX 0DTE && color 0E && cd /d "%SCANNER_DIR%" && echo. && set PA_DRY_RUN=%PA_DRY_RUN% && set PA_RISK_PCT=%PA_RISK_PCT% && set BOT_BASE_URL=%BOT_BASE_URL% && "%PYTHON%" run_live.py"

:: Esperar 2 segundos para que el scanner arranque
timeout /t 2 /nobreak >nul

:: ── PASO 3: ABRIR DASHBOARD EN EL NAVEGADOR ──────────────────────────────────
echo  [3/3] Abriendo dashboard en el navegador...
start "" "http://localhost:8001"

:: ── PANTALLA FINAL ────────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   SISTEMA INICIADO CORRECTAMENTE
echo.
echo   Ventanas activas:
echo     [Amarillo] PA SCANNER    — Detecta señales SPX 0DTE
echo     [Cyan]     BOT ALFA      — API + conexion con IBKR
echo     [Browser]  Dashboard     — http://localhost:8001
echo.
echo   Estado actual: %PA_DRY_RUN%
if "%PA_DRY_RUN%"=="1" (
    echo     DRY-RUN activado — El scanner analiza pero NO opera
    echo     Para operar en real: cambia PA_DRY_RUN=0 en este .bat
) else (
    echo     PRODUCCION activada — Las señales se ejecutan en IBKR
)
echo.
echo   Para cerrar todo: cierra cada ventana por separado.
echo  ============================================================
echo.
pause
