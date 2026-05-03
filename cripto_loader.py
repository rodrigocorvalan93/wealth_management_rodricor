# -*- coding: utf-8 -*-
"""
cripto_loader.py

Trae precios cripto desde CoinGecko (API pública gratuita) y los escribe a
`data/precios_cripto.csv`.

USO:
    python cripto_loader.py                      # snapshot del día (todos los tickers default)
    python cripto_loader.py --tickers BTC,ETH    # solo algunos
    python cripto_loader.py --desde 2026-01-01   # rango histórico

API endpoints usados:
    /simple/price                  → precio actual (sin auth)
    /coins/{id}/market_chart/range → histórico (sin auth)

Rate limit: ~10-30 req/min en plan free. Si te bloquean, hace falta API key
(CoinGecko Demo plan: gratis con email).

Output (data/precios_cripto.csv):
    fecha,ticker,price,currency,source
    2026-05-03,BTC,95234.50,USD,coingecko
    2026-05-03,ETH,3456.78,USD,coingecko
    ...
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests


COINGECKO_BASE = "https://api.coingecko.com/api/v3"
DEFAULT_TIMEOUT = 30
DEFAULT_OUTPUT = Path("data/precios_cripto.csv")
DEFAULT_TICKERS = ["BTC", "ETH", "SOL", "USDT", "USDC"]

# Mapping ticker (nuestro convenio) → coin_id de CoinGecko
TICKER_TO_COINGECKO = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "SOL":   "solana",
    "USDT":  "tether",
    "USDC":  "usd-coin",
    "BNB":   "binancecoin",
    "DAI":   "dai",
    "MATIC": "matic-network",
    "TRX":   "tron",
    "GNO":   "gnosis",
    "AVAX":  "avalanche-2",
    "LINK":  "chainlink",
    "UNI":   "uniswap",
}


def _get(url, params=None, retries=3):
    """GET con retry y rate limit awareness."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"[cripto] rate limit, esperando {wait}s...")
                time.sleep(wait)
                continue
            print(f"[cripto] HTTP {r.status_code} en {url}: {r.text[:200]}")
            return None
        except requests.RequestException as e:
            print(f"[cripto] error {attempt+1}/{retries}: {e}")
            time.sleep(5)
    return None


def get_current_prices(tickers, vs_currency="usd"):
    """Trae precios actuales para una lista de tickers.
    Devuelve dict: {ticker: price_usd}."""
    coin_ids = []
    ticker_by_id = {}
    for t in tickers:
        cid = TICKER_TO_COINGECKO.get(t.upper())
        if not cid:
            print(f"[cripto] WARN: no conozco coin_id para {t}, lo skipo")
            continue
        coin_ids.append(cid)
        ticker_by_id[cid] = t.upper()
    if not coin_ids:
        return {}

    data = _get(
        f"{COINGECKO_BASE}/simple/price",
        params={"ids": ",".join(coin_ids), "vs_currencies": vs_currency},
    )
    if not data:
        return {}

    out = {}
    for cid, ticker in ticker_by_id.items():
        if cid in data:
            price = data[cid].get(vs_currency)
            if price:
                out[ticker] = float(price)
    return out


def get_historic_prices(ticker, desde, hasta=None, vs_currency="usd"):
    """Trae histórico de un ticker entre fechas. Devuelve lista [(fecha_iso, price)].

    CoinGecko free permite hasta 365d de histórico hourly por request.
    Para más, devuelve daily.
    """
    if hasta is None:
        hasta = date.today()
    cid = TICKER_TO_COINGECKO.get(ticker.upper())
    if not cid:
        print(f"[cripto] WARN: no conozco coin_id para {ticker}")
        return []

    # Convertir a timestamps unix
    desde_ts = int(datetime.combine(desde, datetime.min.time()).timestamp())
    hasta_ts = int(datetime.combine(hasta, datetime.min.time()).timestamp())

    data = _get(
        f"{COINGECKO_BASE}/coins/{cid}/market_chart/range",
        params={
            "vs_currency": vs_currency,
            "from": desde_ts,
            "to": hasta_ts,
        },
    )
    if not data or "prices" not in data:
        return []

    # Cada elemento es [timestamp_ms, price]
    # Reducir a 1 muestra por día (la última del día)
    by_day = {}
    for ts_ms, price in data["prices"]:
        d = datetime.fromtimestamp(ts_ms / 1000).date()
        by_day[d] = price  # va sobreescribiendo, queda última

    return sorted(
        (d.isoformat(), float(p)) for d, p in by_day.items()
    )


# =============================================================================
# Persistencia: data/precios_cripto.csv
# =============================================================================

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
    """Escribe el dict ordenado por fecha desc, ticker asc."""
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


def upsert_current(csv_path, tickers, fecha=None):
    """Upsert: snapshot del día actual."""
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
            "price": f"{price:.6f}",
            "currency": "USD",
            "source": "coingecko",
        }
        n += 1

    _save(csv_path, existing)
    return n


def upsert_historic(csv_path, ticker, desde, hasta=None):
    """Upsert: histórico de un ticker."""
    existing = _load_existing(csv_path)
    rows = get_historic_prices(ticker, desde, hasta)

    n = 0
    for fecha_iso, price in rows:
        key = (fecha_iso, ticker.upper())
        existing[key] = {
            "fecha": fecha_iso,
            "ticker": ticker.upper(),
            "price": f"{price:.6f}",
            "currency": "USD",
            "source": "coingecko",
        }
        n += 1

    _save(csv_path, existing)
    return n


def main():
    p = argparse.ArgumentParser(description="Loader de precios cripto desde CoinGecko")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--tickers", type=str, default=None,
                   help=f"Lista coma-separada (default: {','.join(DEFAULT_TICKERS)})")
    p.add_argument("--desde", type=str, default=None,
                   help="Bajada histórica desde esta fecha (YYYY-MM-DD)")
    p.add_argument("--hasta", type=str, default=None, help="Hasta esta fecha")
    args = p.parse_args()

    tickers = (
        args.tickers.split(",") if args.tickers
        else DEFAULT_TICKERS
    )
    tickers = [t.strip().upper() for t in tickers]

    print(f"[cripto] tickers: {','.join(tickers)}")
    print(f"[cripto] output: {args.output}")

    if args.desde:
        # Modo histórico
        desde = date.fromisoformat(args.desde)
        hasta = date.fromisoformat(args.hasta) if args.hasta else None
        total = 0
        for t in tickers:
            print(f"[cripto] descargando histórico {t} desde {desde}...")
            n = upsert_historic(args.output, t, desde, hasta)
            print(f"[cripto]   {t}: {n} filas")
            total += n
            time.sleep(2)  # rate limit awareness
        print(f"[cripto] total filas upserteadas: {total}")
    else:
        # Modo snapshot
        n = upsert_current(args.output, tickers)
        print(f"[cripto] snapshot: {n} precios actualizados")

    return 0


if __name__ == "__main__":
    sys.exit(main())
