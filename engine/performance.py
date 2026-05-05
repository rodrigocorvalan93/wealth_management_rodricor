# -*- coding: utf-8 -*-
"""
engine/performance.py

Métricas de performance "verdaderas" que separan flujos de capital
(deposits, withdrawals, sueldos, gastos) del retorno de inversión.

Métricas:
  - TWR (Time Weighted Return): retorno de la INVERSIÓN, ignora cuándo
    metiste/sacaste plata. Es el que usa la industria para comparar
    portfolios. Period-by-period: r_i = (V_end - F_i) / V_begin - 1.
    Compuesto: TWR = ∏(1 + r_i) - 1.
  - MWR / Modified Dietz: retorno PERSONAL aproximado, pondera flujos
    por tiempo. Refleja el éxito "real" para vos (incluye timing).

Flujos externos al portfolio:
  - INCOME (sueldos): entra cash
  - EXPENSE / CARD_INSTALLMENT: sale cash
  - OPENING_BALANCE: aporte inicial
  - Otros eventos (TRADE, TRANSFER_CASH, FUNDING, ACCOUNTING_ADJUSTMENT
    sin opening_balance, dividendos cobrados como INCOME): se filtran
    o cuentan según convención.

Convenciones:
  - Flujo POSITIVO = entró capital al portfolio (deposit / income / seed)
  - Flujo NEGATIVO = salió capital (withdrawal / expense)

Detección: revisamos movements en cuentas "boundary":
  - account = 'external_income'  → la qty es negativa cuando hay ingreso,
                                    así que el inflow al portfolio = -qty
  - account = 'external_expense' → la qty es positiva cuando hay gasto,
                                    el outflow = -qty
  - account = 'opening_balance'  → similar a external_income (fuente)
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

from .fx import convert as fx_convert, FxError
from .snapshots import get_equity_curve


BOUNDARY_ACCOUNTS = ("external_income", "external_expense", "opening_balance")


def get_external_flows(conn, fecha_desde, fecha_hasta, anchor_currency="USD"):
    """Devuelve flujos externos al portfolio agrupados por fecha.

    Returns:
        list[dict]: [{fecha: ISO, amount_anchor: float}, ...] ordenado asc.
        amount_anchor > 0 → entró capital, < 0 → salió.

    Cualquier movement involving una boundary account aporta al flujo:
    el "side externo" (qty positiva en boundary = el portfolio le pagó
    a la boundary = outflow). Se invierte el signo para obtener el flow
    desde la perspectiva del portfolio.
    """
    if isinstance(fecha_desde, date):
        fecha_desde = fecha_desde.isoformat()
    if isinstance(fecha_hasta, date):
        fecha_hasta = fecha_hasta.isoformat()

    placeholders = ",".join("?" for _ in BOUNDARY_ACCOUNTS)
    cur = conn.execute(
        f"""
        SELECT e.event_date AS fecha, m.qty, m.asset, e.event_type
        FROM movements m
        JOIN events e ON e.event_id = m.event_id
        WHERE m.account IN ({placeholders})
          AND e.event_date >= ?
          AND e.event_date <= ?
        ORDER BY e.event_date ASC, e.event_id ASC
        """,
        (*BOUNDARY_ACCOUNTS, fecha_desde, fecha_hasta),
    )
    # Cache de tasas de FX por (asset, fecha) — evita queries repetidas si
    # hay muchos flujos de la misma moneda en la misma fecha.
    fx_cache: dict[tuple[str, str], float] = {}

    def _convert(flow_native, asset, fecha):
        if asset == anchor_currency:
            return flow_native
        cache_key = (asset, fecha)
        if cache_key in fx_cache:
            rate = fx_cache[cache_key]
            return None if rate is None else flow_native * rate
        try:
            # Usamos amount=1 para obtener la tasa, luego multiplicamos
            converted = fx_convert(conn, 1.0, asset, anchor_currency,
                                     fecha, fallback_days=14)
            fx_cache[cache_key] = converted
            return flow_native * converted
        except FxError:
            fx_cache[cache_key] = None
            return None

    by_fecha = {}
    for row in cur.fetchall():
        fecha = row["fecha"] if hasattr(row, "__getitem__") else row[0]
        qty = row["qty"]
        asset = row["asset"]
        flow_native = -qty  # boundary ganó → portfolio perdió esa cantidad
        flow_anchor = _convert(flow_native, asset, fecha)
        if flow_anchor is None:
            continue  # FX faltante, skip silently
        by_fecha[fecha] = by_fecha.get(fecha, 0.0) + flow_anchor

    return [{"fecha": f, "amount_anchor": v}
            for f, v in sorted(by_fecha.items())]


def calculate_twr(snapshots, flows):
    """Time Weighted Return.

    Para cada par de snapshots consecutivos, calcula el sub-return
    aislando el flujo:
        r_i = (V_end - F_period) / V_begin - 1

    Donde F_period es la suma de flujos que ocurrieron entre los dos
    snapshots (asumimos al inicio del subperíodo, conservador).

    Compone:
        TWR = ∏(1 + r_i) - 1

    Args:
        snapshots: list[dict] [{fecha, mv_anchor}] ordenada asc
        flows: list[dict] [{fecha, amount_anchor}] ordenada asc

    Returns:
        dict con: twr_pct, twr_annual, n_periods, sub_returns
    """
    if len(snapshots) < 2:
        return {"twr_pct": None, "twr_annual": None, "n_periods": 0,
                "sub_returns": []}

    # Index flows por fecha para sumas eficientes
    flow_by_date = {f["fecha"]: f["amount_anchor"] for f in flows}

    sub_returns = []
    cumulative = 1.0
    for i in range(1, len(snapshots)):
        v0 = snapshots[i - 1]["mv_anchor"]
        v1 = snapshots[i]["mv_anchor"]
        f0 = snapshots[i - 1]["fecha"]
        f1 = snapshots[i]["fecha"]
        # Flujos entre (f0, f1] — incluyendo f1 (al final del período)
        f_period = sum(amt for fecha, amt in flow_by_date.items()
                        if f0 < fecha <= f1)
        if v0 == 0:
            continue
        # Sub-return: el "valor que quedó después de descontar el flujo"
        # dividido por el valor al inicio del período.
        r_i = (v1 - f_period) / v0 - 1.0
        sub_returns.append({"from": f0, "to": f1, "r": r_i,
                             "v0": v0, "v1": v1, "flow": f_period})
        cumulative *= (1.0 + r_i)
    twr_pct = cumulative - 1.0

    # Anualizar (con guards para retornos < -100%)
    twr_annual = _annualize(twr_pct, snapshots[0]["fecha"], snapshots[-1]["fecha"])

    return {"twr_pct": twr_pct, "twr_annual": twr_annual,
            "n_periods": len(sub_returns),
            "sub_returns": sub_returns}


def _annualize(period_return, fecha_desde, fecha_hasta):
    """Anualiza un retorno con guards. Devuelve None si no es computable."""
    if period_return is None:
        return None
    try:
        d0 = date.fromisoformat(fecha_desde)
        dN = date.fromisoformat(fecha_hasta)
        days = (dN - d0).days
        if days <= 0:
            return None
        base = 1.0 + period_return
        if base <= 0:
            # Pérdida ≥ 100%: el "anualizado" no tiene sentido, devolvemos None.
            return None
        return base ** (365.0 / days) - 1.0
    except (ValueError, OverflowError):
        return None


def calculate_mwr_dietz(snapshots, flows):
    """Modified Dietz return (proxy práctico del MWR / IRR).

    Fórmula:
        MWR = (V_end - V_begin - sum(F)) / (V_begin + sum(W_i * F_i))

    Donde W_i = (días entre F_i y V_end) / (días totales del período).

    Es una buena aproximación del IRR sin necesidad de resolver
    iterativamente. En la mayoría de casos prácticos difiere <0.5% del IRR.

    Args:
        snapshots: list ordenada asc; usamos el primero y el último.
        flows: list de flujos en el período.

    Returns:
        dict con: mwr_pct, mwr_annual, v_begin, v_end, total_flow.
    """
    if len(snapshots) < 2:
        return {"mwr_pct": None, "mwr_annual": None,
                "v_begin": None, "v_end": None, "total_flow": 0.0}

    v_begin = snapshots[0]["mv_anchor"]
    v_end = snapshots[-1]["mv_anchor"]
    d_begin = date.fromisoformat(snapshots[0]["fecha"])
    d_end = date.fromisoformat(snapshots[-1]["fecha"])
    total_days = (d_end - d_begin).days
    if total_days <= 0:
        return {"mwr_pct": None, "mwr_annual": None,
                "v_begin": v_begin, "v_end": v_end, "total_flow": 0.0}

    flows_in_period = [f for f in flows
                        if snapshots[0]["fecha"] < f["fecha"] <= snapshots[-1]["fecha"]]
    total_flow = sum(f["amount_anchor"] for f in flows_in_period)

    # Weighted flow: cada flujo se pondera por el % del período que
    # estuvo en el portfolio.
    weighted_flow = 0.0
    for f in flows_in_period:
        try:
            df = date.fromisoformat(f["fecha"])
            days_remaining = (d_end - df).days
            w = days_remaining / total_days if total_days > 0 else 0.0
            weighted_flow += w * f["amount_anchor"]
        except Exception:
            continue

    denom = v_begin + weighted_flow
    if abs(denom) < 1e-9:
        return {"mwr_pct": None, "mwr_annual": None,
                "v_begin": v_begin, "v_end": v_end,
                "total_flow": total_flow}

    mwr_pct = (v_end - v_begin - total_flow) / denom
    mwr_annual = _annualize(mwr_pct, snapshots[0]["fecha"], snapshots[-1]["fecha"])

    return {"mwr_pct": mwr_pct, "mwr_annual": mwr_annual,
            "v_begin": v_begin, "v_end": v_end,
            "total_flow": total_flow,
            "n_flows": len(flows_in_period)}


def performance_summary(conn, anchor_currency="USD", investible_only=False,
                         fecha_desde=None, fecha_hasta=None):
    """Devuelve un resumen completo de performance: TWR + MWR + flows.

    Args:
        conn: SQLite connection
        anchor_currency: moneda en la que se reporta
        investible_only: si True, usa solo el snapshot del PN invertible
        fecha_desde / fecha_hasta: ventana opcional (default: toda la curva)

    Returns:
        dict con todas las métricas + raw curve y flows.
    """
    curve = get_equity_curve(conn, anchor_currency=anchor_currency,
                              fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
                              investible_only=investible_only)
    if not curve:
        return {
            "anchor_currency": anchor_currency,
            "investible_only": investible_only,
            "twr": {"twr_pct": None, "twr_annual": None, "n_periods": 0},
            "mwr": {"mwr_pct": None, "mwr_annual": None,
                    "v_begin": None, "v_end": None, "total_flow": 0.0},
            "flows": [],
            "curve_points": 0,
            "from_date": None, "to_date": None,
        }

    flows = get_external_flows(conn,
                                fecha_desde=fecha_desde or curve[0]["fecha"],
                                fecha_hasta=fecha_hasta or curve[-1]["fecha"],
                                anchor_currency=anchor_currency)

    twr = calculate_twr(curve, flows)
    mwr = calculate_mwr_dietz(curve, flows)

    return {
        "anchor_currency": anchor_currency,
        "investible_only": investible_only,
        "twr": twr,
        "mwr": mwr,
        "flows": flows,
        "curve_points": len(curve),
        "from_date": curve[0]["fecha"],
        "to_date": curve[-1]["fecha"],
        "v_begin": curve[0]["mv_anchor"],
        "v_end": curve[-1]["mv_anchor"],
        "total_change_abs": curve[-1]["mv_anchor"] - curve[0]["mv_anchor"],
        "total_flows": sum(f["amount_anchor"] for f in flows),
    }
