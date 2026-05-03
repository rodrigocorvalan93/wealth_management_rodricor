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


def get_rate(conn, fecha, moneda: str, base: str = DEFAULT_BASE,
             fallback_days: int = FX_FALLBACK_DAYS) -> Optional[float]:
    """Devuelve `rate` (1 unidad de `moneda` cuesta `rate` unidades de `base`).

    Si moneda == base devuelve 1.0.
    Si no encuentra rate exacto, busca hasta `fallback_days` hacia atrás.
    Si moneda es una stablecoin (currencies.is_stable=1), redirige al rate de
    su quote_vs (ej USDC → usa rate de USD, paridad implícita 1:1).
    Devuelve None si no encuentra nada (caller decide qué hacer).
    """
    if moneda == base:
        return 1.0

    fecha_iso = _to_iso(fecha)

    # Búsqueda exacta primero
    cur = conn.execute(
        "SELECT rate FROM fx_rates WHERE fecha=? AND moneda=? AND base=?",
        (fecha_iso, moneda, base),
    )
    row = cur.fetchone()
    if row is not None:
        return float(row["rate"] if hasattr(row, "keys") else row[0])

    # Fallback: hasta N días hacia atrás
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

    # Si llegamos acá, no encontramos rate explícito.
    # Resolución implícita para stablecoins: si la moneda es stable y tiene
    # `quote_vs`, redirigir al rate de quote_vs (paridad 1:1).
    # Ej: USDC stable, quote_vs=USD → usa rate(USD) como rate(USDC).
    cur = conn.execute(
        "SELECT is_stable, quote_vs FROM currencies WHERE code=?",
        (moneda,),
    )
    row = cur.fetchone()
    if row is not None:
        is_stable = row["is_stable"] if hasattr(row, "keys") else row[0]
        quote_vs = row["quote_vs"] if hasattr(row, "keys") else row[1]
        if is_stable and quote_vs and quote_vs != moneda:
            # Recursión 1-nivel: pedir rate del quote_vs
            return get_rate(conn, fecha, quote_vs, base, fallback_days)

    return None


def convert(conn, amount: float, from_ccy: str, to_ccy: str,
            fecha, base: str = DEFAULT_BASE,
            fallback_days: int = FX_FALLBACK_DAYS) -> float:
    """Convierte `amount` from_ccy → to_ccy en `fecha`.

    Estrategia:
    - Si from_ccy == to_ccy, devuelve `amount`.
    - Si una de las dos es la base, usa el rate directo.
    - Si ninguna es la base, va vía cross-rate: from→base→to.

    Lanza FxError si no encuentra el rate necesario.
    """
    if from_ccy == to_ccy:
        return amount

    # Caso 1: from es la base (ARS → USB): amount / rate(USB)
    if from_ccy == base:
        rate_to = get_rate(conn, fecha, to_ccy, base, fallback_days)
        if rate_to is None:
            raise FxError(
                f"Falta FX: ({to_ccy}/{base}) en {_to_iso(fecha)} "
                f"(o hasta {fallback_days}d antes)"
            )
        return amount / rate_to

    # Caso 2: to es la base (USB → ARS): amount * rate(USB)
    if to_ccy == base:
        rate_from = get_rate(conn, fecha, from_ccy, base, fallback_days)
        if rate_from is None:
            raise FxError(
                f"Falta FX: ({from_ccy}/{base}) en {_to_iso(fecha)} "
                f"(o hasta {fallback_days}d antes)"
            )
        return amount * rate_from

    # Caso 3: cross-rate vía base. from → base → to
    rate_from = get_rate(conn, fecha, from_ccy, base, fallback_days)
    rate_to = get_rate(conn, fecha, to_ccy, base, fallback_days)
    if rate_from is None or rate_to is None:
        missing = []
        if rate_from is None: missing.append(f"{from_ccy}/{base}")
        if rate_to is None: missing.append(f"{to_ccy}/{base}")
        raise FxError(
            f"Falta FX para cross {from_ccy}→{to_ccy} en {_to_iso(fecha)}: {missing}"
        )
    return amount * rate_from / rate_to


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
    """Si existe data/fx_historico.csv, lo carga. Devuelve filas importadas."""
    p = Path(data_dir) / "fx_historico.csv"
    if p.is_file():
        return import_fx_csv(conn, p)
    return 0
