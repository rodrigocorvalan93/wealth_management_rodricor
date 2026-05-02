# -*- coding: utf-8 -*-
"""
test_get_trades_params.py

Sabemos que data/getTrades existe en Cocos (devuelve OK con trades:[]),
pero no sabemos qué parámetros espera. Este script prueba 6 variantes de
nombres de parámetros para descubrirlo.

Hipótesis a testear:
  A) sin filtros (¿retorna todos los del día?)
  B) dateFrom/dateTo en formato YYYY-MM-DD (lo que ya probamos → vacío)
  C) from/to en formato YYYY-MM-DD
  D) dateFrom/dateTo + endpoint exige solo el día actual
  E) startDate/endDate
  F) startTime/endTime con epoch ms
  G) date (un solo día, formato YYYY-MM-DD)

USO:
    python3 test_get_trades_params.py
"""

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests


def load_secrets():
    for p in [Path.cwd() / "secrets.txt", Path(__file__).parent / "secrets.txt"]:
        if p.is_file():
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            return
load_secrets()

BASE = os.environ.get("BYMA_API_URL", "https://api.cocos.xoms.com.ar/")
if not BASE.endswith("/"):
    BASE += "/"

s = requests.Session()
r = s.post(f"{BASE}j_spring_security_check",
           data={"j_username": os.environ["OMS_USER"],
                 "j_password": os.environ["OMS_PASS"]}, timeout=10)
r.raise_for_status()
print(f"[login] OK ({BASE})\n")

SYM = "MERV - XMEV - AL30 - 24hs"
HOY = date.today()
AYER = HOY - timedelta(days=1)
HACE_5 = HOY - timedelta(days=5)
HACE_30 = HOY - timedelta(days=30)


def probe(label, params):
    print("=" * 78)
    print(f"[{label}]")
    print(f"  params: {params}")
    try:
        r = s.get(f"{BASE}rest/data/getTrades", params=params, timeout=15)
    except Exception as e:
        print(f"  → EXCEPCIÓN: {e}\n")
        return
    text = r.text or ""
    try:
        obj = json.loads(text)
    except Exception:
        print(f"  → HTTP {r.status_code} | RAW: {text[:200]}\n")
        return

    status = obj.get("status", "?")
    trades = obj.get("trades")
    msg = obj.get("description") or obj.get("message") or ""

    if status == "ERROR":
        verdict = f"ERROR: {msg}"
    elif isinstance(trades, list):
        if trades:
            verdict = f"✓✓✓ OK con {len(trades)} TRADES"
        else:
            verdict = "OK pero trades vacío"
    else:
        verdict = f"status={status}, sin campo trades"

    print(f"  → HTTP {r.status_code} | {verdict}")
    print(f"  body: {json.dumps(obj, ensure_ascii=False)[:400]}")
    if isinstance(trades, list) and trades:
        print(f"  primer trade: {json.dumps(trades[0], ensure_ascii=False, indent=2)[:600]}")
    print()


# A) sin filtros de fecha
probe("A. sin filtros (solo symbol)", {
    "marketId": "ROFX",
    "symbol": SYM,
})

# B) ya sabemos que da vacío, pero lo repetimos para tener referencia
probe("B. dateFrom/dateTo YYYY-MM-DD (1 mes)", {
    "marketId": "ROFX",
    "symbol": SYM,
    "dateFrom": HACE_30.strftime("%Y-%m-%d"),
    "dateTo": HOY.strftime("%Y-%m-%d"),
})

# C) variante from/to
probe("C. from/to YYYY-MM-DD", {
    "marketId": "ROFX",
    "symbol": SYM,
    "from": HACE_30.strftime("%Y-%m-%d"),
    "to": HOY.strftime("%Y-%m-%d"),
})

# D) solo viernes (último día hábil) — capaz solo trae intraday
probe("D. dateFrom=dateTo=ayer (probar día único hábil)", {
    "marketId": "ROFX",
    "symbol": SYM,
    "dateFrom": AYER.strftime("%Y-%m-%d"),
    "dateTo": AYER.strftime("%Y-%m-%d"),
})

# E) startDate/endDate (otro estándar común)
probe("E. startDate/endDate YYYY-MM-DD", {
    "marketId": "ROFX",
    "symbol": SYM,
    "startDate": HACE_5.strftime("%Y-%m-%d"),
    "endDate": HOY.strftime("%Y-%m-%d"),
})

# F) epoch milliseconds
probe("F. startTime/endTime en epoch ms", {
    "marketId": "ROFX",
    "symbol": SYM,
    "startTime": int(datetime.combine(HACE_5, datetime.min.time()).timestamp() * 1000),
    "endTime": int(datetime.combine(HOY, datetime.max.time()).timestamp() * 1000),
})

# G) un solo parámetro 'date'
probe("G. date (un solo día — viernes)", {
    "marketId": "ROFX",
    "symbol": SYM,
    "date": AYER.strftime("%Y-%m-%d"),
})

print("=" * 78)
print("LECTURA:")
print("  - Si alguna variante devuelve trades > 0 → esa es la firma correcta.")
print("  - Si TODAS devuelven [], el endpoint solo trae intraday (no sirve")
print("    para histórico) → toca ir por IOL/IAMC/dolarapi.")
print("  - Si alguna devuelve ERROR con un mensaje útil tipo 'missing param X'")
print("    → ese mensaje nos dice qué le falta.")
