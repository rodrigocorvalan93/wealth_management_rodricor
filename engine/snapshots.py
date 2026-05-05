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

    IMPORTANTE: usa `mv_pn_anchor` (signed: pasivos negativos), NO `mv_anchor`,
    para que los totales reflejen el PATRIMONIO NETO (assets - liabilities).
    Si usáramos mv_anchor crudo, los pasivos sumarían en vez de restar y
    la equity curve quedaría inflada por el monto de las deudas.

    Si ya existen para (fecha, account, ancla), se sobreescriben.
    Devuelve cantidad de snapshots escritos. Si el PN total es 0 (no hay
    holdings con FX resuelto todavía), no graba nada — un snapshot con valor 0
    rompe el cálculo de TWR/MWR (división por cero o baseline irreal).
    """
    from .schema import insert_pn_snapshot

    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else str(fecha)

    # Agregar mv_pn_anchor (signed) por cuenta (incluye y excluye no-invertibles)
    by_acc_all = defaultdict(float)
    by_acc_inv = defaultdict(float)
    total_all = 0.0
    total_inv = 0.0

    for h in holdings:
        if not h.get("mv_anchor_ok") or h.get("mv_anchor") is None:
            continue
        acc = h["account"]
        # Usar mv_pn_anchor (signed: pasivos negativos). Fallback a mv_anchor
        # si el holding no tiene la versión signada (compat con tests viejos).
        mv = h.get("mv_pn_anchor")
        if mv is None:
            mv = h["mv_anchor"]
        by_acc_all[acc] += mv
        total_all += mv
        if h.get("investible", True):
            by_acc_inv[acc] += mv
            total_inv += mv

    # Guard: si el PN total es ~0 los holdings no se resolvieron bien (FX
    # faltante, sin precios, etc). Grabar un 0 contamina la equity curve.
    if abs(total_all) < 1e-6:
        return 0

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


def backfill_snapshots(conn, anchor_currency="USD", cadence_days=7,
                        fecha_desde=None, fecha_hasta=None):
    """Reconstruye la equity curve histórica computando holdings en cada fecha.

    Walks desde `fecha_desde` (default: fecha del primer evento) hasta
    `fecha_hasta` (default: hoy), grabando un snapshot cada `cadence_days`
    días + una al final. Usa `calculate_holdings(fecha=...)` que respeta
    historia de movements + precios + FX disponibles a esa fecha.

    Snapshots con PN ~ 0 (FX faltante en fechas viejas, etc) se descartan
    silenciosamente — `record_snapshots` ya tiene ese guard.

    Returns: dict {n_dates_tried, n_snapshots_written, fecha_desde, fecha_hasta}.
    """
    from .holdings import calculate_holdings

    today = date.today()
    if fecha_hasta is None:
        fecha_hasta = today
    elif isinstance(fecha_hasta, str):
        fecha_hasta = date.fromisoformat(fecha_hasta)

    if fecha_desde is None:
        # Primer evento del usuario
        cur = conn.execute("SELECT MIN(event_date) AS d FROM events")
        row = cur.fetchone()
        first = row["d"] if row else None
        if not first:
            return {"n_dates_tried": 0, "n_snapshots_written": 0,
                    "fecha_desde": None, "fecha_hasta": fecha_hasta.isoformat()}
        fecha_desde = date.fromisoformat(first[:10])
    elif isinstance(fecha_desde, str):
        fecha_desde = date.fromisoformat(fecha_desde)

    # Generar lista de fechas: desde, desde+cadence, ..., hasta (incluye hasta)
    dates = []
    d = fecha_desde
    while d < fecha_hasta:
        dates.append(d)
        d = d + timedelta(days=cadence_days)
    dates.append(fecha_hasta)

    n_written = 0
    for d in dates:
        try:
            holdings = calculate_holdings(conn, fecha=d,
                                           anchor_currency=anchor_currency)
        except Exception:
            continue
        n_written += record_snapshots(conn, holdings, d,
                                       anchor_currency=anchor_currency)

    return {
        "n_dates_tried": len(dates),
        "n_snapshots_written": n_written,
        "fecha_desde": fecha_desde.isoformat(),
        "fecha_hasta": fecha_hasta.isoformat(),
        "cadence_days": cadence_days,
    }


def returns_by_period(conn, anchor_currency="USD", investible_only=False,
                       today=None):
    """Calcula returns simples del PN para varios períodos: 1d, 1w, 1m, 3m, ytd, 1y.

    Para cada período, busca el snapshot más cercano a `today - período`
    (con tolerancia de hasta 5 días hacia atrás) y devuelve:
        return_pct = (mv_now / mv_then) - 1
        return_abs = mv_now - mv_then
        from_date  = fecha del snapshot inicial
        n_days     = días entre los dos snapshots

    Si no hay snapshot suficientemente viejo para un período, ese período
    queda con return_pct=None.

    NOTA: este es un return SIMPLE de equity (no separa flujos de capital
    de retorno de inversión). Para TWR/MWR ver calculate_returns y futuro
    /api/performance.
    """
    from datetime import date as _date, timedelta
    today = today or _date.today()
    today_iso = today.isoformat()

    # Helper: encuentra el snapshot más cercano (hacia abajo) a target_date,
    # con tolerancia hacia atrás de tolerance_days (porque puede no haber
    # snapshot exacto del fin de semana o feriado).
    account = TOTAL_INV_KEY if investible_only else TOTAL_KEY

    def _snapshot_at(target_date, tolerance_days=5):
        target_iso = target_date.isoformat()
        floor_iso = (target_date - timedelta(days=tolerance_days)).isoformat()
        cur = conn.execute(
            """SELECT fecha, mv_anchor FROM pn_snapshots
               WHERE anchor_currency = ? AND account = ?
                 AND investible_only = ?
                 AND fecha <= ? AND fecha >= ?
               ORDER BY fecha DESC LIMIT 1""",
            (anchor_currency, account, 1 if investible_only else 0,
             target_iso, floor_iso),
        )
        row = cur.fetchone()
        return (row["fecha"], row["mv_anchor"]) if row else (None, None)

    # MV de hoy: snapshot más reciente <= today
    now_fecha, mv_now = _snapshot_at(today, tolerance_days=30)
    if mv_now is None:
        # Sin snapshots: devolver todo None
        return {p: {"from_date": None, "to_date": None,
                    "return_abs": None, "return_pct": None,
                    "n_days": None}
                for p in ("1d", "1w", "1m", "3m", "ytd", "1y")}

    PERIODS = [
        ("1d", today - timedelta(days=1)),
        ("1w", today - timedelta(days=7)),
        ("1m", today - timedelta(days=30)),
        ("3m", today - timedelta(days=90)),
        ("ytd", _date(today.year, 1, 1) - timedelta(days=1)),  # baseline = último día del año anterior
        ("1y", today - timedelta(days=365)),
    ]
    out = {}
    for label, target_date in PERIODS:
        from_fecha, mv_then = _snapshot_at(target_date, tolerance_days=15)
        if mv_then is None or mv_then == 0:
            out[label] = {"from_date": None, "to_date": now_fecha,
                          "return_abs": None, "return_pct": None,
                          "n_days": None}
            continue
        return_abs = mv_now - mv_then
        return_pct = (mv_now / mv_then) - 1.0
        # Días entre snapshots
        try:
            n_days = (_date.fromisoformat(now_fecha) -
                       _date.fromisoformat(from_fecha)).days
        except Exception:
            n_days = None
        out[label] = {
            "from_date": from_fecha, "to_date": now_fecha,
            "return_abs": return_abs, "return_pct": return_pct,
            "n_days": n_days,
        }
    return out


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


def trim_anomalous_leading(curve, min_ratio=0.01):
    """Descarta snapshots iniciales con valor < min_ratio * max(curve).

    Caso típico: el primer snapshot fue grabado antes de que estuvieran
    cargados los precios o el FX, dando un PN parcial (o 0). Esos puntos
    rompen TWR / MWR / total_return %, así que los filtramos antes de calcular
    métricas. Mantenemos el resto intacto.

    Si la curva entera está por debajo del umbral, devuelve la curva sin
    cambios (no hay nada con qué comparar).
    """
    if not curve:
        return curve
    max_val = max((p["mv_anchor"] for p in curve if p.get("mv_anchor") is not None),
                   default=0)
    if max_val <= 0:
        return curve
    threshold = max_val * min_ratio
    i = 0
    while i < len(curve) - 1:
        v = curve[i].get("mv_anchor")
        if v is None or abs(v) < threshold:
            i += 1
        else:
            break
    return curve[i:]


def _period_returns(curve):
    """Calcula retornos % entre puntos consecutivos. Skip puntos con valor 0."""
    rets = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]["mv_anchor"]
        curr = curve[i]["mv_anchor"]
        if prev in (0, None) or curr is None:
            continue
        rets.append((curr - prev) / prev)
    return rets


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _avg(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def _periods_per_year(curve):
    """Estima cuántos snapshots equivalen a un año, según el espaciado real
    entre fechas. Si solo hay 1 punto, asumimos 252 (días bursátiles)."""
    from datetime import date as _date
    if len(curve) < 2:
        return 252.0
    try:
        d0 = _date.fromisoformat(curve[0]["fecha"][:10])
        d1 = _date.fromisoformat(curve[-1]["fecha"][:10])
        days = (d1 - d0).days
        if days <= 0:
            return 252.0
        n_intervals = len(curve) - 1
        avg_days_between = days / n_intervals
        return 365.0 / max(avg_days_between, 1.0)
    except (ValueError, TypeError):
        return 252.0


def calculate_returns(curve, risk_free_rate=0.0):
    """Calcula métricas de performance del equity curve.

    curve: lista [{fecha, mv_anchor}, ...] ordenada asc.

    Métricas devueltas:
      first_value, last_value, total_return_abs, total_return_pct, n_periods
      max_drawdown_abs, max_drawdown_pct
      volatility_period, volatility_annual
      sharpe_ratio (annualized, usando risk_free_rate)
      sortino_ratio (penaliza solo downside)
      calmar_ratio (return / |max_drawdown|)
      avg_return_period (retorno promedio por período)
      best_period_return, worst_period_return

    risk_free_rate: tasa libre de riesgo anual (decimal). Default 0.
    """
    # Filtrar snapshots iniciales degenerados (PN parcial / sin FX). Sin esto,
    # un primer punto con valor ~0 produce total_return_pct gigantesco.
    curve = trim_anomalous_leading(curve)
    if not curve:
        return {
            "first_value": 0.0, "last_value": 0.0,
            "total_return_abs": 0.0, "total_return_pct": 0.0,
            "n_periods": 0,
            "max_drawdown_pct": 0.0, "max_drawdown_abs": 0.0,
            "volatility_period": 0.0, "volatility_annual": 0.0,
            "sharpe_ratio": None, "sortino_ratio": None,
            "calmar_ratio": None,
            "avg_return_period": 0.0,
            "best_period_return": 0.0, "worst_period_return": 0.0,
            "periods_per_year": 252.0,
        }

    first = curve[0]["mv_anchor"]
    last = curve[-1]["mv_anchor"]
    abs_ret = last - first
    pct_ret = (abs_ret / first) if first not in (0, None) else 0.0

    # Drawdown
    peak = first
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

    # Period returns
    rets = _period_returns(curve)
    if len(rets) < 2:
        return {
            "first_value": first, "last_value": last,
            "total_return_abs": abs_ret, "total_return_pct": pct_ret,
            "n_periods": len(curve),
            "max_drawdown_abs": max_dd_abs, "max_drawdown_pct": max_dd_pct,
            "volatility_period": 0.0, "volatility_annual": 0.0,
            "sharpe_ratio": None, "sortino_ratio": None,
            "calmar_ratio": None,
            "avg_return_period": _avg(rets),
            "best_period_return": max(rets) if rets else 0.0,
            "worst_period_return": min(rets) if rets else 0.0,
            "periods_per_year": _periods_per_year(curve),
        }

    avg_r = _avg(rets)
    vol_p = _stdev(rets)
    ppy = _periods_per_year(curve)
    vol_a = vol_p * (ppy ** 0.5)

    # Sharpe (annualized)
    rf_period = risk_free_rate / ppy if ppy > 0 else 0.0
    excess = [r - rf_period for r in rets]
    excess_avg = _avg(excess)
    excess_std = _stdev(excess)
    sharpe = (excess_avg / excess_std * (ppy ** 0.5)) if excess_std > 1e-12 else None

    # Sortino (downside deviation)
    downside = [r - rf_period for r in rets if r < rf_period]
    if len(downside) >= 2:
        downside_std = (sum(r * r for r in downside) / len(downside)) ** 0.5
        sortino = (excess_avg / downside_std * (ppy ** 0.5)) if downside_std > 1e-12 else None
    elif not downside:
        sortino = float("inf") if excess_avg > 0 else None
    else:
        sortino = None

    # Calmar = retorno anualizado / |max DD|
    if max_dd_pct < -1e-9:
        # CAGR aproximado: (last/first) ^ (ppy / n_periods) - 1
        years = max(len(curve) / ppy, 1e-9)
        try:
            cagr = (last / first) ** (1 / years) - 1 if first > 0 else 0.0
        except (ZeroDivisionError, ValueError):
            cagr = 0.0
        calmar = cagr / abs(max_dd_pct)
    else:
        calmar = None

    return {
        "first_value": first,
        "last_value": last,
        "total_return_abs": abs_ret,
        "total_return_pct": pct_ret,
        "n_periods": len(curve),
        "max_drawdown_abs": max_dd_abs,
        "max_drawdown_pct": max_dd_pct,
        "volatility_period": vol_p,
        "volatility_annual": vol_a,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino if sortino != float("inf") else None,
        "calmar_ratio": calmar,
        "avg_return_period": avg_r,
        "best_period_return": max(rets),
        "worst_period_return": min(rets),
        "periods_per_year": ppy,
    }
