import json
import os
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger("ibg.history")

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.json")
CUTOFF_FILE = os.path.join(DATA_DIR, "history_cutoff.json")


class HistoryManager:
    def __init__(self):
        self._ensure_data_dir()
        # Cargar el cutoff_time PRIMERO (antes del historial)
        self._cutoff_time: str = self._load_cutoff()
        # Cargar historial filtrando por cutoff
        self._cache: List[Dict[str, Any]] = self._load_history()

    def _ensure_data_dir(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

    def _load_history(self) -> List[Dict[str, Any]]:
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Filtrar por cutoff_time si existe
            if self._cutoff_time:
                filtered = [e for e in raw if self._is_after_cutoff(e.get('time', ''))]
                if len(filtered) < len(raw):
                    logger.info(f"Historial cargado: {len(raw)} entradas, {len(raw)-len(filtered)} filtradas por cutoff ({len(filtered)} aceptadas)")
                return filtered
            return raw
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def _save_history(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def _load_cutoff(self) -> str:
        """Carga el timestamp de corte (ejecuciones anteriores a este se ignoran)."""
        if not os.path.exists(CUTOFF_FILE):
            return ""
        try:
            with open(CUTOFF_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("cutoff_time", "")
        except Exception:
            return ""

    def _save_cutoff(self, cutoff_time: str):
        """Guarda el timestamp de corte."""
        try:
            with open(CUTOFF_FILE, "w", encoding="utf-8") as f:
                json.dump({"cutoff_time": cutoff_time}, f)
        except Exception as e:
            logger.error(f"Error saving cutoff: {e}")

    def _is_after_cutoff(self, exec_time_str: str) -> bool:
        """Retorna True si la ejecución ocurrió DESPUÉS del cutoff_time."""
        if not self._cutoff_time:
            return True  # Sin cutoff, aceptar todo
        try:
            t_str = str(exec_time_str).strip()

            # Parsear el tiempo de la ejecución
            exec_dt = None
            # Formato: "2026-03-30 14:28:17+00:00" o "2026-03-30T14:28:17+00:00"
            for sep in ['T', ' ']:
                try:
                    normalized = t_str.replace(' ', 'T', 1) if sep == ' ' else t_str
                    exec_dt = datetime.fromisoformat(normalized)
                    break
                except ValueError:
                    pass
            # Formato IBKR: "20260330  14:28:17"
            if exec_dt is None:
                try:
                    clean = t_str.replace('  ', ' ').strip()
                    exec_dt = datetime.strptime(clean, "%Y%m%d %H:%M:%S")
                except ValueError:
                    pass

            if exec_dt is None:
                logger.warning(f"No se pudo parsear tiempo: '{t_str}'")
                return True  # No filtrar si no se puede parsear

            if exec_dt.tzinfo is None:
                exec_dt = exec_dt.replace(tzinfo=timezone.utc)

            # Parsear cutoff
            cutoff_dt = datetime.fromisoformat(self._cutoff_time)
            if cutoff_dt.tzinfo is None:
                cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)

            return exec_dt > cutoff_dt
        except Exception as e:
            logger.warning(f"Error comparando tiempo de ejecución: {e}")
            return True  # En caso de error, no filtrar

    def clear_history(self):
        """
        Limpia el historial y establece un cutoff_time en el momento actual.
        Las ejecuciones de IBKR anteriores a este momento serán ignoradas.
        """
        self._cache = []
        self._save_history()
        # Establecer cutoff en el momento actual (UTC)
        cutoff = datetime.now(timezone.utc).isoformat()
        self._cutoff_time = cutoff
        self._save_cutoff(cutoff)
        logger.info(f"✅ Historial limpiado. Cutoff establecido: {cutoff}")

    def add_executions(self, executions: List[Any]):
        """
        Agrega nuevas ejecuciones al historial, evitando duplicados.
        Filtra ejecuciones anteriores al cutoff_time.
        Acepta objetos Execution de ib_insync o dicts.
        """
        added = False
        existing_ids = {item.get("execId") for item in self._cache if item.get("execId")}

        for ex in executions:
            # Normalizar a dict
            if hasattr(ex, "execution"):  # Es un Fill/Execution de ib_insync
                d = {
                    "execId": ex.execution.execId,
                    "time": ex.execution.time.strftime("%Y%m%d  %H:%M:%S") if isinstance(ex.execution.time, datetime) else str(ex.execution.time),
                    "symbol": ex.contract.symbol,
                    "localSymbol": ex.contract.localSymbol,
                    "side": ex.execution.side,
                    "shares": float(ex.execution.shares),
                    "price": float(ex.execution.price),
                    "permId": ex.execution.permId,
                    "orderId": ex.execution.orderId,
                    "account": ex.execution.acctNumber,
                    "realizedPNL": float(ex.commissionReport.realizedPNL) if ex.commissionReport else 0.0,
                    "contract": {
                        "conId": ex.contract.conId,
                        "symbol": ex.contract.symbol,
                        "localSymbol": ex.contract.localSymbol,
                        "secType": ex.contract.secType,
                        "currency": ex.contract.currency,
                        "exchange": ex.contract.exchange,
                        "strike": getattr(ex.contract, 'strike', 0.0),
                        "right": getattr(ex.contract, 'right', ''),
                        "expiry": getattr(ex.contract, 'lastTradeDateOrContractMonth', '')
                    }
                }
            elif isinstance(ex, dict):
                d = ex
                if "realizedPNL" not in d:
                    d["realizedPNL"] = 0.0
            else:
                continue

            # Filtrar por cutoff_time
            exec_time = d.get("time", "")
            if not self._is_after_cutoff(exec_time):
                logger.debug(f"Ignorando ejecución anterior al cutoff: {d.get('symbol')} @ {exec_time}")
                continue

            if d.get("execId") not in existing_ids:
                self._cache.append(d)
                existing_ids.add(d.get("execId"))
                added = True

        if added:
            # Ordenar por fecha descendente
            self._cache.sort(key=lambda x: x.get("time", ""), reverse=True)
            self._save_history()
            logger.info(f"✅ Nuevas ejecuciones guardadas en historial.")

    def get_all_movements(self) -> List[Dict[str, Any]]:
        return self._cache

    def get_cumulative_cash_flow(self) -> float:
        """
        Calcula el flujo de caja neto acumulado de todas las operaciones.
        Usa secType para determinar el multiplicador (OPT=100, STK=1).
        """
        cash_flow = 0.0
        for trade in self._cache:
            try:
                price = float(trade.get("price", 0.0))
                shares = float(trade.get("shares", 0.0))
                side = trade.get("side", "").upper()

                # Determinar multiplicador por secType
                sec_type = trade.get("contract", {}).get("secType", "")
                has_option_fields = (
                    trade.get("contract", {}).get("strike", 0) and
                    trade.get("contract", {}).get("right", "") not in ("", "?")
                )
                multiplier = 100.0 if (sec_type == "OPT" or has_option_fields) else 1.0

                amount = price * shares * multiplier

                # Filtro de seguridad: ignorar transacciones > $20,000
                if amount > 20000:
                    logger.warning(f"Ignorando trade masivo: {trade.get('symbol')} ${amount:,.2f}")
                    continue

                if side in ["BOT", "BUY"]:
                    cash_flow -= amount
                elif side in ["SLD", "SELL"]:
                    cash_flow += amount
            except Exception as e:
                logger.error(f"Error calculating cash flow: {e}")
                continue

        return cash_flow


# Instancia global
history_manager = HistoryManager()
