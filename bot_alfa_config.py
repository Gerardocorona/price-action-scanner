# Configuration for Interactive Brokers (IBKR)
# Ensure TWS or IB Gateway is running with API enabled.

IB_HOST = "127.0.0.1"
IB_PORT = 7497        # 7497 for Paper, 7496 for Live, 4002 for Gateway
IB_CLIENT_ID = 123    # Unique ID to avoid conflicts

# Trading Defaults
DEFAULT_QUANTITY = 10
SL_PERCENT = 20.0      # Initial Stop Loss Percentage (-20%)
TT_PERCENT = 10.0      # Take Profit (Trigger) Percentage (+10%)
TRAIL_PERCENT = 5.0    # Trailing Stop Percentage (5% after trigger)

# SPX Scanner Constraints
MIN_PRICE = 3.80
MAX_PRICE = 5.50
MAX_SPREAD = 0.10      # Max $10 difference ($0.10 in price)

# Mobile Dashboard Security
WEB_USER = "admin"
WEB_PASS = "Gerardo090928#*"

# TradingView Webhook
WEBHOOK_SECRET = "pa-scanner-2026"   # Token secreto para validar webhooks de TradingView
RISK_PERCENT = 0.20                   # 20% del balance por trade
