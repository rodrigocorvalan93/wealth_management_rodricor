# -*- coding: utf-8 -*-
"""
engine/brokers/binance.py

Conector read-only de Binance (spot wallet).

Auth: HMAC-SHA256 con `binance_api_secret`. La key tiene que tener
permiso 'Enable Reading' SOLAMENTE — sin trading ni withdrawals
(el conector NUNCA hace POST/DELETE).

Endpoints usados:
  GET /api/v3/account              → balances spot
  GET /sapi/v1/staking/position    → staking (opcional, si existe)
  GET /sapi/v1/asset/wallet/balance → snapshot consolidado wallets
                                       (opcional, requiere permiso extra)

Para mantener simple, la versión inicial solo lee /api/v3/account
(spot wallet). Stablecoins (USDT, USDC, BUSD, DAI) se taggean como
STABLECOIN; el resto como CRYPTO.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from datetime import date


BASE_URL = "https://api.binance.com"


# Lista corta de stablecoins comunes (en mayúsculas, base symbol)
_STABLES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD"}

# Fiats que Binance soporta (cuando comprás cripto con tarjeta o haces P2P)
_FIATS = {"ARS", "USD", "EUR", "BRL", "GBP", "RUB", "TRY", "MXN", "COP",
          "CLP", "PEN", "UYU", "BOB", "PYG"}


def _sign(secret: str, query: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _signed_get(api_key: str, api_secret: str, path: str, params: dict = None,
                 timeout: int = 15):
    import requests
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    qs = urllib.parse.urlencode(params, doseq=True)
    sig = _sign(api_secret, qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    r = requests.get(
        url,
        headers={"X-MBX-APIKEY": api_key},
        timeout=timeout,
    )
    if r.status_code == 401:
        raise PermissionError(
            f"Binance rechazó las credenciales (401). "
            f"Verificá api_key/secret y que la key tenga 'Enable Reading'."
        )
    if r.status_code == 418 or r.status_code == 429:
        raise RuntimeError(f"Binance rate limit ({r.status_code})")
    r.raise_for_status()
    return r.json()


def fetch_positions(creds: dict) -> dict:
    """Trae balances del wallet spot. Filtra los con qty>0."""
    api_key = (creds.get("binance_api_key") or "").strip()
    api_secret = (creds.get("binance_api_secret") or "").strip()
    if not api_key or not api_secret:
        raise ValueError("Faltan credenciales Binance "
                          "(binance_api_key, binance_api_secret)")

    data = _signed_get(api_key, api_secret, "/api/v3/account")
    balances = data.get("balances", [])
    positions = []
    warnings = []
    today = date.today().isoformat()

    for b in balances:
        try:
            free = float(b.get("free", 0) or 0)
            locked = float(b.get("locked", 0) or 0)
        except (TypeError, ValueError):
            continue
        total = free + locked
        if total < 1e-9:
            continue
        sym = (b.get("asset") or "").upper()
        if not sym:
            continue
        # Las "LD-" son posiciones lockeadas en savings (Binance Earn).
        # En general aparecen aparte; no las dupliques.
        if sym.startswith("LD"):
            warnings.append(f"Skipping LD-prefix savings: {sym}")
            continue

        is_stable = sym in _STABLES
        is_fiat = sym in _FIATS
        # is_cash sugerido: stablecoins + fiats. El endpoint apply lo
        # puede sobrescribir según lo que el user tenga en sus monedas.
        is_cash = is_stable or is_fiat
        # asset_class:
        #   FIAT      -> CASH (saldo en moneda fiat, ej ARS, USD, BRL)
        #   STABLE    -> STABLECOIN (USDT, USDC, ...)
        #   resto     -> CRYPTO (BTC, ETH, SOL, ...)
        if is_fiat:
            cls = "CASH"
        elif is_stable:
            cls = "STABLECOIN"
        else:
            cls = "CRYPTO"
        # Currency natural del asset:
        #   - stables/fiats: ellos mismos (son la currency)
        #   - crypto: USD (binance cotiza en USD aunque podés tradear contra USDT)
        natural_ccy = sym if (is_stable or is_fiat) else "USD"
        positions.append({
            "ticker": sym,
            "raw_ticker": sym,
            "qty": total,
            "avg_price": None,  # Binance no expone avg cost por endpoint público
            "currency": natural_ccy,
            "asset_class": cls,
            "name": sym,
            "is_cash": is_cash,
            "free": free,
            "locked": locked,
        })

    # Orden: stablecoins primero, después por qty desc
    positions.sort(key=lambda p: (not p["is_cash"], -p["qty"]))

    return {
        "broker": "binance",
        "as_of": today,
        "positions": positions,
        "warnings": warnings,
    }


def test_credentials(creds: dict) -> dict:
    """Ping al endpoint /api/v3/account para validar las creds."""
    api_key = (creds.get("binance_api_key") or "").strip()
    api_secret = (creds.get("binance_api_secret") or "").strip()
    if not api_key or not api_secret:
        return {"ok": False, "error": "Faltan binance_api_key/secret"}
    try:
        data = _signed_get(api_key, api_secret, "/api/v3/account")
        return {
            "ok": True,
            "can_trade": bool(data.get("canTrade")),
            "can_deposit": bool(data.get("canDeposit")),
            "can_withdraw": bool(data.get("canWithdraw")),
            "account_type": data.get("accountType"),
            "permissions": data.get("permissions", []),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
