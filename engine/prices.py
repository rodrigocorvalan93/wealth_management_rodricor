# -*- coding: utf-8 -*-
"""
engine/prices.py

Importador de precios al motor (tabla `prices` del sqlite).

Lee CSVs estandarizados de los loaders externos:
  - data/precios_historico.csv    (BYMA: bonos, acciones, CEDEARs)
  - data/precios_cafci.csv        (CAFCI: FCIs)
  - data/precios_cripto.csv       (CoinGecko: cripto)
  - data/precios_us.csv           (yfinance: ADRs US)

Y los upsertea a la tabla `prices` del sqlite.

Formato CSV unificado:
    fecha,ticker,price,currency,source

Funciones:
    import_prices_csv(conn, path)             — importa un CSV
    auto_load_all(conn, data_dir)              — auto-import de todos los CSVs
    get_price(conn, ticker, fecha, fallback_days=14)  — query con fallback
    get_latest_price(conn, ticker)             — último precio disponible
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, timedelta
from pathlib import Path


class PriceError(Exception):
    """Error específico de precios."""


# =============================================================================
# Importer
# =============================================================================

# Aliases ES → EN (los loaders existentes a veces usan español)
HEADER_ALIASES = {
    "Fecha": "fecha",
    "Ticker": "ticker",
    "Precio": "price",
    "Moneda": "currency",
    "Fuente": "source",
    "fecha": "fecha",
    "ticker": "ticker",
    "price": "price",
    "currency": "currency",
    "source": "source",
}

EXPECTED_HEADERS_EN = {"fecha", "ticker", "price", "currency"}


def _normalize_row(row):
    """Normaliza una fila CSV: traduce headers ES → EN."""
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        normalized = HEADER_ALIASES.get(k.strip(), k.strip())
        out[normalized] = v
    return out


def import_prices_csv(conn, path):
    """Importa un CSV de precios a la tabla `prices`.
    Idempotente: PK es (fecha, ticker), upsert sobrescribe.

    Acepta headers en inglés (fecha,ticker,price,currency,source) o
    español (Fecha,Ticker,Precio,Moneda,Fuente).

    Bonos en BYMA: el feed devuelve precios como "% del valor nominal"
    (ej AL30D = 65.50 significa 65.50% de par). El qty del ledger está en
    valor nominal (VN). Para que mv = qty × price funcione (ej 1500 VN ×
    0.6550 = 982.5 USB), el price tiene que estar en DECIMAL, no en %.

    Si el asset es BOND_AR, BOND_CORP_AR o BOND_US, dividimos el precio
    por 100 al importar (convirtiendo de % a decimal). Para equities,
    ETFs, FCIs, cripto, cash: sin escalado.

    Devuelve cantidad de filas insertadas/actualizadas.
    """
    path = Path(path)
    if not path.is_file():
        return 0

    # Cargar el mapa ticker → asset_class para saber qué dividir por 100
    asset_classes = {}
    try:
        for r in conn.execute("SELECT ticker, asset_class FROM assets"):
            asset_classes[r[0]] = r[1]
    except Exception:
        pass  # tabla assets no existe todavía; sin scaling

    # Todos los bonos que BYMA cotiza en % del par necesitan /100 para
    # convertir a decimal y que qty (en VN) × price dé valor real en moneda.
    BOND_CLASSES = {"BOND_AR", "BOND_CORP_AR", "BOND_US"}
    n = 0
    skipped = 0
    scaled = 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return 0
        # Normalizar headers para validación
        normalized_headers = set(HEADER_ALIASES.get(h.strip(), h.strip()) for h in reader.fieldnames)
        if not EXPECTED_HEADERS_EN.issubset(normalized_headers):
            raise PriceError(
                f"Headers inválidos en {path}: {reader.fieldnames}. "
                f"Esperados (EN o ES): {EXPECTED_HEADERS_EN}"
            )

        for raw_row in reader:
            row = _normalize_row(raw_row)
            fecha = (row.get("fecha") or "").strip()
            ticker = (row.get("ticker") or "").strip()
            price_str = (row.get("price") or "").strip()
            currency = (row.get("currency") or "").strip()
            source = (row.get("source") or "").strip()

            if not fecha or not ticker or not price_str or not currency:
                skipped += 1
                continue

            # Acepta tanto "1234.56" como "1234,56"
            price_str_clean = price_str.replace(",", ".") if "," in price_str and "." not in price_str else price_str

            try:
                price = float(price_str_clean)
            except ValueError:
                skipped += 1
                continue

            if price <= 0:
                skipped += 1
                continue

            # Bonos: convertir % de par → decimal (BYMA devuelve %, ledger usa decimal)
            if asset_classes.get(ticker) in BOND_CLASSES:
                price = price / 100.0
                scaled += 1

            conn.execute(
                """
                INSERT INTO prices (fecha, ticker, price, currency, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(fecha, ticker) DO UPDATE SET
                    price = excluded.price,
                    currency = excluded.currency,
                    source = excluded.source
                """,
                (fecha, ticker, price, currency, source or None),
            )
            n += 1

    conn.commit()
    if skipped:
        print(f"[prices] {path.name}: {skipped} filas inválidas saltadas")
    if scaled:
        print(f"[prices] {path.name}: {scaled} precios de bonos escalados /100 (% → decimal)")
    return n


def auto_load_all(conn, data_dir="data"):
    """Auto-importa todos los CSVs de precios encontrados.

    Busca en data_dir:
      - precios_historico.csv  (BYMA)
      - precios_cafci.csv      (CAFCI)
      - precios_cripto.csv     (CoinGecko)
      - precios_us.csv         (yfinance)

    Devuelve dict {fuente: cantidad_filas}.
    """
    data_dir = Path(data_dir)
    csvs = {
        "byma":     data_dir / "precios_historico.csv",
        "cafci":    data_dir / "precios_cafci.csv",
        "cripto":   data_dir / "precios_cripto.csv",
        "yfinance": data_dir / "precios_us.csv",
        # `manual` siempre se carga ÚLTIMO para que las correcciones del
        # superadmin pisen lo que vino de los loaders automáticos.
        "manual":   data_dir / "precios_manual.csv",
    }

    stats = {}
    for source, path in csvs.items():
        try:
            n = import_prices_csv(conn, path)
            stats[source] = n
        except PriceError as e:
            print(f"[prices] WARN {source}: {e}")
            stats[source] = 0
    return stats


# =============================================================================
# Query
# =============================================================================

def get_price(conn, ticker, fecha, fallback_days=14):
    """Obtiene el precio de un ticker en una fecha.

    Si no hay precio exacto, busca hasta `fallback_days` antes (días no-feriados
    en general, pero la query incluye todos).

    Devuelve dict {price, currency, fecha_efectiva, source} o None.
    """
    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else str(fecha)

    # Match exacto primero
    cur = conn.execute(
        "SELECT price, currency, source FROM prices WHERE ticker = ? AND fecha = ?",
        (ticker, fecha_iso),
    )
    row = cur.fetchone()
    if row:
        return {
            "price": row["price"],
            "currency": row["currency"],
            "fecha_efectiva": fecha_iso,
            "source": row["source"],
            "fallback_used": False,
        }

    # Fallback: buscar el más reciente dentro de la ventana
    if fallback_days > 0:
        fecha_obj = date.fromisoformat(fecha_iso) if isinstance(fecha_iso, str) else fecha
        floor_date = (fecha_obj - timedelta(days=fallback_days)).isoformat()
        cur = conn.execute(
            """
            SELECT price, currency, fecha, source FROM prices
            WHERE ticker = ? AND fecha BETWEEN ? AND ?
            ORDER BY fecha DESC LIMIT 1
            """,
            (ticker, floor_date, fecha_iso),
        )
        row = cur.fetchone()
        if row:
            return {
                "price": row["price"],
                "currency": row["currency"],
                "fecha_efectiva": row["fecha"],
                "source": row["source"],
                "fallback_used": True,
                "fallback_days": (fecha_obj - date.fromisoformat(row["fecha"])).days,
            }

    return None


def get_latest_price(conn, ticker):
    """Último precio disponible (sin filtro de fecha)."""
    cur = conn.execute(
        """
        SELECT price, currency, fecha, source FROM prices
        WHERE ticker = ?
        ORDER BY fecha DESC LIMIT 1
        """,
        (ticker,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "price": row["price"],
        "currency": row["currency"],
        "fecha_efectiva": row["fecha"],
        "source": row["source"],
    }


def get_prices_for_assets(conn, tickers, fecha, fallback_days=14):
    """Bulk lookup: precios para una lista de tickers en una fecha.

    Devuelve dict {ticker: {price, currency, ...} | None}.
    """
    out = {}
    for t in tickers:
        out[t] = get_price(conn, t, fecha, fallback_days)
    return out
