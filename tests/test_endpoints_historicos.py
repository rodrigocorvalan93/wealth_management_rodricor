# -*- coding: utf-8 -*-
"""
test_endpoints_historicos.py

Tantea endpoints históricos conocidos de Primary/Matriz contra el OMS al
que estás conectado (Cocos por default). Reporta para cada endpoint:
  - HTTP status
  - status del payload (OK / ERROR / desconocido)
  - tamaño del response
  - primeras 600 chars para inspección

NO modifica tu loader. NO escribe archivos. Solo lee secrets.txt y prueba.

USO:
    python3 test_endpoints_historicos.py

Si querés probar con un ticker o fechas distintas, editá las constantes
SAMPLE_TICKER y FECHA_DESDE / FECHA_HASTA abajo.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests


# =============================================================================
# Carga de secrets (mismo patrón que el loader)
# =============================================================================

def load_secrets() -> None:
    candidates = [
        Path.cwd() / "secrets.txt",
        Path(__file__).parent / "secrets.txt",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        print(f"[secrets] cargado {p.name}")
        return
    print("[secrets] NO se encontró secrets.txt", file=sys.stderr)


load_secrets()


# =============================================================================
# Config
# =============================================================================

BASE_URL = os.environ.get("BYMA_API_URL", "https://api.cocos.xoms.com.ar/")
if not BASE_URL.endswith("/"):
    BASE_URL += "/"

OMS_USER = os.environ.get("OMS_USER")
OMS_PASS = os.environ.get("OMS_PASS")

# Símbolo de prueba: AL30 24hs (líquido, debería tener dato)
SAMPLE_TICKER_PLAZO = "MERV - XMEV - AL30 - 24hs"
SAMPLE_TICKER_RAW = "AL30"

# Rango de fechas: últimos 30 días
FECHA_HASTA = date.today()
FECHA_DESDE = FECHA_HASTA - timedelta(days=30)


# =============================================================================
# Login
# =============================================================================

def login() -> requests.Session:
    if not OMS_USER or not OMS_PASS:
        print("[error] faltan OMS_USER / OMS_PASS en secrets.txt", file=sys.stderr)
        sys.exit(1)
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}j_spring_security_check",
        data={"j_username": OMS_USER, "j_password": OMS_PASS},
        timeout=10,
    )
    r.raise_for_status()
    print(f"[login] OK  ({BASE_URL})\n")
    return s


# =============================================================================
# Helpers
# =============================================================================

def _summarize(text: str, max_len: int = 600) -> str:
    """Devuelve un resumen del response: si es JSON intenta indentarlo,
    sino devuelve los primeros caracteres."""
    if not text:
        return "(empty)"
    text = text.strip()
    try:
        obj = json.loads(text)
        pretty = json.dumps(obj, indent=2, ensure_ascii=False)
        if len(pretty) > max_len:
            return pretty[:max_len] + f"\n... (+{len(pretty)-max_len} chars)"
        return pretty
    except json.JSONDecodeError:
        return text[:max_len] + ("..." if len(text) > max_len else "")


def _classify(text: str) -> str:
    """Clasifica el estado del response en una palabra."""
    if not text or not text.strip():
        return "EMPTY"
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            st = obj.get("status")
            if st == "OK":
                # ¿hay data o solo esqueleto?
                for key in ("trades", "historicalData", "data", "marketData",
                            "result", "results", "items"):
                    val = obj.get(key)
                    if isinstance(val, list) and val:
                        return f"OK_DATA ({len(val)} items)"
                    if isinstance(val, dict) and any(v is not None for v in val.values()):
                        return "OK_DATA"
                return "OK_VACÍO"
            if st == "ERROR":
                msg = obj.get("description") or obj.get("message") or "?"
                return f"ERROR ({msg[:60]})"
            if st:
                return f"STATUS={st}"
        return "JSON_OK"
    except json.JSONDecodeError:
        if "<html" in text.lower() or "<!doctype" in text.lower():
            return "HTML (login redirect?)"
        return "RAW (no JSON)"


def probe(s: requests.Session, label: str, method: str, url: str,
          params: dict = None, data: dict = None) -> None:
    """Hace la request y muestra resultado."""
    print("=" * 78)
    print(f"[{label}]")
    print(f"  {method} {url}")
    if params:
        print(f"  params: {params}")
    if data:
        print(f"  data: {data}")
    try:
        if method == "GET":
            r = s.get(url, params=params, timeout=15)
        else:
            r = s.post(url, params=params, data=data, timeout=15)
    except requests.RequestException as e:
        print(f"  → EXCEPCIÓN: {type(e).__name__}: {e}\n")
        return

    cls = _classify(r.text)
    size = len(r.text or "")
    print(f"  → HTTP {r.status_code} | {cls} | {size} bytes")
    print(f"  body:")
    print("    " + _summarize(r.text).replace("\n", "\n    "))
    print()


# =============================================================================
# Suite de endpoints a tantear
# =============================================================================

def main():
    s = login()

    fdesde = FECHA_DESDE.strftime("%Y-%m-%d")
    fhasta = FECHA_HASTA.strftime("%Y-%m-%d")
    fdesde_ddmm = FECHA_DESDE.strftime("%d-%m-%Y")
    fhasta_ddmm = FECHA_HASTA.strftime("%d-%m-%Y")

    sym_enc = quote(SAMPLE_TICKER_PLAZO)

    print(f"Probando contra: {BASE_URL}")
    print(f"Símbolo: {SAMPLE_TICKER_PLAZO!r}")
    print(f"Rango:   {fdesde}  →  {fhasta}\n")

    # 1) marketdata/get — control: ya sabemos que funciona pero hoy dará null
    probe(
        s, "1. marketdata/get (control, snapshot actual)", "GET",
        f"{BASE_URL}rest/marketdata/get",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "entries": "LA,CL,ACP",
            "depth": 1,
        },
    )

    # 2) data/getHistoricalTrades — el clásico de Primary Rooftop
    probe(
        s, "2. data/getHistoricalTrades (formato YYYY-MM-DD)", "GET",
        f"{BASE_URL}rest/data/getHistoricalTrades",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde,
            "dateTo": fhasta,
        },
    )

    # 3) data/getTrades — versión sin "Historical"
    probe(
        s, "3. data/getTrades", "GET",
        f"{BASE_URL}rest/data/getTrades",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde,
            "dateTo": fhasta,
        },
    )

    # 4) data/getHistoricalPrices — variante con foco en "Prices"
    probe(
        s, "4. data/getHistoricalPrices", "GET",
        f"{BASE_URL}rest/data/getHistoricalPrices",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde,
            "dateTo": fhasta,
        },
    )

    # 5) marketdata/getHistoricalTrades — algunas versiones lo cuelgan ahí
    probe(
        s, "5. marketdata/getHistoricalTrades", "GET",
        f"{BASE_URL}rest/marketdata/getHistoricalTrades",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde,
            "dateTo": fhasta,
        },
    )

    # 6) data/getDailySettlement — settlement diario
    probe(
        s, "6. data/getDailySettlement", "GET",
        f"{BASE_URL}rest/data/getDailySettlement",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde,
            "dateTo": fhasta,
        },
    )

    # 7) data/getDailyMarketData — market data agregada por día
    probe(
        s, "7. data/getDailyMarketData", "GET",
        f"{BASE_URL}rest/data/getDailyMarketData",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde,
            "dateTo": fhasta,
        },
    )

    # 8) data/getHistoricalTrades con formato dd-mm-yyyy (algunas APIs lo aceptan así)
    probe(
        s, "8. data/getHistoricalTrades (formato dd-mm-yyyy)", "GET",
        f"{BASE_URL}rest/data/getHistoricalTrades",
        params={
            "marketId": "ROFX",
            "symbol": SAMPLE_TICKER_PLAZO,
            "dateFrom": fdesde_ddmm,
            "dateTo": fhasta_ddmm,
        },
    )

    print("=" * 78)
    print("RESUMEN")
    print("=" * 78)
    print("Buscá arriba endpoints que devolvieron 'OK_DATA (...)' — esos son los")
    print("que sirven para histórico. Si todos dan 404 / HTML / ERROR → Cocos no")
    print("expone histórico vía Primary y hay que ir por fuente externa")
    print("(IOL, IAMC, Rava, dolarapi).")


if __name__ == "__main__":
    main()
