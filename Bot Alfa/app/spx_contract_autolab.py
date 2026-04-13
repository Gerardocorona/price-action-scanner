"""
SPX Contract AutoLab — Motor de Mejora Continua para Selección de Contratos 0DTE.

Ciclo evolutivo:
1. Acumula N trades cerrados de SPX (window_size, default 10).
2. Evalúa el retorno porcentual promedio de los trades con el Champion actual.
3. Genera un Challenger: muta UN parámetro del Champion.
4. Compara retorno promedio Champion vs Challenger.
5. Si Challenger supera al Champion por el umbral → promover y actualizar config.

Este módulo NO modifica el flujo de ejecución de órdenes.
Solo lee del historial (history.py) y escribe en config/spx_selector_config.json.
"""

import json
import copy
import random
import logging
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("ibg.spx_autolab")
logger.propagate = True
if not logger.handlers:
    logger.setLevel(logging.INFO)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "spx_selector_config.json"


class SPXContractAutoLab:
    """
    Motor evolutivo para optimizar la selección de contratos SPX 0DTE.
    
    Lee trades cerrados del historial, evalúa el rendimiento del Champion actual,
    genera Challengers con mutaciones de un parámetro, y promueve si el Challenger
    demuestra mejor rendimiento.
    """

    def __init__(self):
        self.config = self._load_config()
        if not self.config:
            logger.error("❌ [SPX_AUTOLAB] No se pudo cargar spx_selector_config.json")
            return
        self._last_evaluated_count = 0
        logger.info(
            f"🔬 [SPX_AUTOLAB] Iniciado | "
            f"Window: {self.config['autolab']['window_size']} trades | "
            f"Batallas previas: {self.config['history']['total_battles']}"
        )

    def _load_config(self) -> Optional[dict]:
        """Carga la configuración desde disco."""
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"❌ [SPX_AUTOLAB] Error cargando config: {e}")
        return None

    def _save_config(self):
        """Persiste la configuración actualizada a disco."""
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, default=str)
            logger.info("💾 [SPX_AUTOLAB] Config guardada.")
        except Exception as e:
            logger.error(f"❌ [SPX_AUTOLAB] Error guardando config: {e}")

    # ================================================================
    #   EXTRACCIÓN DE TRADES SPX DEL HISTORIAL
    # ================================================================
    def get_spx_closed_trades(self, history_cache: List[dict]) -> List[dict]:
        """
        Extrae trades cerrados de SPX del historial.
        
        Un trade cerrado es un par BUY + SLD del mismo contrato SPX.
        Retorna lista de trades con: entry_price, exit_price, return_pct, 
        strike, contract_mid (precio mid al momento de la selección).
        """
        # Separar BUYs y SLDs de SPX
        buys = []
        sells = []
        
        for entry in history_cache:
            symbol = entry.get("symbol", "")
            contract = entry.get("contract", {})
            contract_symbol = contract.get("symbol", "") if isinstance(contract, dict) else ""
            
            if symbol.upper() != "SPX" and contract_symbol.upper() != "SPX":
                continue
            
            side = entry.get("side", "")
            if side in ("BUY", "BOT"):
                buys.append(entry)
            elif side in ("SELL", "SLD"):
                sells.append(entry)
        
        # Emparejar BUY con SELL por localSymbol o permId
        closed_trades = []
        used_sell_ids = set()
        
        for buy in buys:
            buy_local = buy.get("localSymbol", "") or (buy.get("contract", {}) or {}).get("localSymbol", "")
            buy_price = float(buy.get("price", 0))
            buy_bid = float(buy.get("bid", 0) or 0)
            buy_ask = float(buy.get("ask", 0) or 0)
            
            if buy_price <= 0:
                continue
            
            # Buscar la venta correspondiente
            for i, sell in enumerate(sells):
                if i in used_sell_ids:
                    continue
                sell_local = sell.get("localSymbol", "") or (sell.get("contract", {}) or {}).get("localSymbol", "")
                
                if sell_local == buy_local and sell_local:
                    sell_price = float(sell.get("price", 0))
                    realized_pnl = float(sell.get("realizedPNL", 0))
                    
                    if sell_price <= 0:
                        continue
                    
                    # Calcular retorno porcentual
                    return_pct = (sell_price - buy_price) / buy_price
                    
                    # Precio mid al momento de la selección (bid+ask)/2 o el precio de compra
                    if buy_bid > 0 and buy_ask > 0:
                        contract_mid = (buy_bid + buy_ask) / 2.0
                    else:
                        contract_mid = buy_price
                    
                    contract_info = buy.get("contract", {}) or {}
                    
                    closed_trades.append({
                        "entry_price": buy_price,
                        "exit_price": sell_price,
                        "contract_mid": contract_mid,
                        "return_pct": return_pct,
                        "realized_pnl": realized_pnl,
                        "strike": float(contract_info.get("strike", 0)),
                        "right": contract_info.get("right", ""),
                        "expiry": contract_info.get("expiry", ""),
                        "local_symbol": buy_local,
                        "buy_time": buy.get("time", ""),
                        "sell_time": sell.get("time", ""),
                        "spread_at_entry": (buy_ask - buy_bid) / contract_mid if contract_mid > 0 and buy_bid > 0 and buy_ask > 0 else 0.0,
                    })
                    used_sell_ids.add(i)
                    break
        
        return closed_trades

    # ================================================================
    #   GENERACIÓN DE CHALLENGER
    # ================================================================
    def generate_challenger(self) -> dict:
        """
        Genera un Challenger mutando UN parámetro aleatorio del Champion.
        La mutación respeta los bounds definidos en la configuración.
        """
        champion = copy.deepcopy(self.config["champion"])
        bounds = self.config["bounds"]
        
        # Elegir un parámetro aleatorio para mutar
        mutable_params = list(bounds.keys())
        param_to_mutate = random.choice(mutable_params)
        
        current_val = champion[param_to_mutate]
        bound = bounds[param_to_mutate]
        step = bound["step"]
        
        # Mutación: +step o -step (aleatorio)
        direction = random.choice([-1, 1])
        new_val = round(current_val + (direction * step), 4)
        
        # Clamp dentro de los bounds
        new_val = max(bound["min"], min(bound["max"], new_val))
        
        # Asegurar que sweet_spot esté dentro de [price_min, price_max]
        challenger = copy.deepcopy(champion)
        challenger[param_to_mutate] = new_val
        
        if challenger["sweet_spot"] < challenger["price_min"]:
            challenger["sweet_spot"] = challenger["price_min"]
        if challenger["sweet_spot"] > challenger["price_max"]:
            challenger["sweet_spot"] = challenger["price_max"]
        
        logger.info(
            f"🧬 [SPX_AUTOLAB] Challenger generado: "
            f"{param_to_mutate} {current_val:.4f} → {new_val:.4f} "
            f"({'↑' if direction > 0 else '↓'})"
        )
        
        return challenger

    # ================================================================
    #   EVALUACIÓN: SCORING SIMULADO
    # ================================================================
    def _simulate_score(self, trade: dict, params: dict) -> float:
        """
        Simula qué score habría tenido el contrato de este trade
        bajo un conjunto de parámetros dado.
        
        Usa el contract_mid del trade como proxy del precio del contrato
        al momento de la selección.
        """
        import math
        
        mid = trade["contract_mid"]
        spread = trade.get("spread_at_entry", 0.0)
        
        price_min = params["price_min"]
        price_max = params["price_max"]
        sweet_spot = params["sweet_spot"]
        max_spread_pct = params["max_spread_pct"]
        
        # ¿El contrato habría pasado el filtro con estos parámetros?
        if not (price_min <= mid <= price_max):
            return -1.0  # Fuera de rango → no habría sido seleccionado
        if spread > max_spread_pct:
            return -1.0  # Spread excesivo → descartado
        
        # Calcular score (misma lógica que _score_spx_candidate)
        range_width = price_max - price_min
        if range_width > 0:
            distance = abs(mid - sweet_spot) / range_width
            score_price = max(0.0, 1.0 - distance)
        else:
            score_price = 1.0
        
        if max_spread_pct > 0:
            score_spread = max(0.0, 1.0 - (spread / max_spread_pct))
        else:
            score_spread = 1.0
        
        if range_width > 0:
            moneyness_norm = (mid - price_min) / range_width
            score_moneyness = math.exp(-((moneyness_norm - 0.6) ** 2) / 0.18)
        else:
            score_moneyness = 0.5
        
        w_price = params["w_price"]
        w_spread = params["w_spread"]
        w_moneyness = params["w_moneyness"]
        total_weight = w_price + w_spread + w_moneyness
        
        return (w_price * score_price + w_spread * score_spread + w_moneyness * score_moneyness) / total_weight

    def evaluate_params(self, trades: List[dict], params: dict) -> dict:
        """
        Evalúa un conjunto de parámetros contra los trades cerrados.
        
        Retorna métricas de rendimiento:
        - avg_return_pct: retorno porcentual promedio de trades que habrían sido seleccionados
        - win_rate: porcentaje de trades con retorno positivo
        - selected_count: cuántos trades habrían pasado el filtro
        - avg_score: score promedio de los trades seleccionados
        """
        selected_returns = []
        scores = []
        
        for trade in trades:
            score = self._simulate_score(trade, params)
            if score >= 0:  # Habría sido seleccionado
                selected_returns.append(trade["return_pct"])
                scores.append(score)
        
        if not selected_returns:
            return {
                "avg_return_pct": 0.0,
                "win_rate": 0.0,
                "selected_count": 0,
                "avg_score": 0.0,
            }
        
        wins = sum(1 for r in selected_returns if r > 0)
        
        return {
            "avg_return_pct": sum(selected_returns) / len(selected_returns),
            "win_rate": wins / len(selected_returns),
            "selected_count": len(selected_returns),
            "avg_score": sum(scores) / len(scores),
        }

    # ================================================================
    #   BATALLA: CHAMPION vs CHALLENGER
    # ================================================================
    def execute_battle(self, trades: List[dict]) -> Optional[dict]:
        """
        Ejecuta una batalla Champion vs Challenger.
        
        1. Evalúa el Champion actual contra los trades.
        2. Genera un Challenger.
        3. Evalúa el Challenger contra los mismos trades.
        4. Si el Challenger supera al Champion por el umbral → promover.
        
        Retorna el resultado de la batalla o None si no hay suficientes trades.
        """
        if not self.config:
            return None
        
        window_size = self.config["autolab"]["window_size"]
        threshold = self.config["autolab"]["promotion_threshold"]
        
        if len(trades) < window_size:
            logger.info(
                f"📊 [SPX_AUTOLAB] {len(trades)}/{window_size} trades acumulados. "
                f"Esperando {window_size - len(trades)} más para la próxima batalla."
            )
            return None
        
        # Usar solo los últimos N trades (ventana)
        window_trades = trades[-window_size:]
        
        # Evaluar Champion
        champion = self.config["champion"]
        champion_metrics = self.evaluate_params(window_trades, champion)
        
        # Generar y evaluar Challenger
        challenger = self.generate_challenger()
        challenger_metrics = self.evaluate_params(window_trades, challenger)
        
        # Comparar
        champ_return = champion_metrics["avg_return_pct"]
        chall_return = challenger_metrics["avg_return_pct"]
        
        # El Challenger debe superar al Champion por el umbral relativo
        promoted = False
        if champ_return > 0:
            improvement = (chall_return - champ_return) / abs(champ_return)
            promoted = improvement > threshold
        elif chall_return > champ_return:
            # Si el Champion tiene retorno negativo y el Challenger es mejor → promover
            promoted = True
        
        battle_result = {
            "date": dt.datetime.now().isoformat(),
            "window_trades": len(window_trades),
            "champion_metrics": champion_metrics,
            "challenger_metrics": challenger_metrics,
            "challenger_params": challenger,
            "promoted": promoted,
            "improvement_pct": round(
                ((chall_return - champ_return) / abs(champ_return) * 100) if champ_return != 0 else 0, 2
            ),
        }
        
        # Registrar batalla
        self.config["history"]["battles"].append(battle_result)
        self.config["history"]["total_battles"] += 1
        self.config["history"]["last_battle_date"] = battle_result["date"]
        
        if promoted:
            # Promover Challenger a Champion
            old_champion = copy.deepcopy(self.config["champion"])
            self.config["champion"] = challenger
            self.config["version"] = self.config.get("version", 1) + 1
            self.config["history"]["total_promotions"] += 1
            
            logger.info(
                f"🏆 [SPX_AUTOLAB] ¡CHALLENGER PROMOVIDO! (Batalla #{self.config['history']['total_battles']})\n"
                f"  Champion retorno: {champ_return:.2%} → Challenger retorno: {chall_return:.2%}\n"
                f"  Mejora: {battle_result['improvement_pct']:.1f}%\n"
                f"  Cambios: {self._diff_params(old_champion, challenger)}"
            )
            
            # Forzar recarga del config en el selector
            try:
                from .contract_selector import reload_spx_config
                reload_spx_config()
            except Exception:
                pass
        else:
            logger.info(
                f"📊 [SPX_AUTOLAB] Batalla #{self.config['history']['total_battles']}: "
                f"Champion retiene (retorno {champ_return:.2%} vs Challenger {chall_return:.2%})"
            )
        
        # Guardar siempre (batalla registrada)
        self._save_config()
        
        return battle_result

    def _diff_params(self, old: dict, new: dict) -> str:
        """Genera un string legible con las diferencias entre dos configs."""
        diffs = []
        for key in old:
            if old[key] != new.get(key):
                diffs.append(f"{key}: {old[key]} → {new[key]}")
        return ", ".join(diffs) if diffs else "sin cambios"

    # ================================================================
    #   PUNTO DE ENTRADA: CHECK AND EVOLVE
    # ================================================================
    def check_and_evolve(self, history_cache: List[dict]) -> Optional[dict]:
        """
        Punto de entrada principal. Llamar periódicamente con el historial actualizado.
        
        1. Extrae trades cerrados de SPX.
        2. Si hay suficientes trades nuevos → ejecuta batalla.
        3. Retorna resultado de la batalla o None.
        """
        if not self.config:
            return None
        
        closed_trades = self.get_spx_closed_trades(history_cache)
        
        if len(closed_trades) <= self._last_evaluated_count:
            # No hay trades nuevos suficientes
            return None
        
        window_size = self.config["autolab"]["window_size"]
        new_trades_since_last = len(closed_trades) - self._last_evaluated_count
        
        if new_trades_since_last >= window_size:
            logger.info(
                f"🔬 [SPX_AUTOLAB] {new_trades_since_last} trades nuevos detectados. "
                f"Iniciando batalla evolutiva..."
            )
            result = self.execute_battle(closed_trades)
            if result:
                self._last_evaluated_count = len(closed_trades)
            return result
        else:
            logger.debug(
                f"[SPX_AUTOLAB] {new_trades_since_last}/{window_size} trades nuevos. "
                f"Esperando más evidencia."
            )
            return None

    # ================================================================
    #   UTILIDADES
    # ================================================================
    def get_status(self) -> dict:
        """Retorna el estado actual del AutoLab para el dashboard."""
        if not self.config:
            return {"status": "ERROR", "message": "Config no cargada"}
        
        return {
            "status": "ACTIVE",
            "version": self.config.get("version", 1),
            "champion": self.config["champion"],
            "total_battles": self.config["history"]["total_battles"],
            "total_promotions": self.config["history"]["total_promotions"],
            "last_battle": self.config["history"]["last_battle_date"],
            "window_size": self.config["autolab"]["window_size"],
            "objective": self.config["autolab"]["objective"],
        }


# Singleton global
spx_autolab = SPXContractAutoLab()
