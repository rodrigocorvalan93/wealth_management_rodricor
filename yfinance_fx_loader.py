# -*- coding: utf-8 -*-
"""
yfinance_fx_loader.py

Loader de cotizaciones FX de monedas extranjeras (no-Argentina) desde Yahoo
Finance. Output: data/fx_foreign.csv.

CONTEXTO:
  - El dólar argentino (CCL/MEP/oficial) sale por `fx_loader.py` (dolarapi /
    argentinadatos). Es un mundo aparte: tiene múltiples cotizaciones según
    la operatoria, no se rige por mercados internacionales.
  - Las monedas FOREX (EUR, GBP, JPY, BRL, CAD, CHF, AUD, CNY, MXN, etc) sí
    cotizan en mercado internacional: se traen de Yahoo Finance vía pares
    contra USD (símbolos tipo `EURUSD=X`, `GBPUSD=X`, `USDJPY=X`).

CONVENCIÓN ALMACENAMIENTO:
  Cada fila guarda 1 unidad de la moneda extranjera EN USD. Ejemplo:
    fecha       moneda  rate      base  source
    2026-05-05  EUR     1.0823    USD   yfinance EURUSD=X
    2026-05-05  JPY     0.00643   USD   yfinance USDJPY=X (invertido)

  En el motor (`engine/fx.py`), `convert()` usará cross-rate vía USD o ARS
  para convertir entre estas monedas y cualquier otra.

USO:
    # snapshot del día con pares default
    python yfinance_fx_loader.py

    # custom (especificar monedas extranjeras)
    python yfinance_fx_loader.py --currencies EUR,GBP,JPY,BRL

    # histórico
    python yfinance_fx_loader.py --desde 2024-01-01

REQUIERE: pip install yfinance pandas
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    print("[error] yfinance no instalado. pip install yfinance --break-system-packages")
    sys.exit(1)


DEFAULT_OUTPUT = Path("data/fx_foreign.csv")
CSV_HEADERS = ["fecha", "moneda", "rate", "base", "source"]

# Default: monedas FX que no son ARS-related. Si tu portfolio usa otra,
# pasala por --currencies.
DEFAULT_CURRENCIES = ["EUR", "GBP", "JPY", "BRL", "CAD", "CHF", "AUD",
                      "CNY", "MXN", "CLP", "UYU"]

# Yahoo Finance usa sufijo =X para FX pairs. Algunos pares tienen el formato
# CCY1USD=X (1 CCY1 = X USD), otros vienen como USDCCY2=X (1 USD = X CCY2)
# y hay que invertir. Mapeamos cada moneda extranjera al símbolo correcto.
# Convención preferida: <CCY>USD=X para que rate = "1 CCY en USD".
PAIR_DIRECT = {  # 1 CCY = rate USD (formato CCYUSD=X)
    "EUR": "EURUSD=X",
    "GBP": "GBPUSD=X",
    "AUD": "AUDUSD=X",
    "NZD": "NZDUSD=X",
}
PAIR_INVERTED = {  # 1 USD = rate CCY (formato USDCCY=X) → invertir
    "JPY": "USDJPY=X",
    "BRL": "USDBRL=X",
    "CAD": "USDCAD=X",
    "CHF": "USDCHF=X",
    "CNY": "USDCNY=X",
    "MXN": "USDMXN=X",
    "CLP": "USDCLP=X",
    "UYU": "USDUYU=X",
    "COP": "USDCOP=X",
    "PEN": "USDPEN=X",
}


def yahoo_symbol_for(currency: str) -> tuple[str, bool] | None:
    """Devuelve (symbol_yf, invert) para la moneda dada.

    invert=True significa que el quote viene como USD/CCY y hay que hacer
    1/rate para obtener CCY/USD.
    """
    c = currency.upper()
    if c in PAIR_DIRECT:
        return (PAIR_DIRECT[c], False)
    if c in PAIR_INVERTED:
        return (PAIR_INVERTED[c], True)
    return None


def _load_existing(csv_path: Path) -> dict:
    if not csv_path.is_file():
        return {}
    out = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["fecha"], row["moneda"], row.get("base", "USD"))
            out[key] = row
    return out


def _save(csv_path: Path, rows_dict: dict) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(
        rows_dict.values(),
        key=lambda r: (r["fecha"], r["moneda"]),
        reverse=True,
    )
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(items)


def fetch_current(currencies):
    """Snapshot del día. Devuelve dict {ccy: rate_usd}."""
    out = {}
    for c in currencies:
        sym = yahoo_symbol_for(c)
        if sym is None:
            print(f"[yf-fx] WARN: {c} sin par configurado, skip", file=sys.stderr)
            continue
        symbol, invert = sym
        try:
            tk = yf.Ticker(symbol)
            try:
                price = float(tk.fast_info["lastPrice"])
            except (KeyError, TypeError, AttributeError):
                hist = tk.history(period="2d")
                if hist.empty:
                    print(f"[yf-fx] WARN sin datos {symbol}", file=sys.stderr)
                    continue
                price = float(hist["Close"].iloc[-1])
            if price <= 0:
                continue
            out[c.upper()] = (1.0 / price) if invert else price
        except Exception as e:
            print(f"[yf-fx] error {symbol}: {e}", file=sys.stderr)
    return out


def fetch_historic(currency: str, desde, hasta=None):
    """Histórico. Devuelve [(fecha_iso, rate_in_usd), ...]."""
    sym = yahoo_symbol_for(currency)
    if sym is None:
        return []
    symbol, invert = sym
    desde_str = desde.isoformat() if isinstance(desde, date) else desde
    hasta_str = hasta.isoformat() if hasta and isinstance(hasta, date) else hasta
    try:
        hist = yf.Ticker(symbol).history(start=desde_str, end=hasta_str)
        if hist.empty:
            return []
        out = []
        for idx, row in hist.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            close = float(row["Close"])
            if close <= 0:
                continue
            rate = (1.0 / close) if invert else close
            out.append((d.isoformat(), rate))
        return out
    except Exception as e:
        print(f"[yf-fx] error histórico {symbol}: {e}", file=sys.stderr)
        return []


def upsert_current(csv_path: Path, currencies, fecha=None) -> int:
    if fecha is None:
        fecha = date.today()
    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else fecha
    existing = _load_existing(csv_path)
    rates = fetch_current(currencies)
    for ccy, rate in rates.items():
        key = (fecha_iso, ccy, "USD")
        existing[key] = {
            "fecha": fecha_iso, "moneda": ccy,
            "rate": f"{rate:.6f}", "base": "USD",
            "source": f"yfinance {PAIR_DIRECT.get(ccy) or PAIR_INVERTED.get(ccy)}",
        }
    _save(csv_path, existing)
    return len(rates)


def upsert_historic(csv_path: Path, currency, desde, hasta=None) -> int:
    existing = _load_existing(csv_path)
    rows = fetch_historic(currency, desde, hasta)
    for fecha_iso, rate in rows:
        key = (fecha_iso, currency.upper(), "USD")
        existing[key] = {
            "fecha": fecha_iso, "moneda": currency.upper(),
            "rate": f"{rate:.6f}", "base": "USD",
            "source": f"yfinance {PAIR_DIRECT.get(currency.upper()) or PAIR_INVERTED.get(currency.upper())}",
        }
    _save(csv_path, existing)
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="Loader FX foráneo desde Yahoo Finance")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--currencies", type=str, default=None,
                   help=f"CSV (default: {','.join(DEFAULT_CURRENCIES)})")
    p.add_argument("--desde", type=str, default=None)
    p.add_argument("--hasta", type=str, default=None)
    args = p.parse_args()

    if args.currencies:
        currencies = [c.strip().upper() for c in args.currencies.split(",")]
    else:
        currencies = DEFAULT_CURRENCIES

    print(f"[yf-fx] currencies: {','.join(currencies)}")
    print(f"[yf-fx] output: {args.output}")

    if args.desde:
        desde = date.fromisoformat(args.desde)
        hasta = date.fromisoformat(args.hasta) if args.hasta else None
        total = 0
        for c in currencies:
            n = upsert_historic(args.output, c, desde, hasta)
            print(f"[yf-fx]   {c}: {n} filas")
            total += n
        print(f"[yf-fx] total filas: {total}")
    else:
        n = upsert_current(args.output, currencies)
        print(f"[yf-fx] snapshot: {n} cotizaciones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
