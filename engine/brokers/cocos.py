# -*- coding: utf-8 -*-
"""
engine/brokers/cocos.py

Conector de Cocos / OMS LatinSecurities (BYMA).

Reusa el flow de login del byma_loader y agrega lectura de posiciones
de la API de risk del OMS.

Endpoints típicos del OMS Matriz (Cocos / Latin):
  POST  /j_spring_security_check         (login form)
  GET   /rest/risk/position              (posiciones consolidadas)
  GET   /rest/risk/accountReport/<id>    (variaciones por cuenta)
  GET   /rest/order/all                  (órdenes históricas — para trades)

La estructura del response varía un poco entre versiones del OMS, así
que tratamos los campos como tolerantes (usa fallbacks).

Nota: el "ticker" como lo devuelve el OMS típicamente incluye el plazo:
  AL30D-24hs    →  AL30D
  GD30C-CI      →  GD30C
Normalizamos quitando el sufijo de plazo.
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any, Optional


def _base_url(creds: dict) -> str:
    url = (creds.get("byma_api_url") or
           os.environ.get("BYMA_API_URL") or
           "https://api.cocos.xoms.com.ar/").rstrip("/") + "/"
    return url


def _login(creds: dict):
    """Login OMS. Devuelve session de requests."""
    import requests
    base = _base_url(creds)
    s = requests.Session()
    s.mount("https://", requests.adapters.HTTPAdapter(
        pool_connections=10, pool_maxsize=10, max_retries=2,
    ))
    r = s.post(
        f"{base}j_spring_security_check",
        data={"j_username": creds["byma_user"],
              "j_password": creds["byma_pass"]},
        timeout=15,
    )
    r.raise_for_status()
    return s


_PLAZO_RE = re.compile(r"^(?P<sym>[A-Za-z0-9]+?)(?:-(?:24hs|48hs|72hs|CI|T0|T1|T2))?$")


def _normalize_ticker(symbol: str) -> str:
    if not symbol:
        return ""
    m = _PLAZO_RE.match(symbol.strip())
    if m:
        return m.group("sym")
    return symbol.strip()


def _classify(ticker: str, currency: Optional[str] = None) -> str:
    """Heurística simple. Misma lógica que infer_moneda_nativa pero al revés."""
    t = (ticker or "").upper()
    # Cash codes
    if t in ("ARS", "USD", "USB", "USDT", "USDC"):
        return "CASH"
    # Bonos AR típicos: AL30, GD30, AE38, BPC7, AY24, AL35, GD35, etc.
    if re.match(r"^(AL|GD|AE|BPC|AY|AO|AF|TC|TX|TO|TZ|TY|S\d|T\d|TXMJ)", t):
        return "BOND_AR"
    # Acciones AR mainstream
    if t in ("GGAL", "BMA", "YPFD", "PAMP", "TGS", "BBAR", "ALUA", "TXAR",
             "CEPU", "EDN", "LOMA", "TRAN", "VALO", "MIRG", "COME"):
        return "EQUITY_AR"
    # CEDEAR (acciones US listadas en BYMA)
    if t in ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA",
             "JPM", "KO", "DIS", "BABA", "MELI", "GLOB", "VIST"):
        return "EQUITY_US"
    return "OTHER"


def fetch_positions(creds: dict) -> dict:
    """Trae posiciones del OMS y las devuelve normalizadas."""
    if not creds.get("byma_user") or not creds.get("byma_pass"):
        raise ValueError("Faltan credenciales BYMA (byma_user, byma_pass)")
    base = _base_url(creds)
    session = _login(creds)

    # El endpoint exacto varía según versión del OMS Matriz (Primary/Cocos/
    # Latin/IEB/etc). Probamos varios. Si todos fallan, levantamos un error
    # que incluye el detalle de cada response para diagnosticar.
    candidates = [
        "rest/risk/detailedPosition",     # Primary: positions detalladas
        "rest/risk/position",             # Cocos/Latin clásico
        "rest/risk/accountReport",        # algunos OMS lo usan así
        "rest/order/getPositions",        # legacy
        "rest/risk/positionByCustomer",   # alternativa
    ]
    raw = None
    used_endpoint = None
    diagnostics = []   # lista de (endpoint, status, content_type, body_snippet)
    for ep in candidates:
        try:
            r = session.get(f"{base}{ep}", timeout=15)
            ct = r.headers.get("content-type", "")
            snippet = (r.text or "")[:200].replace("\n", " ")
            diagnostics.append((ep, r.status_code, ct, snippet))
            if r.status_code == 200 and "json" in ct:
                payload = r.json()
                # Si devolvió lista vacía o dict vacío, probamos el próximo
                # (puede ser otro endpoint que sí tiene data)
                if payload or used_endpoint is None:
                    raw = payload
                    used_endpoint = ep
                    break
        except Exception as e:
            diagnostics.append((ep, None, "", f"EXC: {type(e).__name__}: {e}"))

    if raw is None:
        detail = "\n".join(
            f"  - {ep}: status={st} ct='{ct}' body='{body}'"
            for ep, st, ct, body in diagnostics
        )
        raise RuntimeError(
            f"No pude leer posiciones del OMS ({base}).\n\n"
            f"Endpoints probados:\n{detail}\n\n"
            f"💡 Si tu broker usa otra URL (Latin: "
            f"https://api.latinsecurities.matrizoms.com.ar/, IEB, etc.), "
            f"cargala en `byma_api_url` desde Settings → Credenciales. "
            f"Si todos los endpoints dan 401 o redirect, el login no funcionó "
            f"con tu user/pass."
        )

    # Estructura típica: lista de dicts o {"data": [...]} o
    # {"positions": [...]} — normalizamos.
    items = raw
    if isinstance(raw, dict):
        for key in ("positions", "data", "items", "rows", "result"):
            if isinstance(raw.get(key), list):
                items = raw[key]
                break
    if not isinstance(items, list):
        items = []

    positions = []
    warnings = []
    today = date.today().isoformat()
    for it in items:
        if not isinstance(it, dict):
            continue
        sym = (it.get("symbol") or it.get("ticker") or it.get("instrument")
               or it.get("instrumentId") or "")
        sym = _normalize_ticker(str(sym))
        if not sym:
            continue
        # Cantidad: distintos campos según versión
        qty = (it.get("quantity") or it.get("qty") or it.get("totalQty")
               or it.get("size") or it.get("posTotal") or 0)
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            qty = 0.0
        if abs(qty) < 1e-9:
            continue
        avg = it.get("avgPrice") or it.get("averagePrice") or it.get("price")
        try:
            avg = float(avg) if avg is not None else None
        except (TypeError, ValueError):
            avg = None

        ccy = (it.get("currency") or it.get("ccy") or "").upper() or None
        # Si el ticker termina en D → USB; en C → USD; resto → ARS
        if not ccy:
            if sym.endswith("D"):   ccy = "USB"
            elif sym.endswith("C"): ccy = "USD"
            else:                   ccy = "ARS"

        is_cash = sym.upper() in ("ARS", "USD", "USB")
        positions.append({
            "ticker": sym,
            "raw_ticker": str(it.get("symbol") or sym),
            "qty": qty,
            "avg_price": avg,
            "currency": ccy,
            "asset_class": "CASH" if is_cash else _classify(sym, ccy),
            "name": it.get("description") or it.get("name") or sym,
            "is_cash": is_cash,
        })

    return {
        "broker": "cocos",
        "endpoint": used_endpoint,
        "as_of": today,
        "positions": positions,
        "warnings": warnings,
    }
