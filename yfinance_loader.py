# -*- coding: utf-8 -*-
"""
yfinance_loader.py

Trae precios de ADRs/Equities US desde Yahoo Finance via paquete `yfinance`.
Escribe a `data/precios_us.csv`.

USO:
    python yfinance_loader.py                                # snapshot tickers default
    python yfinance_loader.py --tickers AMZN,MSFT,SPY        # custom
    python yfinance_loader.py --desde 2026-01-01             # histórico

Default tickers (los que tenés en IBKR):
    AMZN, MSFT, SPY, MAGS, NU, AAPL

REQUIERE: pip install yfinance --break-system-packages

Output (data/precios_us.csv):
    fecha,ticker,price,currency,source
    2026-05-03,AMZN,212.45,USD,yfinance
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
    print("[error] yfinance no instalado. Corré: pip install yfinance --break-system-packages")
    sys.exit(1)


DEFAULT_OUTPUT = Path("data/precios_us.csv")
# Tickers default: los que están en `especies` (con sufijo _ADR para distinguir de CEDEARs)
DEFAULT_TICKERS = ["AMZN_ADR", "MSFT_ADR", "SPY_ADR", "MAGS_ADR", "NU_ADR", "AAPL_ADR"]

# Mapping ticker (nuestro convenio en `especies`) → ticker yfinance
# Para ADRs con sufijo, el ticker yfinance es el ticker base (sin _ADR)
INTERNAL_TO_YFINANCE = {
    # ADRs en IBKR (con sufijo _ADR en nuestro motor)
    "AMZN_ADR": "AMZN",
    "MSFT_ADR": "MSFT",
    "SPY_ADR":  "SPY",
    "MAGS_ADR": "MAGS",
    "NU_ADR":   "NU",
    "AAPL_ADR": "AAPL",
    # ADRs latam (en NYSE)
    "GGAL_ADR": "GGAL",
    "BMA_ADR":  "BMA",
    "YPF_ADR":  "YPF",
    "VIST_ADR": "VIST",
    # Sin sufijo (cuando quieras precios US directos para asset US sin sufijo)
    "AMZN": "AMZN",
    "MSFT": "MSFT",
    "SPY":  "SPY",
    "MAGS": "MAGS",
    "NU":   "NU",
    "AAPL": "AAPL",
}


CSV_HEADERS = ["fecha", "ticker", "price", "currency", "source"]


def _load_existing(csv_path):
    """Carga existente como dict {(fecha, ticker): row}."""
    if not csv_path.is_file():
        return {}
    out = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["fecha"], row["ticker"])
            out[key] = row
    return out


def _save(csv_path, rows_dict):
    """Escribe ordenado por fecha desc, ticker asc."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(
        rows_dict.values(),
        key=lambda r: (r["fecha"], r["ticker"]),
        reverse=True,
    )
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(items)


def get_current_prices(tickers):
    """Snapshot del día. Devuelve {ticker_internal: price}."""
    out = {}
    for t in tickers:
        yf_ticker = INTERNAL_TO_YFINANCE.get(t.upper(), t.upper())
        try:
            tk = yf.Ticker(yf_ticker)
            # fast_info es más rápido que info, suficiente para precio
            try:
                price = tk.fast_info["lastPrice"]
            except (KeyError, TypeError):
                # Fallback: history del último día
                hist = tk.history(period="2d")
                if hist.empty:
                    print(f"[yf] WARN: sin datos para {yf_ticker}")
                    continue
                price = float(hist["Close"].iloc[-1])
            out[t.upper()] = float(price)
        except Exception as e:
            print(f"[yf] error en {yf_ticker}: {e}")
    return out


def get_historic_prices(ticker, desde, hasta=None):
    """Histórico de un ticker. Devuelve [(fecha_iso, price)]."""
    yf_ticker = INTERNAL_TO_YFINANCE.get(ticker.upper(), ticker.upper())

    desde_str = desde.isoformat() if isinstance(desde, date) else desde
    hasta_str = hasta.isoformat() if hasta and isinstance(hasta, date) else hasta

    try:
        tk = yf.Ticker(yf_ticker)
        hist = tk.history(start=desde_str, end=hasta_str)
        if hist.empty:
            return []

        out = []
        for idx, row in hist.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            out.append((d.isoformat(), float(row["Close"])))
        return out
    except Exception as e:
        print(f"[yf] error histórico {yf_ticker}: {e}")
        return []


def upsert_current(csv_path, tickers, fecha=None):
    """Snapshot del día actual."""
    if fecha is None:
        fecha = date.today()
    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else fecha

    existing = _load_existing(csv_path)
    prices = get_current_prices(tickers)

    n = 0
    for ticker, price in prices.items():
        key = (fecha_iso, ticker)
        existing[key] = {
            "fecha": fecha_iso,
            "ticker": ticker,
            "price": f"{price:.4f}",
            "currency": "USD",
            "source": "yfinance",
        }
        n += 1

    _save(csv_path, existing)
    return n


def upsert_historic(csv_path, ticker, desde, hasta=None):
    """Histórico de un ticker."""
    existing = _load_existing(csv_path)
    rows = get_historic_prices(ticker, desde, hasta)

    n = 0
    for fecha_iso, price in rows:
        key = (fecha_iso, ticker.upper())
        existing[key] = {
            "fecha": fecha_iso,
            "ticker": ticker.upper(),
            "price": f"{price:.4f}",
            "currency": "USD",
            "source": "yfinance",
        }
        n += 1

    _save(csv_path, existing)
    return n


def main():
    p = argparse.ArgumentParser(description="Loader de precios US desde Yahoo Finance")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--tickers", type=str, default=None,
                   help=f"Lista coma-separada (default: {','.join(DEFAULT_TICKERS)})")
    p.add_argument("--desde", type=str, default=None,
                   help="Histórico desde esta fecha (YYYY-MM-DD)")
    p.add_argument("--hasta", type=str, default=None, help="Hasta esta fecha")
    args = p.parse_args()

    tickers = (
        [t.strip() for t in args.tickers.split(",")] if args.tickers
        else DEFAULT_TICKERS
    )
    tickers = [t.upper() for t in tickers]

    print(f"[yf] tickers: {','.join(tickers)}")
    print(f"[yf] output: {args.output}")

    if args.desde:
        # Modo histórico
        desde = date.fromisoformat(args.desde)
        hasta = date.fromisoformat(args.hasta) if args.hasta else None
        total = 0
        for t in tickers:
            print(f"[yf] descargando histórico {t} desde {desde}...")
            n = upsert_historic(args.output, t, desde, hasta)
            print(f"[yf]   {t}: {n} filas")
            total += n
        print(f"[yf] total filas upserteadas: {total}")
    else:
        # Modo snapshot
        n = upsert_current(args.output, tickers)
        print(f"[yf] snapshot: {n} precios actualizados")

    return 0


if __name__ == "__main__":
    sys.exit(main())
