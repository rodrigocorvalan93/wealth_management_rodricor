# -*- coding: utf-8 -*-
"""
engine/brokers/ibkr_flex.py

Conector read-only de Interactive Brokers via Flex Web Service.

A diferencia de TWS/CP API que necesitan un gateway corriendo, Flex
es un endpoint HTTP que devuelve un XML report pre-configurado por el
user en su Flex Query.

Setup en IBKR Account Management:
  1. Reports → Flex Queries → Custom Flex Query → New
  2. Sections: 'Open Positions' (mandatory) + 'Trades' (opcional)
  3. Format: XML, Period: 'Last Business Day' (o Custom)
  4. Save → te da un Query ID numérico (8-9 digits).
  5. Settings → User Settings → Flex Web Service → Generate Token
     (válido por 1 año).

Endpoints:
  POST  /Universal/servlet/FlexStatementService.SendRequest?t=TOKEN&q=QUERY_ID&v=3
        → devuelve <FlexStatementResponse><Status>...</Status>
                    <ReferenceCode>...</ReferenceCode>
                    <Url>...</Url></FlexStatementResponse>
  GET   <Url>?t=TOKEN&q=REFERENCE_CODE&v=3
        → el XML del reporte (cuando esté ready, sino re-intentar)
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Optional


SEND_URL = ("https://gdcdyn.interactivebrokers.com/"
            "Universal/servlet/FlexStatementService.SendRequest")
DEFAULT_GET_URL = ("https://gdcdyn.interactivebrokers.com/"
                    "Universal/servlet/FlexStatementService.GetStatement")


# Error codes documentados de IBKR Flex. Mapping → mensaje útil.
# Source: IBKR Flex Web Service API Reference.
_IBKR_ERROR_HINTS = {
    "1001": ("IBKR no puede generar el reporte ahora mismo (transitorio). "
              "Pasa típicamente porque acabás de pedir el mismo reporte "
              "(IBKR tiene un cooldown de ~30s-3min entre generaciones). "
              "Si descargaste el XML manualmente en el browser hace poco, "
              "esperá 5-10 min y reintentá desde la app."),
    "1003": ("Statement is not available. Verifica que tu Flex Query "
              "esté guardada y configurada con el período correcto."),
    "1004": "No records found for the specified period.",
    "1005": "Service unavailable — re-intentá en unos minutos.",
    "1006": ("Too many requests. IBKR limita los reportes por minuto; "
              "esperá 1-2 minutos."),
    "1007": "Statement could not be generated.",
    "1008": "Statement still being processed (esperar y reintentar).",
    "1009": "Statement could not be retrieved.",
    "1010": ("Bad token: el token de Flex Web Service es inválido o "
              "expiró (los tokens caducan al año). Generá uno nuevo en "
              "IBKR → Settings → User Settings → Flex Web Service → "
              "Token Configuration."),
    "1011": ("Account is in 'simulated' mode — no se generan reports."),
    "1012": ("Token expired. Generá uno nuevo en IBKR → Settings → "
              "Flex Web Service."),
    "1013": ("Invalid query ID. Verificá que el Query ID matchee con "
              "una Flex Query existente en tu cuenta IBKR (Reports → "
              "Flex Queries). El ID es un número de 6-9 dígitos."),
    "1014": "User has too many requests in queue.",
    "1015": "Statement is too large.",
    "1016": "Account is restricted.",
    "1017": "Statement contains no data.",
    "1018": "API not enabled for this user.",
    "1019": "Statement is being prepared (esperar).",
    "1020": "Invalid IP address — verificá la IP whitelist en IBKR.",
    "1021": "Reporting feature is not enabled for this account.",
}


def _parse_ibkr_error(text: str) -> tuple[str | None, str | None]:
    """Devuelve (error_code, error_message) si el XML es un error response."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return (None, None)
    code = (root.findtext("ErrorCode") or "").strip()
    msg = (root.findtext("ErrorMessage") or "").strip()
    return (code or None, msg or None)


# Códigos transitorios de IBKR — la app reintenta automáticamente con backoff.
# Para cada uno, lista de delays (segundos) entre reintentos. Total time =
# sum(delays) + tiempo de las requests (~3-5s). Mantenelo bajo el timeout del
# background job (~3 min) para que no quede colgado.
_TRANSIENT_BACKOFFS = {
    "1001": [20, 60, 120],   # cooldown post-generación (≤ 3.4 min total)
    "1005": [10, 30, 60],    # service unavailable
    "1006": [30, 60, 90],    # too many requests
    "1008": [10, 20, 30],    # statement still being processed
    "1014": [30, 60, 90],    # too many requests in queue
}


def _request_report(token: str, query_id: str, timeout: int = 20) -> str:
    """Inicia el flex report. Devuelve reference_code.

    Para errores transitorios (1001, 1005, 1006, 1008, 1014) reintenta
    automáticamente con backoff antes de fallar. Otros errores fallan al
    primer intento.

    Lanza RuntimeError con mensaje contextual si falla.
    """
    import requests
    # Trim defensivo — copy-paste suele agregar whitespace
    token = (token or "").strip()
    query_id = (query_id or "").strip()
    if not token or not query_id:
        raise ValueError("token y query_id requeridos")
    # Validación temprana de formato
    if not query_id.isdigit():
        raise ValueError(
            f"Query ID '{query_id}' no es numérico. Tiene que ser un "
            f"número de 6-9 dígitos (lo encontrás en Reports → Flex "
            f"Queries en tu cuenta IBKR)."
        )

    last_code: str | None = None
    last_msg: str | None = None
    attempt = 0
    while True:
        r = requests.get(
            SEND_URL,
            params={"t": token, "q": query_id, "v": "3"},
            timeout=timeout,
        )
        r.raise_for_status()
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            raise RuntimeError(
                f"IBKR devolvió un response no-XML inesperado "
                f"(primeros 200 chars: {r.text[:200]!r})"
            )
        status = (root.findtext("Status") or "").strip()
        if status == "Success":
            ref = root.findtext("ReferenceCode")
            if ref:
                return ref
            raise RuntimeError("IBKR Status=Success pero sin ReferenceCode")

        last_code = (root.findtext("ErrorCode") or "").strip() or None
        last_msg = (root.findtext("ErrorMessage") or "").strip() or None

        # Si es transitorio y todavía quedan reintentos → esperar y reintentar
        backoffs = _TRANSIENT_BACKOFFS.get(last_code or "")
        if backoffs and attempt < len(backoffs):
            wait = backoffs[attempt]
            print(f"[ibkr_flex] code={last_code} transitorio, esperando {wait}s "
                  f"(retry {attempt+1}/{len(backoffs)})", flush=True)
            time.sleep(wait)
            attempt += 1
            continue
        break

    # Falló — mapear el code a un mensaje útil
    hint = _IBKR_ERROR_HINTS.get(last_code or "", "")
    if not hint and last_msg:
        hint = last_msg
    detail = f"code={last_code}" if last_code else "sin código"
    if last_msg:
        detail += f", msg='{last_msg}'"
    retried_note = ""
    if last_code in _TRANSIENT_BACKOFFS:
        retried_note = (
            f"\n\nLa app reintentó {len(_TRANSIENT_BACKOFFS[last_code])} veces "
            f"con backoff antes de fallar — el error es persistente del lado "
            f"de IBKR. Esperá 5-10 minutos y volvé a probar.\n"
        )
    raise RuntimeError(
        f"IBKR Flex SendRequest falló ({detail}).\n\n"
        f"💡 {hint}{retried_note}\n\n"
        f"Setup de IBKR Flex:\n"
        f"  1. Reports → Flex Queries → New (Custom)\n"
        f"  2. Sections: 'Open Positions' (mandatorio) + 'Trades' "
        f"(opcional)\n"
        f"  3. Format: XML, Period: 'Last Business Day' (o ajustá)\n"
        f"  4. Save → guardá el Query ID (número de 6-9 dígitos)\n"
        f"  5. Settings → User Settings → Flex Web Service → Generate "
        f"Token (válido 1 año)\n"
    )


def _get_report(token: str, reference_code: str, max_attempts: int = 8,
                 backoff: float = 2.0, timeout: int = 30) -> str:
    """Polling del report hasta que esté listo. Devuelve el XML como str."""
    import requests
    for attempt in range(max_attempts):
        r = requests.get(
            DEFAULT_GET_URL,
            params={"t": token, "q": reference_code, "v": "3"},
            timeout=timeout,
        )
        r.raise_for_status()
        text = r.text
        # Si está pendiente, IBKR devuelve un XML con Status=Warn y
        # ErrorCode=1019 (Statement is being prepared)
        if "<FlexStatementResponse" in text:
            try:
                root = ET.fromstring(text)
                code = root.findtext("ErrorCode") or ""
                if code in ("1019", "1018"):  # in progress
                    time.sleep(backoff * (attempt + 1))
                    continue
                msg = (root.findtext("ErrorMessage") or
                       f"Status={root.findtext('Status')}")
                raise RuntimeError(f"IBKR Flex GetStatement falló: {msg}")
            except ET.ParseError:
                pass
        # Caso happy: el body ES el reporte
        if "<FlexQueryResponse" in text:
            return text
        # Caso ambiguo: esperar y reintentar
        time.sleep(backoff * (attempt + 1))
    raise TimeoutError(
        "IBKR Flex no devolvió el report a tiempo. Probá con menos data."
    )


def _classify_secid(sec_id_type: str, sec_id: str, asset_category: str) -> str:
    """Heurística de asset_class según campos de IBKR."""
    cat = (asset_category or "").upper()
    if cat == "CASH":
        return "CASH"
    if cat == "FUT" or cat == "OPT":
        return "DERIVATIVE"
    if cat == "BOND":
        # IBKR no distingue AR/US fácilmente; ponemos BOND_US por defecto
        # (el user puede recategorizar en el preview).
        return "BOND_US"
    if cat == "ETF":
        return "ETF"
    if cat == "STK":
        return "EQUITY_US"
    if cat == "FUND":
        return "FCI"
    if cat == "CRYPTO":
        return "CRYPTO"
    return "OTHER"


def _parse_positions_xml(xml: str) -> list[dict]:
    """Extrae <OpenPosition> elements. IBKR Flex puede devolverlos
    embebidos en distintos lugares del response."""
    root = ET.fromstring(xml)
    out = []
    # Recorre todos los OpenPosition en el árbol
    for op in root.iter("OpenPosition"):
        attrs = op.attrib
        sym = (attrs.get("symbol") or attrs.get("conid") or "").strip()
        if not sym:
            continue
        try:
            qty = float(attrs.get("position") or attrs.get("positionValue") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if abs(qty) < 1e-9:
            continue
        try:
            avg = float(attrs.get("costBasisPrice") or attrs.get("openPrice")
                          or 0) or None
        except (TypeError, ValueError):
            avg = None
        ccy = (attrs.get("currency") or "USD").upper()
        cat = (attrs.get("assetCategory") or "").upper()
        cls = _classify_secid(attrs.get("secIdType", ""),
                                attrs.get("secId", ""), cat)
        is_cash = (cat == "CASH")
        out.append({
            "ticker": sym,
            "raw_ticker": sym,
            "qty": qty,
            "avg_price": avg,
            "currency": ccy,
            "asset_class": "CASH" if is_cash else cls,
            "name": attrs.get("description") or sym,
            "is_cash": is_cash,
        })
    # CashReport / EquitySummary también tienen cash. Algunos reports
    # ponen cash en <CashReportCurrency>.
    for cash in root.iter("CashReportCurrency"):
        attrs = cash.attrib
        try:
            qty = float(attrs.get("endingCash") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if abs(qty) < 1e-9:
            continue
        ccy = (attrs.get("currency") or "USD").upper()
        if ccy == "BASE_SUMMARY":
            continue
        out.append({
            "ticker": ccy,
            "raw_ticker": ccy,
            "qty": qty,
            "avg_price": 1.0,
            "currency": ccy,
            "asset_class": "CASH",
            "name": f"Cash {ccy}",
            "is_cash": True,
        })
    return out


def fetch_positions(creds: dict) -> dict:
    token = (creds.get("ibkr_flex_token") or "").strip()
    qid = (creds.get("ibkr_flex_query_id") or "").strip()
    if not token or not qid:
        raise ValueError(
            "Faltan credenciales IBKR (ibkr_flex_token, ibkr_flex_query_id)"
        )
    ref = _request_report(token, qid)
    xml = _get_report(token, ref)
    positions = _parse_positions_xml(xml)
    return {
        "broker": "ibkr",
        "as_of": date.today().isoformat(),
        "positions": positions,
        "warnings": [] if positions else [
            "Sin posiciones en el reporte. Verificá que tu Flex Query "
            "incluya la sección 'Open Positions' y Period adecuado."
        ],
    }


def test_credentials(creds: dict) -> dict:
    """Pide el reporte; si llega XML, las creds están OK."""
    try:
        token = (creds.get("ibkr_flex_token") or "").strip()
        qid = (creds.get("ibkr_flex_query_id") or "").strip()
        if not token or not qid:
            return {"ok": False, "error": "Faltan token / query_id"}
        ref = _request_report(token, qid)
        return {"ok": True, "reference_code": ref}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
