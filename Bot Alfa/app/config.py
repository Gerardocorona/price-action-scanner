from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ib_host: str = "127.0.0.1"
    ib_port: int = 7497
    ib_client_id: int = 101
    ib_account: str | None = None
    broker_is_paper: bool = True
    ib_reconnect_interval: int = 10 # seconds between connection attempts
    app_host: str = "0.0.0.0"
    app_port: int = 8001
    webhook_token: str | None = None
    price_timeout: float = 15.0  # seconds to wait for market data
    order_ack_timeout: float = 5.0  # seconds to wait for IB to confirm order
    fill_timeout: float = 60.0  # max seconds to wait for parent order fill
    
    # Trading Parameters (Unified - Nuevos Valores)
    sl_percent: float = 0.20  # 20% Stop Loss
    tp_percent: float = 0.10  # 10% Take Profit (Gatillo para el Trailing si está activo)
    use_conditional_trailing: bool = True
    trailing_percent: float = 0.05  # 5% de Trail
    
    # Capital Management
    capital_fraction: float = 0.50  # 50% del balance total para operar en el día
    per_trade_fraction: float = 1.00  # 100% del capital operativo por trade (Una sola bala)
    daily_tp_target: float = 0.15  # Meta diaria de TP (15%)
    max_daily_loss_pct: float = 0.30  # Pérdida máxima diaria permitida (30%)
    
    # Virtual Account (Simulación de Saldo)
    use_virtual_balance: bool = True
    virtual_balance: float = 5000.0
    
    # DCA / Recovery Strategy
    dca_enabled: bool = False
    dca_trigger_pct: float = -0.50  # Disparar DCA cuando PnL <= -50%
    dca_bounce_pct: float = 0.05    # Rebote requerido (5%) desde el mínimo para confirmar DCA
    dca_max_attempts: int = 3  # Máximo 3 DCAs por posición antes de cerrar
    dca_tp_percent: float = 0.10  # TP reducido para salir rápido en recuperación (10%)
    
    # Tickers activos para escanear (separados por coma)
    active_tickers: str = "SPY,QQQ,GOOG,AMZN,TSLA,NVDA,MSFT,AMD,IWM,DIA"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
