import os
import logging

logger = logging.getLogger("ibg.id_manager")

class OrderIdManager:
    """
    Gestiona la persistencia del Order ID para evitar colisiones entre reinicios.
    """
    def __init__(self, filename: str = "config/last_order_id.txt"):
        self.filename = filename
        self._last_id = self._load()

    def _load(self) -> int:
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    return int(f.read().strip())
            except:
                return 0
        return 0

    def _save(self, order_id: int):
        try:
            os.makedirs(os.path.dirname(self.filename), exist_ok=True)
            with open(self.filename, "w") as f:
                f.write(str(order_id))
        except Exception as e:
            logger.error(f"Error guardando Order ID: {e}")

    def get_next_id(self, ib_req_id: int) -> int:
        """
        Retorna el ID más alto entre el persistido y el sugerido por IBKR, con buffer de seguridad.
        Usa un buffer +100 para evitar colisiones con órdenes previas.
        """
        # Usar el máximo y sumar buffer para garantizar IDs frescos y sin colisiones
        next_id = max(self._last_id, ib_req_id + 100) + 1
        self._last_id = next_id
        self._save(next_id)
        logger.info(f"Generated next Order ID: {next_id} (from persistent={self._last_id}, ib_req={ib_req_id})")
        return next_id

# Instancia global
id_manager = OrderIdManager()
