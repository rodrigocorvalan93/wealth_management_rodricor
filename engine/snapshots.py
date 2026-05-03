# -*- coding: utf-8 -*-
"""
engine/snapshots.py

Snapshots históricos del PN para construir la equity curve.

Modelo:
  pn_snapshots(fecha, account, anchor_currency, mv_anchor, investible_only)

  - account = '__TOTAL__': total del portfolio en esa fecha/ancla
  - investible_only = 1: total excluyendo cuentas no-invertibles

Cada vez que se corre el reporte, se appenda un snapshot por cuenta
y un snapshot total. Es idempotente por (fecha, account, ancla, investible_only).

USO:
    from engine.snapshots import record_snapshots, get_equity_curve
    record_snapshots(conn, holdings, fecha, anchor_currency='USD')
    curve = get_equity_curve(conn, anchor_currency='USD')
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional


TOTAL_KEY = "__TOTAL__"
TOTAL_INV_KEY = "__TOTAL_INVESTIBLE__"


def record_snapshots(conn, holdings, fecha, anchor_currency="USD"):
    """Registra snapshots por cuenta + total + total invertible.

    Si ya existen para (fecha, account, ancla), se sobreescriben.
    Devuelve cantidad de snapshots escritos.
    """
    from .schema import insert_pn_snapshot

    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else str(fecha)

    # Agregar mv_anchor por cuenta (incluye y excluye no-invertibles)
    by_acc_all = defaultdict(float)
    by_acc_inv = defaultdict(float)
    total_all = 0.0
    total_inv = 0.0

    for h in holdings:
        if not h.get("mv_anchor_ok") or h["mv_anchor"] is None:
            continue
        acc = h["account"]
        mv = h["mv_anchor"]
        by_acc_all[acc] += mv
        total_all += mv
        if h.get("investible", True):
            by_acc_inv[acc] += mv
            total_inv += mv

    n = 0
    for acc, val in by_acc_all.items():
        insert_pn_snapshot(
            conn, fecha=fecha_iso, account=acc,
            anchor_currency=anchor_currency, mv_anchor=val,
            investible_only=0,
        )
        n += 1

    # Total all
    insert_pn_snapshot(
        conn, fecha=fecha_iso, account=TOTAL_KEY,
        anchor_currency=anchor_currency, mv_anchor=total_all,
        investible_only=0,
        notes="Total portfolio (incluye no-invertibles)",
    )
    n += 1

    # Total invertible
    insert_pn_snapshot(
        conn, fecha=fecha_iso, account=TOTAL_INV_KEY,
        anchor_currency=anchor_currency, mv_anchor=total_inv,
        investible_only=1,
        notes="Total invertible (excluye reserva no declarada, etc)",
    )
    n += 1

    conn.commit()
    return n


def get_equity_curve(conn, anchor_currency="USD",
                     fecha_desde=None, fecha_hasta=None,
                     account=None, investible_only=False):
    """Obtiene la serie temporal del PN.

    Args:
        anchor_currency: filtra por moneda ancla
        fecha_desde, fecha_hasta: ventana opcional (ISO o date)
        account: si se pasa, solo esa cuenta. Por default: '__TOTAL__'
        investible_only: si True, usa el snapshot que excluye no-invertibles.

    Devuelve lista de dicts [{fecha, mv_anchor}, ...] ordenada asc por fecha.
    """
    if account is None:
        account = TOTAL_INV_KEY if investible_only else TOTAL_KEY

    where = ["anchor_currency = ?", "account = ?"]
    params = [anchor_currency, account]

    if investible_only and account == TOTAL_INV_KEY:
        where.append("investible_only = 1")
    else:
        where.append("investible_only = ?")
        params.append(1 if investible_only else 0)

    if fecha_desde:
        d = fecha_desde.isoformat() if isinstance(fecha_desde, date) else fecha_desde
        where.append("fecha >= ?")
        params.append(d)
    if fecha_hasta:
        d = fecha_hasta.isoformat() if isinstance(fecha_hasta, date) else fecha_hasta
        where.append("fecha <= ?")
        params.append(d)

    cur = conn.execute(
        f"""SELECT fecha, mv_anchor FROM pn_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY fecha ASC""",
        params,
    )
    return [{"fecha": r["fecha"], "mv_anchor": r["mv_anchor"]} for r in cur.fetchall()]


def get_equity_curves_by_account(conn, anchor_currency="USD",
                                  fecha_desde=None, fecha_hasta=None,
                                  investible_only=False):
    """Devuelve equity curves para todas las cuentas (excluye totales).

    Returns: {account: [{fecha, mv_anchor}, ...], ...}
    """
    where = ["anchor_currency = ?",
             "account NOT IN (?, ?)",
             "investible_only = ?"]
    params = [anchor_currency, TOTAL_KEY, TOTAL_INV_KEY,
              1 if investible_only else 0]

    if fecha_desde:
        d = fecha_desde.isoformat() if isinstance(fecha_desde, date) else fecha_desde
        where.append("fecha >= ?")
        params.append(d)
    if fecha_hasta:
        d = fecha_hasta.isoformat() if isinstance(fecha_hasta, date) else fecha_hasta
        where.append("fecha <= ?")
        params.append(d)

    cur = conn.execute(
        f"""SELECT fecha, account, mv_anchor FROM pn_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY account, fecha""",
        params,
    )
    out = defaultdict(list)
    for r in cur.fetchall():
        out[r["account"]].append({
            "fecha": r["fecha"],
            "mv_anchor": r["mv_anchor"],
        })
    return dict(out)


def calculate_returns(curve):
    """Calcula retorno simple acumulado y variaciones.

    curve: lista [{fecha, mv_anchor}, ...] ordenada asc.
    Devuelve dict {
        first_value, last_value,
        total_return_abs, total_return_pct,
        n_periods,
        max_drawdown_pct, max_drawdown_abs,
    }
    """
    if not curve or len(curve) < 1:
        return {
            "first_value": 0.0, "last_value": 0.0,
            "total_return_abs": 0.0, "total_return_pct": 0.0,
            "n_periods": 0,
            "max_drawdown_pct": 0.0, "max_drawdown_abs": 0.0,
        }

    first = curve[0]["mv_anchor"]
    last = curve[-1]["mv_anchor"]
    abs_ret = last - first
    pct_ret = (abs_ret / first) if first not in (0, None) else 0.0

    # Drawdown: peak-to-trough
    peak = curve[0]["mv_anchor"]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for p in curve:
        v = p["mv_anchor"]
        if v > peak:
            peak = v
        dd_abs = v - peak
        dd_pct = (dd_abs / peak) if peak not in (0, None) else 0.0
        if dd_abs < max_dd_abs:
            max_dd_abs = dd_abs
        if dd_pct < max_dd_pct:
            max_dd_pct = dd_pct

    return {
        "first_value": first,
        "last_value": last,
        "total_return_abs": abs_ret,
        "total_return_pct": pct_ret,
        "n_periods": len(curve),
        "max_drawdown_abs": max_dd_abs,
        "max_drawdown_pct": max_dd_pct,
    }
