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


def _public_get(path: str, timeout: int = 15):
    """GET sin firma (endpoints públicos: tickers, etc)."""
    import requests
    r = requests.get(f"{BASE_URL}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def _fetch_prices_usdt(symbols: list[str]) -> dict[str, float]:
    """Devuelve {SYM: price_in_USDT} para los symbols crypto que tienen
    par <SYM>USDT en Binance. Hace 1 sola llamada al ticker bulk.

    Si el endpoint falla, devuelve {}; el caller usa avg_price=None.
    """
    if not symbols:
        return {}
    try:
        # Endpoint bulk: devuelve TODOS los tickers (~1000 entradas).
        # Filtramos client-side. Más eficiente que N llamadas individuales.
        all_tickers = _public_get("/api/v3/ticker/price")
    except Exception:
        return {}
    by_sym = {}
    target_pairs = {f"{s}USDT": s for s in symbols}
    for t in all_tickers:
        pair = t.get("symbol", "")
        if pair in target_pairs:
            try:
                by_sym[target_pairs[pair]] = float(t["price"])
            except (KeyError, ValueError, TypeError):
                pass
    return by_sym


def fetch_positions(creds: dict) -> dict:
    """Trae balances del wallet spot. Filtra los con qty>0.

    También baja precios actuales en USDT para cada crypto via
    /api/v3/ticker/price (público, sin auth). Eso permite que el
    motor valore las tenencias inmediatamente, sin necesidad de
    correr el cripto_loader aparte.

    Modelo de assets:
      - FIATS (ARS, USD, EUR, BRL, ...): is_cash=True. Saldos cash en
        moneda fiat. avg_price=1.0.
      - STABLECOINS (USDT, USDC, BUSD, ...): is_cash=False, asset_class
        STABLECOIN. Tratados como ASSETS (no como cash) — esto matchea
        cómo AFIP los considera (cripto, no efectivo). avg_price=1.0.
      - CRYPTO (BTC, ETH, SOL, ...): is_cash=False, asset_class CRYPTO.
        avg_price = precio actual en USDT desde Binance ticker.
    """
    api_key = (creds.get("binance_api_key") or "").strip()
    api_secret = (creds.get("binance_api_secret") or "").strip()
    if not api_key or not api_secret:
        raise ValueError("Faltan credenciales Binance "
                          "(binance_api_key, binance_api_secret)")

    data = _signed_get(api_key, api_secret, "/api/v3/account")
    balances = data.get("balances", [])
    today = date.today().isoformat()

    # Primer pass: filtrar y normalizar sin precios todavía.
    raw_positions = []
    warnings = []
    crypto_syms = []  # symbols que necesitan precio fetcheado
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
        if sym.startswith("LD"):
            warnings.append(f"Skipping LD-prefix savings: {sym}")
            continue

        is_stable = sym in _STABLES
        is_fiat = sym in _FIATS

        if is_fiat:
            cls = "CASH"
            is_cash = True
            natural_ccy = sym
        elif is_stable:
            cls = "STABLECOIN"
            # IMPORTANTE: ahora tratamos stablecoins como ASSET, no cash.
            # Esto es más correcto contablemente (cripto-cripto trades
            # vs ARS son hechos imponibles en AR) y simétrico con BTC/ETH.
            is_cash = False
            natural_ccy = "USD"
        else:
            cls = "CRYPTO"
            is_cash = False
            natural_ccy = "USD"
            crypto_syms.append(sym)

        raw_positions.append({
            "sym": sym, "qty": total, "is_stable": is_stable,
            "is_fiat": is_fiat, "cls": cls, "is_cash": is_cash,
            "ccy": natural_ccy, "free": free, "locked": locked,
        })

    # Segundo pass: bajar precios de los crypto symbols.
    prices = _fetch_prices_usdt(crypto_syms) if crypto_syms else {}
    if crypto_syms and not prices:
        warnings.append(
            "No pude bajar precios actuales de Binance (rate limit o red). "
            "Las posiciones se importan con avg_price=None y aparecen en "
            "0 hasta que cargues precios manualmente."
        )

    # Tercer pass: armar la lista final con precios.
    positions = []
    for r in raw_positions:
        if r["is_stable"]:
            avg = 1.0  # peg asumido. El motor recalcula si después tiene precio real.
        elif r["is_fiat"]:
            avg = 1.0  # 1 ARS = 1 ARS, etc
        else:
            avg = prices.get(r["sym"])  # puede ser None si no hay par USDT

        positions.append({
            "ticker": r["sym"],
            "raw_ticker": r["sym"],
            "qty": r["qty"],
            "avg_price": avg,
            "currency": r["ccy"],
            "asset_class": r["cls"],
            "name": r["sym"],
            "is_cash": r["is_cash"],
            "free": r["free"],
            "locked": r["locked"],
        })

    # Orden: cash primero, después stables, después crypto por qty*price
    def _sort_key(p):
        # priority: 0=fiat-cash, 1=stable, 2=crypto
        if p["is_cash"]:
            prio = 0
        elif p["asset_class"] == "STABLECOIN":
            prio = 1
        else:
            prio = 2
        notional = p["qty"] * (p["avg_price"] or 0)
        return (prio, -notional)
    positions.sort(key=_sort_key)

    return {
        "broker": "binance",
        "as_of": today,
        "positions": positions,
        "warnings": warnings,
        "prices_fetched": len(prices),
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
