# -*- coding: utf-8 -*-
"""
engine/fx.py

Helper para conversion FX en el motor.

Modelo de la tabla `fx_rates`:
    (fecha, moneda, base) → rate
    Ej: (2026-04-30, USB, ARS) → 1180.50  (1 USB = 1180.50 ARS)

Operaciones soportadas:
- get_rate(fecha, moneda, base): rate directo o con fallback al día anterior
- convert(amount, from_ccy, to_ccy, fecha): conversión con cross-rate vía base
- import_fx_csv(): carga histórico desde data/fx_historico.csv

Convención: si moneda==base, rate=1. Si necesita cross-rate, va vía 'ARS'.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional


# Default base currency (lo que aparece en fx_loader.py de Anthropic)
DEFAULT_BASE = "ARS"

# Cuántos días hacia atrás puede buscar el motor si no hay rate exacto
FX_FALLBACK_DAYS = 7


class FxError(Exception):
    """Error de conversión FX (no se encontró rate)."""


def _to_iso(d) -> str:
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    raise ValueError(f"Fecha inválida: {d!r}")


def _direct_rate(conn, fecha_iso: str, moneda: str, base: str,
                  fallback_days: int) -> Optional[float]:
    """Búsqueda directa en fx_rates (sin recursión / cross). Devuelve None
    si no hay fila para (fecha, moneda, base) ni para los `fallback_days`
    días previos."""
    cur = conn.execute(
        "SELECT rate FROM fx_rates WHERE fecha=? AND moneda=? AND base=?",
        (fecha_iso, moneda, base),
    )
    row = cur.fetchone()
    if row is not None:
        return float(row["rate"] if hasattr(row, "keys") else row[0])

    if fallback_days > 0:
        cur = conn.execute(
            """SELECT fecha, rate FROM fx_rates
               WHERE moneda=? AND base=? AND fecha<? AND fecha>=?
               ORDER BY fecha DESC LIMIT 1""",
            (
                moneda, base, fecha_iso,
                (date.fromisoformat(fecha_iso) - timedelta(days=fallback_days)).isoformat(),
            ),
        )
        row = cur.fetchone()
        if row is not None:
            return float(row["rate"] if hasattr(row, "keys") else row[1])

    return None


def get_rate(conn, fecha, moneda: str, base: str = DEFAULT_BASE,
             fallback_days: int = FX_FALLBACK_DAYS,
             _seen: Optional[set] = None) -> Optional[float]:
    """Devuelve `rate` (1 unidad de `moneda` cuesta `rate` unidades de `base`).

    Si moneda == base devuelve 1.0.
    Estrategia de búsqueda (en orden):
      1. Rate directo en fx_rates para (fecha, moneda, base) — exacto o
         hasta `fallback_days` hacia atrás.
      2. Rate inverso: 1 / rate(base, moneda) si existiera la fila inversa.
      3. Cross-rate vía pivots ARS, USD, USB (según haga falta):
         rate(moneda, pivot) / rate(base, pivot).
      4. Stablecoin: si moneda.is_stable=1 con quote_vs, recurre con quote_vs.

    Devuelve None si no encuentra nada (caller decide qué hacer).
    """
    if moneda == base:
        return 1.0

    fecha_iso = _to_iso(fecha)

    # Guard contra recursión infinita en cross-rate
    if _seen is None:
        _seen = set()
    key = (moneda, base)
    if key in _seen:
        return None
    _seen = _seen | {key}

    # 1. Directo
    direct = _direct_rate(conn, fecha_iso, moneda, base, fallback_days)
    if direct is not None:
        return direct

    # 2. Inverso (rate(base, moneda) → 1/rate)
    inverse = _direct_rate(conn, fecha_iso, base, moneda, fallback_days)
    if inverse is not None and inverse != 0:
        return 1.0 / inverse

    # 3. Cross-rate vía pivots conocidos. Si tenemos rates contra USD pero
    #    pedimos contra ARS (o viceversa), encadenamos vía un pivote.
    PIVOTS = ("ARS", "USD", "USB")
    for pivot in PIVOTS:
        if pivot in (moneda, base):
            continue
        # rate(moneda, pivot) y rate(base, pivot) — recursión limitada
        r_m = get_rate(conn, fecha, moneda, pivot, fallback_days, _seen)
        if r_m is None:
            continue
        r_b = get_rate(conn, fecha, base, pivot, fallback_days, _seen)
        if r_b is None or r_b == 0:
            continue
        return r_m / r_b

    # 4. Stablecoin: redirigir a su quote_vs
    cur = conn.execute(
        "SELECT is_stable, quote_vs FROM currencies WHERE code=?",
        (moneda,),
    )
    row = cur.fetchone()
    if row is not None:
        is_stable = row["is_stable"] if hasattr(row, "keys") else row[0]
        quote_vs = row["quote_vs"] if hasattr(row, "keys") else row[1]
        if is_stable and quote_vs and quote_vs != moneda:
            return get_rate(conn, fecha, quote_vs, base, fallback_days, _seen)

    return None


def convert(conn, amount: float, from_ccy: str, to_ccy: str,
            fecha, base: str = DEFAULT_BASE,
            fallback_days: int = FX_FALLBACK_DAYS) -> float:
    """Convierte `amount` from_ccy → to_ccy en `fecha`.

    Llama a `get_rate(from_ccy, to_ccy)` que ya implementa la búsqueda
    directa, inversa, cross-rate vía pivots (ARS / USD / USB) y stablecoins.

    Lanza FxError si no encuentra ningún path.
    """
    if from_ccy == to_ccy:
        return amount

    # 1. Intento directo: rate(from, to). get_rate ya hace cross internamente.
    rate = get_rate(conn, fecha, from_ccy, to_ccy, fallback_days)
    if rate is not None:
        return amount * rate

    # 2. Intento explícito vía la base por defecto (ARS) — preserva
    #    compatibilidad con setups que solo tienen rates con base=ARS.
    rate_from = get_rate(conn, fecha, from_ccy, base, fallback_days)
    rate_to = get_rate(conn, fecha, to_ccy, base, fallback_days)
    if rate_from is not None and rate_to is not None and rate_to != 0:
        return amount * rate_from / rate_to

    missing = []
    if rate_from is None: missing.append(f"{from_ccy}/{base}")
    if rate_to is None: missing.append(f"{to_ccy}/{base}")
    raise FxError(
        f"Falta FX para {from_ccy}→{to_ccy} en {_to_iso(fecha)}: {missing}"
    )


def import_fx_csv(conn, csv_path: str | Path) -> int:
    """Importa fx_historico.csv al motor.
    Formato esperado: header = ['Fecha', 'Moneda', 'Compra', 'Venta', ...] o
                      header = ['fecha', 'moneda', 'rate', 'base', 'source']
    Devuelve número de filas importadas."""
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        return 0

    n = 0
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Detectar formato
        sample = reader.fieldnames or []
        sample_lower = [s.lower() for s in sample]
        for row in reader:
            row_lower = {k.lower(): v for k, v in row.items()}
            fecha = row_lower.get("fecha")
            moneda = row_lower.get("moneda")
            base = row_lower.get("base") or "ARS"
            source = row_lower.get("source") or "csv import"
            # Calcular rate: si vienen Compra/Venta, mid; si viene rate, directo
            rate_str = row_lower.get("rate")
            compra = row_lower.get("compra")
            venta = row_lower.get("venta")

            if rate_str:
                rate = float(rate_str)
            elif compra and venta:
                try:
                    rate = (float(compra) + float(venta)) / 2
                except (ValueError, TypeError):
                    continue
            else:
                continue

            if not (fecha and moneda):
                continue

            conn.execute(
                """INSERT OR REPLACE INTO fx_rates
                   (fecha, moneda, rate, base, source)
                   VALUES (?,?,?,?,?)""",
                (fecha[:10], moneda, rate, base, source),
            )
            n += 1
    conn.commit()
    return n


def auto_load_fx(conn, data_dir: str | Path = "data") -> int:
    """Carga los CSVs de FX disponibles en `data/`. Devuelve filas totales.

    Carga en orden:
      - data/fx_historico.csv  → ARS-related (USB, USD CCL, USD oficial)
      - data/fx_foreign.csv    → foreign FX vía Yahoo Finance (EUR, GBP, JPY, ...)
    """
    n = 0
    base = Path(data_dir)
    # `fx_manual.csv` se carga ÚLTIMO para que las correcciones manuales del
    # superadmin pisen lo que vino de los loaders automáticos.
    for fname in ("fx_historico.csv", "fx_foreign.csv", "fx_manual.csv"):
        p = base / fname
        if p.is_file():
            n += import_fx_csv(conn, p)
    return n
