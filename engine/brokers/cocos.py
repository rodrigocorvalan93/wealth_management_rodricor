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


def _extract_list(raw: Any, list_keys: tuple) -> list:
    """Extrae una lista de positions de un response JSON que puede tener
    distintas shapes. Prueba:
      1. raw es directamente una lista
      2. raw[key] es una lista para alguna key en list_keys
      3. raw[key1][key2] es una lista (1 nivel de nesting)
    Devuelve [] si no encuentra ninguna.
    """
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    # Búsqueda en el primer nivel
    for k in list_keys:
        v = raw.get(k)
        if isinstance(v, list):
            return v
    # Búsqueda en el segundo nivel (raw[k1][k2])
    for k1, v1 in raw.items():
        if isinstance(v1, dict):
            for k2 in list_keys:
                v2 = v1.get(k2)
                if isinstance(v2, list):
                    return v2
    return []


def _describe_shape(raw: Any, max_depth: int = 2) -> str:
    """Devuelve string corto describiendo la shape de raw — útil para warnings.
    Ej: '{positions: list[5], total: int}' o '[5 dicts]' o '{data: {...}}'.
    """
    if isinstance(raw, list):
        if not raw:
            return "[]"
        first_type = type(raw[0]).__name__
        return f"[{len(raw)} x {first_type}]"
    if isinstance(raw, dict):
        if max_depth <= 0:
            return "{...}"
        parts = []
        for k, v in list(raw.items())[:8]:  # primeros 8 keys
            if isinstance(v, list):
                parts.append(f"{k}: list[{len(v)}]")
            elif isinstance(v, dict):
                parts.append(f"{k}: {_describe_shape(v, max_depth-1)}")
            else:
                parts.append(f"{k}: {type(v).__name__}")
        suffix = ", ..." if len(raw) > 8 else ""
        return "{" + ", ".join(parts) + suffix + "}"
    return type(raw).__name__


def fetch_positions(creds: dict) -> dict:
    """Trae posiciones del OMS Matriz (Cocos / Latin / Primary) y las
    devuelve normalizadas.

    El OMS requiere el accountName en el path (per Primary API docs):
        GET rest/risk/position/getPositions/{accountName}
        GET rest/risk/detailedPosition/{accountName}
        GET rest/risk/accountReport/{accountName}

    Si `byma_account` no está seteado, intentamos los endpoints legacy
    sin path param (algunos brokers viejos los exponían), pero lo más
    probable es que respondan con error.
    """
    if not creds.get("byma_user") or not creds.get("byma_pass"):
        raise ValueError("Faltan credenciales BYMA (byma_user, byma_pass)")
    base = _base_url(creds)
    account = (creds.get("byma_account") or "").strip()
    session = _login(creds)

    # Endpoints en orden de preferencia. Si tenemos account name, usamos
    # el formato documentado en Primary API. Si no, probamos legacy sin
    # path param (suelen fallar pero por compat).
    if account:
        candidates = [
            f"rest/risk/position/getPositions/{account}",  # Primary documented
            f"rest/risk/detailedPosition/{account}",
            f"rest/risk/accountReport/{account}",
        ]
    else:
        candidates = [
            "rest/risk/position",
            "rest/risk/detailedPosition",
            "rest/risk/accountReport",
            "rest/order/getPositions",
        ]

    LIST_KEYS = ("positions", "data", "items", "rows", "result",
                  "content", "records", "results", "detailedPositions")

    items = []
    raw = None
    used_endpoint = None
    diagnostics = []         # (endpoint, status, content_type, body_snippet)
    status_messages = []     # responses con {status: ERROR, message: ...}

    for ep in candidates:
        try:
            r = session.get(f"{base}{ep}", timeout=15)
            ct = r.headers.get("content-type", "")
            snippet = (r.text or "")[:200].replace("\n", " ")
            diagnostics.append((ep, r.status_code, ct, snippet))
            if r.status_code != 200 or "json" not in ct:
                continue
            payload = r.json()
            extracted = _extract_list(payload, LIST_KEYS)
            if extracted:
                items = extracted
                raw = payload
                used_endpoint = ep
                break
            # 200 + JSON pero sin lista — probablemente error del OMS.
            # Capturamos para mostrar al user después.
            if isinstance(payload, dict):
                st = payload.get("status") or payload.get("statusCode")
                msg = payload.get("message") or payload.get("description")
                if st or msg:
                    status_messages.append((ep, st, msg))
            if raw is None:  # primer payload "razonable" — fallback diagnostic
                raw = payload
                used_endpoint = ep
        except Exception as e:
            diagnostics.append((ep, None, "", f"EXC: {type(e).__name__}: {e}"))

    if not items:
        # No conseguimos una lista de positions. Construir mensaje útil.
        if not account:
            # El user no cargó byma_account → el más probable es ese.
            raise RuntimeError(
                "No pude leer posiciones del OMS. Los endpoints de "
                "Primary/Cocos/Latin requieren el nombre de cuenta en el "
                "path (ej: /rest/risk/position/getPositions/REM7374).\n\n"
                "💡 Cargá tu account name en Settings → Credenciales → "
                "'BYMA account name'. Es el número/código de cuenta que ves "
                "en la interfaz de tu broker (Cocos Capital → tu nombre → "
                "Cuentas)."
            )
        if status_messages:
            msgs_str = "\n".join(
                f"  - {ep}: status={st!r} message={msg!r}"
                for ep, st, msg in status_messages
            )
            raise RuntimeError(
                f"OMS respondió pero ningún endpoint devolvió posiciones "
                f"para la cuenta '{account}'. Mensajes del OMS:\n\n{msgs_str}"
                f"\n\n💡 Verificá que el account name esté escrito tal cual "
                f"figura en el portal del broker (case-sensitive). Si el "
                f"OMS dice 'access denied', pedí al admin de tu cuenta que "
                f"te habilite el permiso de 'Consultas/Posiciones'."
            )
        detail = "\n".join(
            f"  - {ep}: status={st} ct='{ct}' body='{body}'"
            for ep, st, ct, body in diagnostics
        )
        raise RuntimeError(
            f"No pude leer posiciones del OMS ({base}) — ningún endpoint "
            f"respondió útil para account='{account}'.\n\n"
            f"Endpoints probados:\n{detail}"
        )

    positions = []
    warnings = []
    today = date.today().isoformat()
    for it in items:
        if not isinstance(it, dict):
            continue
        # Primary devuelve symbol como "MERV - XMEV - AAPL - CI" y
        # el ticker limpio en instrument.symbolReference. Probamos
        # symbolReference primero, después symbol con segmentación.
        inst = it.get("instrument") or {}
        if isinstance(inst, dict):
            sym = inst.get("symbolReference") or inst.get("symbol") or ""
        else:
            sym = ""
        if not sym:
            sym = (it.get("symbol") or it.get("ticker")
                    or it.get("instrumentId") or "")
        sym = str(sym)
        # Para symbols tipo "MERV - XMEV - AAPL - CI" → tomar el tercer
        # segmento que es el ticker real
        if " - " in sym:
            parts = [p.strip() for p in sym.split(" - ")]
            if len(parts) >= 3:
                sym = parts[2]
        sym = _normalize_ticker(sym)
        if not sym:
            continue
        # Cantidad: Primary usa buySize/sellSize por separado → netear.
        # Otros OMS usan quantity/qty/totalQty/posTotal directos.
        qty = (it.get("quantity") or it.get("qty") or it.get("totalQty")
               or it.get("size") or it.get("posTotal"))
        if qty is None:
            # Primary shape: net = buySize - sellSize
            try:
                buy = float(it.get("buySize") or 0)
                sell = float(it.get("sellSize") or 0)
                qty = buy - sell
            except (TypeError, ValueError):
                qty = 0.0
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            qty = 0.0
        if abs(qty) < 1e-9:
            continue
        # Avg price: Primary usa buyPrice; legacy usa avgPrice/averagePrice/price
        avg = (it.get("avgPrice") or it.get("averagePrice")
               or it.get("buyPrice") or it.get("price"))
        try:
            avg = float(avg) if avg is not None else None
        except (TypeError, ValueError):
            avg = None
        # buyPrice de Primary puede venir en 0 si toda la posición es venta
        if avg == 0:
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
