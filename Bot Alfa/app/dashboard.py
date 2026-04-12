import datetime
from datetime import date
from typing import List, Dict
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .ib_client import client
from .contract_selector import get_day_state, get_day_plan, on_tradingview_alert
from .ibkr_adapter import ibkr_broker
from .config import get_settings
import logging
import asyncio
import traceback
import os
import json
from .models import ManualCloseRequest
from ib_insync import Option

router = APIRouter()

@router.get("/", response_class=RedirectResponse)
async def get_dashboard_root():
    """Redirige al dashboard principal en vivo."""
    return RedirectResponse(url="/dashboard")

@router.get("/emergency", response_class=HTMLResponse)
async def get_emergency_dashboard():
    """Sirve un mando de emergencia ultraligero."""
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "static", "emergency.html")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return HTMLResponse(content=f"Error cargando mando de emergencia: {e}", status_code=500)

@router.post("/manual_trade")
async def manual_trade(request: Request):
    try:
        data = await request.json()
        ticker = data.get("ticker", "").upper().strip()
        direction = data.get("direction", "").lower().strip() # "long" para CALL, "short" para PUT
        use_trailing_stop = data.get("use_trailing_stop", False)
        
        if not ticker or direction not in ["long", "short"]:
            return JSONResponse(content={"status": "error", "message": "Ticker o dirección inválida"}, status_code=400)
        
        client_timestamp = data.get("client_timestamp")
        if client_timestamp:
            server_time = int(datetime.datetime.now().timestamp() * 1000)
            latency = server_time - client_timestamp
            logging.info(f"[LATENCY] Network Latency (Client -> Server): {latency}ms")

        logging.info(f"[MANUAL] Recibida orden manual: {ticker} {direction} (Trailing: {use_trailing_stop})")
        
        # Parámetros de Riesgo del Dashboard (Default 10% si no vienen)
        tp_percent = data.get("tp_percent", 0.10)
        sl_percent = data.get("sl_percent", 0.10)

        # Ejecutar usando la lógica existente con un timeout extendido (45s)
        try:
            result = await asyncio.wait_for(
                on_tradingview_alert(
                    ticker=ticker,
                    direction=direction,
                    broker=ibkr_broker,
                    use_trailing_stop=use_trailing_stop,
                    trailing_percent=10.0, # Ajustado al 10% como solicitado
                    tp_percent=tp_percent,
                    sl_percent=sl_percent,
                    execution_timeout_seconds=40 # 40s para manual trades
                ),
                timeout=45.0
            )
        except asyncio.TimeoutError:
            logging.error(f"[MANUAL] Timeout de 45s alcanzado para {ticker}")
            return JSONResponse(content={"status": "error", "message": "Timeout de ejecución (45s). La orden podría haberse enviado, verifica TWS."}, status_code=504)
        
        logging.info(f"[MANUAL] Resultado ejecución {ticker}: {result}")
        
        return JSONResponse(content={"status": "ok", "result": result})
    except Exception as e:
        logging.error(f"Error en trade manual: {e}")
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@router.post("/manual_close")
async def manual_close(request: ManualCloseRequest):
    """Cierra manualmente una posición."""
    try:
        logging.info(f"[MANUAL] Solicitud de cierre manual para conId: {request.conId} ({request.symbol})")
        
        # Reconstruir contrato básico
        contract = Option()
        contract.conId = request.conId
        contract.symbol = request.symbol
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        if request.expiry: contract.lastTradeDateOrContractMonth = request.expiry
        if request.strike > 0: contract.strike = request.strike
        if request.right: contract.right = request.right
        
        success = await client.close_position(contract)
        
        if success:
            return JSONResponse(content={"status": "success"})
        else:
            return JSONResponse(content={"status": "error", "message": "No se pudo cerrar la posición"}, status_code=500)
    except Exception as e:
        logging.error(f"Error en cierre manual: {e}")
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


@router.get("/circuit_breaker_status")
async def circuit_breaker_status():
    """Endpoint para consultar el estado del Circuit Breaker."""
    try:
        from .circuit_breaker import get_circuit_breaker_health
        health = get_circuit_breaker_health()
        return JSONResponse(content=health)
    except Exception as e:
        logging.error(f"Error consultando Circuit Breaker: {e}")
        return JSONResponse(
            content={"healthy": True, "state": "unknown", "message": "Circuit Breaker no disponible"},
            status_code=200
        )


from .data_logger import data_logger

@router.get("/analysis", response_class=HTMLResponse)
async def get_analysis():
    try:
        summary = data_logger.get_analysis_summary()
        
        spreads_html = ""
        for s in summary['spreads']:
            spreads_html += f"""
            <tr>
                <td>{s['ticker']}</td>
                <td>{s['avg_spread_pct']:.2f}%</td>
                <td>{s['max_spread_pct']:.2f}%</td>
                <td>{s['sample_count']}</td>
            </tr>
            """
            
        recent_html = ""
        for r in summary['recent_snapshots']:
            recent_html += f"<tr><td>{r['timestamp']}</td><td>{r['ticker']}</td><td>${r['underlying_price']:.2f}</td><td>{r['event_type']}</td></tr>"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>📊 Análisis de Mercado</title>
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
                .card {{ background: #161b22; padding: 20px; border-radius: 10px; border: 1px solid #30363d; margin-bottom: 20px; }}
                h2 {{ color: #58a6ff; }}
                table {{ width: 100%; border-collapse: collapse; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #30363d; }}
                th {{ color: #58a6ff; }}
                .nav {{ margin-bottom: 20px; }}
                .nav a {{ color: #58a6ff; text-decoration: none; margin-right: 20px; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="nav">
                <a href="/dashboard">← Volver al Dashboard</a>
            </div>
            <h1>📊 Análisis de Valoración y Spreads</h1>
            
            <div class="card">
                <h2>📈 Spreads Promedio por Ticker</h2>
                <table>
                    <thead>
                        <tr><th>Ticker</th><th>Spread Promedio</th><th>Spread Máximo</th><th>Muestras</th></tr>
                    </thead>
                    <tbody>{spreads_html or "<tr><td colspan='4'>No hay datos suficientes aún</td></tr>"}</tbody>
                </table>
            </div>

            <div class="card">
                <h2>🕒 Últimos Snapshots</h2>
                <table>
                    <thead>
                        <tr><th>Fecha/Hora</th><th>Ticker</th><th>Precio Subyacente</th><th>Evento</th></tr>
                    </thead>
                    <tbody>{recent_html or "<tr><td colspan='4'>No hay datos capturados</td></tr>"}</tbody>
                </table>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)
    except Exception as e:
        logging.error(f"Error en página de análisis: {e}")
        return HTMLResponse(content=f"<h1>Error</h1><pre>{e}</pre>")

@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    try:
        from .observability import observability
        observability.record_dashboard_access()
        return await asyncio.wait_for(_render_dashboard_logic(), timeout=20.0)
    except asyncio.TimeoutError:
        logging.error("Timeout generando el Dashboard (20s)")
        return HTMLResponse(content="<h1>Error: Timeout (Latencia Alta)</h1><p>El sistema está tardando demasiado en responder. Intenta recargar en unos segundos.</p>", status_code=504)
    except Exception as e:
        logging.error(f"Error crítico en Dashboard: {e}\n{traceback.format_exc()}")
        return HTMLResponse(content=f"<h1>Error Crítico</h1><pre>{e}</pre>", status_code=500)


@router.get("/spx_autolab_status")
async def spx_autolab_status():
    """Retorna el estado actual del SPX Contract AutoLab."""
    try:
        from .spx_contract_autolab import spx_autolab
        return JSONResponse(content=spx_autolab.get_status())
    except Exception as e:
        return JSONResponse(
            content={"status": "ERROR", "message": str(e)},
            status_code=500
        )


def _load_bot_config():
    """Carga la configuración del bot dinámicamente usando el ProfileEngine."""
    config_data = {
        "dream_team": [],
        "tp_percent": 0.10,
        "sl_percent": 0.20,
        "scan_interval": 5,
        "max_capital": 5000.0,
        "strategies": [],
    }
    
    try:
        from TradingEngine.trading_lab.profile_engine import ProfileEngine
        from pathlib import Path
        
        # Detectar ruta del laboratorio
        current_dir = os.path.dirname(os.path.abspath(__file__))
        lab_path = os.path.abspath(os.path.join(current_dir, '..', 'TradingEngine', 'trading_lab'))
        prof_engine = ProfileEngine(base_path=lab_path)
        
        # Cargar estrategias activas del ProfileEngine
        config_data["strategies"] = list(prof_engine.strategies.keys())
        
        # Cargar tickers permitidos (unión de todos los permitidos por las estrategias)
        active_tickers = set()
        for sid in config_data["strategies"]:
            allowed = prof_engine.get_strategy_setting(sid, 'market_scope.allowed_symbols', [])
            active_tickers.update(allowed)
        
        if active_tickers:
            config_data["dream_team"] = sorted(list(active_tickers))
        else:
            settings = get_settings()
            config_data["dream_team"] = [t.strip() for t in settings.active_tickers.split(",")]

        # Configuración global desde settings
        settings = get_settings()
        config_data["tp_percent"] = settings.tp_percent
        config_data["sl_percent"] = settings.sl_percent
        config_data["max_capital"] = settings.virtual_balance if settings.use_virtual_balance else 5000.0
        
    except Exception as e:
        logging.warning(f"Error cargando ProfileEngine en Dashboard: {e}")
        # Fallback a manual si falla el ProfileEngine
        settings = get_settings()
        config_data["dream_team"] = [t.strip() for t in settings.active_tickers.split(",")]
        config_data["strategies"] = ["Midpoint Rebound V2", "Hour Trend Reversal", "CT15 Opening"]
    
    return config_data


async def _render_dashboard_logic():
    settings = get_settings()
    connected = False
    state_capital = 0.0
    state_pnl = 0.0
    active_orders = []
    portfolio = []
    all_fills = []

    
    try:
        connected = client.is_connected()
    except: pass

    try:
        state = get_day_state()
        plan = get_day_plan()
        
        # 1. Obtener ejecuciones frescas de IBKR
        fresh_fills = await asyncio.wait_for(ibkr_broker.get_daily_executions(), timeout=3.0)
        
        # 2. Guardar en historial persistente
        if fresh_fills:
            from .history import history_manager
            history_manager.add_executions(fresh_fills)
            
        # 3. Leer historial completo para mostrar
        from .history import history_manager
        all_fills = history_manager.get_all_movements()
        
        today_str = date.today().strftime("%Y%m%d")
        calculated_capital = 0.0
        for fill in all_fills:
            fill_time = fill.get('time', '')
            if fill_time.startswith(today_str) or today_str in fill_time: 
                if fill.get('side') in ["BOT", "BUY"]:
                    sec_type = fill.get('contract', {}).get('secType', '')
                    has_option_fields = fill.get('contract', {}).get('strike', 0) and fill.get('contract', {}).get('right', '')
                    mult = 100 if (sec_type == 'OPT' or has_option_fields) else 1
                    calculated_capital += (fill.get('price', 0) * fill.get('shares', 0) * mult)
        
        state_capital = state.used_capital if state.used_capital > 0 else calculated_capital
    except Exception as e:
        pass

    if connected:
        try:
            active_orders = await asyncio.wait_for(ibkr_broker.get_open_trades(), timeout=3.0)
            portfolio = await asyncio.wait_for(client.get_portfolio(), timeout=3.0)
            
            unrealized_pnl_total = 0.0
            if not settings.use_virtual_balance:
                for item in portfolio:
                    if item.get('position', 0) != 0:
                        unrealized_pnl_total += (item.get('unrealizedPNL') or 0.0)
        except: pass

    current_balance = 0.0
    initial_balance = 0.0
    pct_change = 0.0
    is_virtual = settings.use_virtual_balance
    
    try:
        if is_virtual:
            from .history import history_manager
            
            initial_balance = settings.virtual_balance
            cumulative_cash_flow = history_manager.get_cumulative_cash_flow()
            
            market_value = 0.0
            if connected:
                try:
                    portfolio_items = await asyncio.wait_for(client.get_portfolio(), timeout=2.0)
                    for item in portfolio_items:
                        mkt_val = item.get('marketValue')
                        if mkt_val is not None:
                            market_value += float(mkt_val)
                        else:
                            pos = float(item.get('position', 0))
                            price = float(item.get('marketPrice', 0))
                            market_value += (pos * price * 100)
                except Exception as e:
                    logging.warning(f"No se pudo obtener valor de mercado para balance virtual: {e}")

            current_balance = initial_balance + cumulative_cash_flow + market_value
            
            state_pnl = current_balance - initial_balance
            
            if initial_balance > 0:
                pct_change = (state_pnl / initial_balance) * 100
        else:
            if connected:
                current_balance = await asyncio.wait_for(ibkr_broker.get_account_balance(), timeout=3.0)
                if plan and plan.balance > 0:
                    initial_balance = plan.balance
                    state_pnl = current_balance - initial_balance
                    pct_change = (state_pnl / initial_balance) * 100
    except Exception as e:
        logging.error(f"Error calculando balance: {e}")

    # Load bot config
    bot_config = _load_bot_config()

    # Status displays
    connection_status = "ONLINE" if connected else "OFFLINE"
    connection_class = "status-ok" if connected else "status-err"
    dot_class = "online" if connected else "offline"
    pnl_color = "var(--success)" if state_pnl >= 0 else "var(--danger)"
    pnl_formatted = f"${state_pnl:+.2f}"
    pct_class = "pct-up" if pct_change >= 0 else "pct-down"
    
    # Circuit Breaker status
    cb_status = "OK"
    cb_color = "var(--success)"
    try:
        from .circuit_breaker import get_circuit_breaker_health
        cb_health = get_circuit_breaker_health()
        if not cb_health.get("healthy", True):
            cb_status = "TRIPPED"
            cb_color = "var(--danger)"
    except: pass
    
    # Risk Manager status
    risk_status = "SAFE"
    risk_color = "var(--success)"
    try:
        from .risk_manager import risk_manager
        risk_info = risk_manager.get_status()
        if risk_info.get("max_loss_triggered"):
            risk_status = "BLOCKED"
            risk_color = "var(--danger)"
    except: pass
    
    # Build tables
    orders_html = _build_orders_table(active_orders)
    portfolio_html = _build_portfolio_table(portfolio)
    movements_html = _build_movements_table(all_fills)
    
    # Strategy badges HTML
    strategy_badges = ""
    for strat in bot_config.get("strategies", []):
        strat_lower = strat.lower()
        if "rpm" in strat_lower or "midpoint" in strat_lower:
            strategy_badges += f'<span class="strategy-badge rpm">📐 {strat}</span>'
        elif "htr" in strat_lower or "trend" in strat_lower:
            strategy_badges += f'<span class="strategy-badge htr">🔄 {strat}</span>'
        elif "ct15" in strat_lower:
            strategy_badges += f'<span class="strategy-badge ct15">⚡ {strat}</span>'
        elif "alpha" in strat_lower:
            strategy_badges += f'<span class="strategy-badge alpha">🔮 {strat}</span>'
        elif "mean_rev" in strat_lower:
            strategy_badges += f'<span class="strategy-badge mrev">📉 {strat}</span>'
        elif "vps" in strat_lower:
            strategy_badges += f'<span class="strategy-badge vps">🛡️ {strat}</span>'
        else:
            strategy_badges += f'<span class="strategy-badge other">⚙️ {strat}</span>'
    
    if is_virtual:
        strategy_badges += f'<span class="strategy-badge sim">🧪 SIMULACIÓN ${initial_balance/1000:.0f}k</span>'
    
    # Ticker chips
    ticker_chips = ""
    for t in bot_config.get("dream_team", []):
        ticker_chips += f'<span class="ticker-chip">{t}</span>'
    
    # Config items
    tp_pct = settings.tp_percent * 100
    sl_pct = settings.sl_percent * 100
    cap_frac = settings.capital_fraction * 100
    per_trade = settings.per_trade_fraction * 100
    daily_tp = settings.daily_tp_target * 100
    max_loss = settings.max_daily_loss_pct * 100
    
    # Today's stats
    today_str_check = date.today().strftime("%Y%m%d")
    today_trades = 0
    today_realized = 0.0
    for fill in all_fills:
        ft = fill.get('time', '')
        if ft.startswith(today_str_check) or today_str_check in ft:
            today_trades += 1
            today_realized += fill.get('realizedPNL', 0.0)
    
    # Broker mode indicator
    broker_mode_label = "PAPER" if settings.broker_is_paper else "REAL"
    broker_mode_class = "sim" if settings.broker_is_paper else "live-real"
    
    # Current timestamp for display
    now = datetime.datetime.now()
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>INVESTEP Elite | Scan-Driven Trading System</title>
        <link rel="stylesheet" href="/static/dashboard.css">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    </head>
    <body>
        <div class="glass-container fade-in">
            <header>
                <div class="header-title">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <h1>INVESTEP ELITE <span style="font-weight: 300; opacity: 0.7;">| SCAN-DRIVEN SYSTEM</span></h1>
                        <span class="strategy-badge {broker_mode_class}" style="font-size:10px; padding:2px 8px; border: 1px solid currentColor;">BROKER: {broker_mode_label}</span>
                    </div>
                    <p class="version">Engine V6 · Layered Architecture · Dual Strategy</p>
                    <div class="strategy-badges">
                        {strategy_badges}
                    </div>
                </div>
                <div class="status-indicator">
                    <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: rgba(255,255,255,0.5);">IBKR CONNECTION</div>
                    <div class="{connection_class}" style="font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px;">
                        <span class="status-dot {dot_class}"></span> {connection_status}
                    </div>
                    <div style="font-size: 11px; color: rgba(255,255,255,0.4);">{now.strftime("%H:%M:%S")} EST</div>
                </div>
            </header>

            <div class="stats-grid">
                <div class="card">
                    <div class="metric-label">CASH BALANCE</div>
                    <div class="metric-value">${current_balance:,.2f}</div>
                    <div class="metric-sub">
                        <span class="pct-badge {pct_class}">{pct_change:+.2f}%</span>
                        <span style="color: var(--text-muted);">vs. inicio</span>
                    </div>
                </div>
                <div class="card">
                    <div class="metric-label">CAPITAL DEPLOYED</div>
                    <div class="metric-value">${state_capital:,.2f}</div>
                    <div class="metric-sub">
                        <span style="color: var(--text-muted);">{cap_frac:.0f}% alloc · {per_trade:.0f}% per trade</span>
                    </div>
                </div>
                <div class="card">
                    <div class="metric-label">REALIZED P&L</div>
                    <div class="metric-value" style="color: {pnl_color};">{pnl_formatted}</div>
                    <div class="metric-sub">
                        <span style="color: var(--text-muted);">{today_trades} trades hoy · ${today_realized:+.2f} realized</span>
                    </div>
                </div>
                <div class="card">
                    <div class="metric-label">SYSTEM HEALTH</div>
                    <div class="metric-value" style="color: {cb_color};">{"✓ READY" if cb_status == "OK" and risk_status == "SAFE" else "⚠ ALERT"}</div>
                    <div class="metric-sub" style="flex-direction: column; align-items: flex-start; gap: 3px;">
                        <span>CB: <span style="color: {cb_color}; font-weight: 600;">{cb_status}</span></span>
                        <span>Risk: <span style="color: {risk_color}; font-weight: 600;">{risk_status}</span></span>
                    </div>
                </div>
            </div>

            <div class="main-grid">
                <div class="left-col">
                    <div class="card" style="margin-bottom: 20px;">
                        <div class="section-title">ACTIVE POSITIONS & ORDERS</div>
                        <div style="margin-bottom: 20px;">
                            <h4 style="font-size: 11px; color: var(--accent); margin-bottom: 10px; letter-spacing: 1px;">LIVE PORTFOLIO</h4>
                            {portfolio_html}
                        </div>
                        <div>
                            <h4 style="font-size: 11px; color: var(--warning); margin-bottom: 10px; letter-spacing: 1px;">OPEN ORDERS</h4>
                            {orders_html}
                        </div>
                    </div>

                    <div class="card">
                        <div class="section-title">DAILY LOG & AUDIT</div>
                        <div style="max-height: 500px; overflow-y: auto;">
                            {movements_html}
                        </div>
                    </div>
                </div>

                <div class="right-col">
                    <div class="card" style="margin-bottom: 16px; border: 1px solid var(--border-accent);">
                        <div class="section-title">MANUAL ELITE TERMINAL</div>
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div style="display:flex; gap: 10px; align-items: center;">
                                <input type="text" id="manualTicker" class="input-field" style="flex: 1;" value="SPX" placeholder="TICKER (e.g. TSLA)" onfocus="stopRefresh()" onblur="startRefresh()">
                                <div style="display:flex; align-items:center; gap: 5px; font-size: 11px; color: var(--text-dim);">
                                    <input type="checkbox" id="trailingStop" style="width:14px; height:14px; accent-color: var(--accent);">
                                    <label for="trailingStop">TRAIL</label>
                                </div>
                            </div>
                            <div style="display:flex; gap: 10px;">
                                <button class="btn btn-primary" style="flex: 1; background: var(--success); box-shadow: 0 2px 8px rgba(72,187,120,0.2);" onclick="sendManualTrade('long')">BUY CALL</button>
                                <button class="btn btn-primary" style="flex: 1; background: var(--danger); box-shadow: 0 2px 8px rgba(252,129,129,0.2);" onclick="sendManualTrade('short')">BUY PUT</button>
                            </div>
                        </div>
                        <p id="manualStatus" style="font-size: 11px; margin-top: 10px; color: var(--text-muted);"></p>
                    </div>

                    <div class="card" style="margin-bottom: 16px;">
                        <div class="section-title">SYSTEM CONFIG</div>
                        <div class="config-grid">
                            <div class="config-item">
                                <span class="label">TP</span>
                                <span class="value" style="color: var(--success);">{tp_pct:.0f}%</span>
                            </div>
                            <div class="config-item">
                                <span class="label">SL</span>
                                <span class="value" style="color: var(--danger);">{sl_pct:.0f}%</span>
                            </div>
                            <div class="config-item">
                                <span class="label">Daily TP</span>
                                <span class="value">{daily_tp:.0f}%</span>
                            </div>
                            <div class="config-item">
                                <span class="label">Max Loss</span>
                                <span class="value" style="color: var(--danger);">{max_loss:.0f}%</span>
                            </div>
                        </div>
                        
                        <div class="webhook-status active" style="margin-top: 12px; color: var(--success); font-weight: bold;">
                            <span>⚡</span> TV WEBHOOK: ACTIVE
                        </div>
                    </div>

                    <div class="card" style="margin-bottom: 16px;">
                        <div class="section-title">DREAM TEAM ({len(bot_config.get('dream_team', []))} tickers)</div>
                        <div class="ticker-grid">
                            {ticker_chips}
                        </div>
                    </div>

                    <div class="card">
                        <div class="section-title">QUICK LINKS</div>
                        <div style="display: flex; flex-direction: column; gap: 8px;">
                            <a href="/analysis" class="btn btn-outline" style="text-decoration:none; text-align:center;">📊 MARKET ANALYSIS</a>
                            <a href="/status/detailed" class="btn btn-outline" style="text-decoration:none; text-align:center;">📋 SYSTEM JSON</a>
                            <button onclick="window.location.reload()" class="btn btn-outline">🔄 REFRESH NOW</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let refreshInterval = setInterval(() => window.location.reload(), 12000);
            function stopRefresh() {{ clearInterval(refreshInterval); }}
            function startRefresh() {{ 
                clearInterval(refreshInterval); 
                refreshInterval = setInterval(() => window.location.reload(), 12000); 
            }}
            
            window.onload = function() {{
                const tickerInput = document.getElementById('manualTicker');
                if (tickerInput) {{
                    tickerInput.value = "SPX";
                }}
            }};
            
            async function sendManualTrade(direction) {{
                const ticker = document.getElementById('manualTicker').value.toUpperCase().trim();
                const useTrailing = document.getElementById('trailingStop').checked;
                const status = document.getElementById('manualStatus');
                
                if (!ticker) return alert("Ticker requerido");
                
                status.innerText = "⏳ Procesando orden para " + ticker + "...";
                status.style.color = "var(--warning)";
                
                try {{
                    const res = await fetch('/manual_trade', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ ticker, direction, use_trailing_stop: useTrailing, client_timestamp: Date.now() }})
                    }});
                    const data = await res.json();
                    if (data.status === 'ok') {{
                        status.innerText = "✅ Orden aceptada correctamente.";
                        status.style.color = "var(--success)";
                        setTimeout(() => window.location.reload(), 2000);
                    }} else {{
                        status.innerText = "❌ Error: " + data.message;
                        status.style.color = "var(--danger)";
                    }}
                }} catch(e) {{ 
                    status.innerText = "❌ Error de red.";
                    status.style.color = "var(--danger)";
                }}
            }}

            async function closePosition(conId, symbol) {{
                if (!confirm("🚨 ¿Seguro que quieres cerrar " + symbol + "?")) return;
                try {{
                    await fetch('/manual_close', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ conId, symbol }})
                    }});
                    window.location.reload();
                }} catch(e) {{ alert("Error"); }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

def _build_orders_table(orders):
    if not orders: return '<p style="color: var(--text-muted); font-size: 12px;">Sin órdenes activas</p>'
    rows = ""
    for o in orders:
        c = o.get('contract', {})
        ord = o.get('order', {})
        st = o.get('orderStatus', {})
        
        symbol = c.get('symbol', '???')
        strike = c.get('strike')
        right = c.get('right')
        expiry = c.get('lastTradeDateOrContractMonth') or c.get('expiry')
        
        if strike and right and expiry:
            try:
                exp_dt = datetime.datetime.strptime(str(expiry), "%Y%m%d")
                fmt_expiry = exp_dt.strftime("%d%b%y")
            except:
                fmt_expiry = expiry
            pretty_name = f"{symbol} {fmt_expiry} {strike} {right}"
        else:
            pretty_name = c.get('localSymbol') or symbol

        rows += f"""
        <tr>
            <td style='font-weight: 600; color: var(--accent);'>{pretty_name}</td>
            <td><span class="badge {'badge-buy' if ord.get('action')=='BUY' else 'badge-sell'}">{ord.get('action')}</span></td>
            <td>{ord.get('totalQuantity')}</td>
            <td><span style="opacity: 0.8;">{st.get('status')}</span></td>
        </tr>"""
    return f"<table><thead><tr><th>CONTRATO</th><th>ACCIÓN</th><th>CANT</th><th>ESTADO</th></tr></thead><tbody>{rows}</tbody></table>"

def _build_portfolio_table(portfolio):
    if not portfolio: return '<p style="color: var(--text-muted); font-size: 12px;">No hay posiciones abiertas</p>'
    rows = ""
    for item in portfolio:
        c = item.get('contract', {})
        symbol = c.get('symbol', '???')
        strike = c.get('strike')
        right = c.get('right')
        expiry = c.get('lastTradeDateOrContractMonth') or c.get('expiry')
        
        if strike and right and expiry:
            try:
                exp_dt = datetime.datetime.strptime(str(expiry), "%Y%m%d")
                fmt_expiry = exp_dt.strftime("%d%b%y")
            except:
                fmt_expiry = expiry
            pretty_name = f"{symbol} {fmt_expiry} {strike} {right}"
        else:
            pretty_name = c.get('localSymbol') or symbol

        pnl = item.get('unrealizedPNL', 0)
        pnl_color = "var(--success)" if pnl >= 0 else "var(--danger)"
        
        # Calculate PnL percentage
        avg_cost = item.get('averageCost', 0)
        position = item.get('position', 0)
        invested = avg_cost * abs(position) if avg_cost and position else 0
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0

        rows += f"""
        <tr>
            <td style='font-weight: 600; color: var(--accent);'>{pretty_name}</td>
            <td>{item.get('position')}</td>
            <td>${item.get('averageCost'):.2f}</td>
            <td style='color: {pnl_color}; font-weight: 700;'>${pnl:+.2f} <span style="font-size:10px; opacity:0.7;">({pnl_pct:+.1f}%)</span></td>
            <td><button class="btn btn-danger" style="padding: 4px 10px; font-size: 10px;" onclick=\"closePosition({c.get('conId')}, '{symbol}')\">CERRAR</button></td>
        </tr>"""
    return f"<table><thead><tr><th>CONTRATO</th><th>POS</th><th>AVG COST</th><th>PNL</th><th>ACCIÓN</th></tr></thead><tbody>{rows}</tbody></table>"

def _build_movements_table(fills):
    if not fills: return '<p style="color: var(--text-muted); font-size: 12px;">Sin movimientos registrados</p>'
    rows = ""
    sorted_fills = sorted(fills, key=lambda x: x.get('time', ''), reverse=True)
    
    for f in sorted_fills[:30]:
        raw_time = f.get('time', '')
        try:
            dt_obj = datetime.datetime.strptime(raw_time, "%Y%m%d  %H:%M:%S")
            fmt_time = dt_obj.strftime("%H:%M:%S")
            fmt_date = dt_obj.strftime("%m/%d")
        except:
            fmt_time = raw_time
            fmt_date = ""

        contract = f.get('contract', {})
        symbol = f.get('symbol') or contract.get('symbol') or '???'
        
        strike = contract.get('strike')
        right = contract.get('right')
        expiry = contract.get('expiry') or contract.get('lastTradeDateOrContractMonth')
        
        if strike and right and expiry:
            try:
                exp_dt = datetime.datetime.strptime(str(expiry), "%Y%m%d")
                fmt_expiry = exp_dt.strftime("%d%b")
                pretty_name = f"{symbol} {fmt_expiry} {strike}{right}"
            except:
                pretty_name = f"{symbol} {expiry} {strike}{right}"
        else:
            pretty_name = f.get('localSymbol') or contract.get('localSymbol') or symbol
        
        side = f.get('side', '???').upper()
        side_label = "BUY" if side in ["BOT", "BUY"] else "SELL"
        side_class = "badge-buy" if side_label == "BUY" else "badge-sell"
        
        pnl = f.get('realizedPNL', 0.0)
        pnl_html = "<span style='color: var(--text-muted);'>—</span>"
        if pnl != 0:
            color = "var(--success)" if pnl > 0 else "var(--danger)"
            pnl_html = f"<span style='color: {color}; font-weight: 700;'>${pnl:+.2f}</span>"

        time_display = f"{fmt_date} {fmt_time}" if fmt_date else fmt_time

        rows += f"""
        <tr>
            <td style="font-family: 'Inter', monospace; font-size: 11px; color: var(--text-dim);">{time_display}</td>
            <td style="font-weight: 600; color: var(--accent);">{pretty_name}</td>
            <td><span class="badge {side_class}">{side_label}</span></td>
            <td>{f.get('shares')}</td>
            <td>${f.get('price'):.2f}</td>
            <td style="font-size: 11px; color: var(--text-dim);">${(f.get('price',0) * f.get('shares',0) * (100 if (f.get('contract',{}).get('secType') == 'OPT' or (f.get('contract',{}).get('strike',0) and f.get('contract',{}).get('right',''))) else 1)):,.0f}</td>
            <td style="text-align: right;">{pnl_html}</td>
        </tr>"""

    return f"""
    <table style="margin-top: 0;">
        <thead>
            <tr>
                <th>HORA</th>
                <th>CONTRATO</th>
                <th>LADO</th>
                <th>CANT</th>
                <th>PRECIO</th>
                <th>TOTAL</th>
                <th style="text-align: right;">PNL</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>"""
