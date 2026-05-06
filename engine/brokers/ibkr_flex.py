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


def _request_report(token: str, query_id: str, timeout: int = 20) -> str:
    """Inicia el flex report. Devuelve reference_code."""
    import requests
    r = requests.get(
        SEND_URL,
        params={"t": token, "q": query_id, "v": "3"},
        timeout=timeout,
    )
    r.raise_for_status()
    root = ET.fromstring(r.text)
    status = (root.findtext("Status") or "").strip()
    if status != "Success":
        msg = root.findtext("ErrorMessage") or root.findtext("ErrorCode") or "?"
        raise RuntimeError(f"IBKR Flex SendRequest falló: {msg}")
    ref = root.findtext("ReferenceCode")
    if not ref:
        raise RuntimeError("IBKR Flex no devolvió ReferenceCode")
    return ref


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
