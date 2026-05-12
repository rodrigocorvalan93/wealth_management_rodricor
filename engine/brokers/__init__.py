# -*- coding: utf-8 -*-
"""
engine/brokers/__init__.py

Conectores de auto-import de tenencias / trades desde brokers externos.

Cada submódulo expone:

  fetch_positions(creds: dict) -> dict
    {
      "broker": str,
      "as_of": ISO date,
      "positions": [
         {
           "ticker": str,           # ticker normalizado (matching `assets.ticker`)
           "raw_ticker": str,       # como vino del broker (para debug)
           "qty": float,
           "avg_price": float|None,
           "currency": str,
           "asset_class": str,      # heurística → BOND_AR, EQUITY_US, CRYPTO, ...
           "name": str|None,
           "is_cash": bool,
         }, ...
      ],
      "warnings": [str, ...],       # FCI/ticker no reconocido, etc
    }

  fetch_trades(creds: dict, since: date|None) -> dict   (opcional)
    Igual estructura pero con trades.

Cada conector NO toca la DB ni el Excel. La capa de aplicación
(api/app.py) decide qué hacer con la preview (mostrársela al user,
escribir _carga_inicial, etc).
"""

from . import cocos, binance, ibkr_flex


REGISTRY = {
    "cocos": cocos,
    "binance": binance,
    "ibkr": ibkr_flex,
}


def get(name: str):
    return REGISTRY.get(name)


def list_brokers() -> list[dict]:
    """Metadata para que la UI pueda renderizar tiles."""
    return [
        {"id": "cocos",   "name": "Cocos / OMS BYMA",  "icon": "🏦",
         "needs": ["byma_user", "byma_pass", "byma_account"],
         "supports": ["positions"],
         "help": "Usa el OMS configurado en credenciales (Cocos / LatinSecurities). Requiere también el 'byma_account' (nombre/número de cuenta del OMS — el endpoint /getPositions/{account} lo necesita)."},
        {"id": "binance", "name": "Binance",            "icon": "🟡",
         "needs": ["binance_api_key", "binance_api_secret"],
         "supports": ["positions"],
         "help": "Necesita una API key con SOLO permiso 'Enable Reading' (sin trading ni withdrawals). Importa los balances del wallet spot."},
        {"id": "ibkr",    "name": "Interactive Brokers", "icon": "🔵",
         "needs": ["ibkr_flex_token", "ibkr_flex_query_id"],
         "supports": ["positions", "trades"],
         "help": "Configurá una Flex Query con sección 'Open Positions' (y opcionalmente 'Trades') en IBKR Account Management. El token es read-only."},
    ]
