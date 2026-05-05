# -*- coding: utf-8 -*-
"""
engine/pnl.py

Calculadora de PnL realizado usando FIFO matching.

LÓGICA:
  Para cada (cuenta, asset) con trades:
    1. Mantenemos cola FIFO de "lotes" (cada compra es un lote)
    2. Cuando hay venta, matcheamos contra el lote más viejo
    3. PnL realizado = (precio_venta - precio_compra) × qty_matched
    4. Si la venta es mayor al lote, descontamos del siguiente

OUTPUT (cada trade matcheado genera un "fill"):
  fecha_compra, fecha_venta, account, asset
  qty_matched, precio_compra, precio_venta, currency
  pnl_realizado, pnl_pct, holding_period_days

USO:
    from engine.pnl import calculate_realized_pnl
    fills = calculate_realized_pnl(conn, fecha_hasta=date.today())
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


SYSTEM_ACCOUNTS = {
    "opening_balance",
    "external_income",
    "external_expense",
    "interest_income",
    "interest_expense",
}


@dataclass
class Lot:
    """Un lote de compra (FIFO)."""
    fecha: str
    qty: float
    unit_price: float
    currency: str
    event_id: int


@dataclass
class Fill:
    """Un fill: parte de un lote vendido."""
    account: str
    asset: str
    fecha_compra: str
    fecha_venta: str
    qty: float
    precio_compra: float
    precio_venta: float
    currency: str
    pnl_realizado: float
    pnl_pct: float
    holding_period_days: int


def _get_movements(conn, fecha_hasta=None):
    """Devuelve movements ordenados por (account, asset, fecha) hasta fecha_hasta."""
    fecha_hasta_iso = fecha_hasta.isoformat() if isinstance(fecha_hasta, date) else fecha_hasta

    where_fecha = ""
    params = []
    if fecha_hasta_iso:
        where_fecha = " AND e.event_date <= ?"
        params.append(fecha_hasta_iso)

    placeholders = ",".join("?" for _ in SYSTEM_ACCOUNTS)
    params = list(SYSTEM_ACCOUNTS) + params

    cur = conn.execute(
        f"""
        SELECT m.account, m.asset, m.qty, m.unit_price, m.price_currency,
               e.event_date, e.event_id, e.event_type
        FROM movements m
        JOIN events e ON e.event_id = m.event_id
        WHERE m.account NOT IN ({placeholders})
          AND m.unit_price IS NOT NULL
          {where_fecha}
        ORDER BY m.account, m.asset, e.event_date, m.movement_id
        """,
        params,
    )
    return cur.fetchall()


def calculate_realized_pnl(conn, fecha_hasta=None):
    """Calcula PnL realizado FIFO para todo el portfolio.

    Para cada (cuenta, asset):
      - Compras (qty>0) → entran a la cola FIFO
      - Ventas (qty<0) → matchean contra los lotes más viejos primero
      - Cada match genera un Fill con su PnL

    Devuelve lista de Fills ordenados por fecha_venta desc.
    """
    conn.row_factory = sqlite3.Row
    rows = _get_movements(conn, fecha_hasta)

    # Agrupar por (account, asset)
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[(r["account"], r["asset"])].append(r)

    all_fills: List[Fill] = []

    for (account, asset), trades in by_pair.items():
        lots: deque[Lot] = deque()  # FIFO de compras

        for t in trades:
            qty = t["qty"]
            unit_price = t["unit_price"]
            currency = t["price_currency"]
            event_date = t["event_date"]
            event_id = t["event_id"]

            if qty > 0:
                # Compra → entra a la cola
                lots.append(Lot(
                    fecha=event_date,
                    qty=qty,
                    unit_price=unit_price,
                    currency=currency,
                    event_id=event_id,
                ))
            elif qty < 0:
                # Venta → matchea contra lotes
                qty_to_sell = abs(qty)
                while qty_to_sell > 1e-9 and lots:
                    lot = lots[0]
                    qty_match = min(qty_to_sell, lot.qty)
                    # NOTA: si qty_to_sell > suma de lots, el sobrante queda
                    # sin matchear (short selling implícito) — se loguea al
                    # salir del while.

                    # Calcular PnL para este match
                    pnl = (unit_price - lot.unit_price) * qty_match
                    cost_match = lot.unit_price * qty_match
                    pnl_pct = (pnl / cost_match) if cost_match else 0.0

                    # Holding period
                    try:
                        d_compra = date.fromisoformat(lot.fecha[:10])
                        d_venta = date.fromisoformat(event_date[:10])
                        holding_days = (d_venta - d_compra).days
                    except (ValueError, TypeError):
                        holding_days = 0

                    all_fills.append(Fill(
                        account=account,
                        asset=asset,
                        fecha_compra=lot.fecha,
                        fecha_venta=event_date,
                        qty=qty_match,
                        precio_compra=lot.unit_price,
                        precio_venta=unit_price,
                        currency=currency or lot.currency or "",
                        pnl_realizado=pnl,
                        pnl_pct=pnl_pct,
                        holding_period_days=holding_days,
                    ))

                    # Reducir el lote
                    lot.qty -= qty_match
                    qty_to_sell -= qty_match

                    if lot.qty < 1e-9:
                        lots.popleft()

                # Sobrante sin matchear → short / oversold. Avisar.
                if qty_to_sell > 1e-6:
                    import sys as _sys
                    print(
                        f"[pnl] WARN venta sin lots: account={account} asset={asset} "
                        f"fecha={event_date} qty_oversold={qty_to_sell:.6f}. "
                        f"Posible short selling o data input error (más SELL que BUY).",
                        file=_sys.stderr,
                    )

    # Ordenar por fecha_venta desc
    all_fills.sort(key=lambda f: f.fecha_venta, reverse=True)
    return all_fills


def aggregate_pnl_by_asset(fills):
    """Agrupa PnL realizado por asset. Devuelve {asset: {qty, pnl_total, ...}}."""
    out = defaultdict(lambda: {
        "qty_total": 0.0,
        "pnl_realizado_total": 0.0,
        "cost_basis_total": 0.0,
        "n_trades": 0,
    })
    for f in fills:
        a = out[f.asset]
        a["qty_total"] += f.qty
        a["pnl_realizado_total"] += f.pnl_realizado
        a["cost_basis_total"] += f.precio_compra * f.qty
        a["n_trades"] += 1
    return dict(out)


def aggregate_pnl_by_account(fills):
    """Agrupa PnL realizado por cuenta."""
    out = defaultdict(lambda: {"pnl_total": 0.0, "n_trades": 0})
    for f in fills:
        out[f.account]["pnl_total"] += f.pnl_realizado
        out[f.account]["n_trades"] += 1
    return dict(out)


def aggregate_pnl_by_year(fills):
    """Agrupa PnL realizado por año. Devuelve {year: {n_trades, by_currency: {ccy: pnl}}}."""
    out = {}
    for f in fills:
        try:
            year = f.fecha_venta[:4]
        except (ValueError, IndexError):
            year = "unknown"
        if year not in out:
            out[year] = {"n_trades": 0, "by_currency": {}}
        out[year]["n_trades"] += 1
        ccy = f.currency or "?"
        out[year]["by_currency"][ccy] = out[year]["by_currency"].get(ccy, 0.0) + f.pnl_realizado
    return out


def aggregate_pnl_by_year_currency(fills):
    """Variante: devuelve lista [(year, currency, n_trades, pnl_total), ...] ordenada."""
    keys = {}  # (year, ccy) → {n_trades, pnl}
    for f in fills:
        try:
            year = f.fecha_venta[:4]
        except (ValueError, IndexError):
            year = "unknown"
        ccy = f.currency or "?"
        k = (year, ccy)
        if k not in keys:
            keys[k] = {"n_trades": 0, "pnl_total": 0.0}
        keys[k]["n_trades"] += 1
        keys[k]["pnl_total"] += f.pnl_realizado

    out = []
    for (year, ccy), v in keys.items():
        out.append({
            "year": year,
            "currency": ccy,
            "n_trades": v["n_trades"],
            "pnl_total": v["pnl_total"],
        })
    out.sort(key=lambda x: (x["year"], x["currency"]), reverse=True)
    return out


def total_realized_pnl(fills, currency_filter=None):
    """Suma PnL realizado total. Si currency_filter, solo incluye esa moneda.

    Devuelve dict {currency: total_pnl}.
    """
    out = defaultdict(float)
    for f in fills:
        if currency_filter and f.currency != currency_filter:
            continue
        out[f.currency] += f.pnl_realizado
    return dict(out)


def calculate_unrealized_pnl_summary(holdings):
    """Resumen de PnL no-realizado a partir de holdings (de holdings.py).

    Devuelve dict:
        {
            total_unrealized_native: por currency
            total_unrealized_anchor: total en moneda ancla
            best_position: mejor posición
            worst_position: peor posición
            n_winners: cantidad de posiciones ganadoras
            n_losers: cantidad perdedoras
        }
    """
    total_by_ccy = defaultdict(float)
    total_anchor = 0.0
    winners = []
    losers = []

    for h in holdings:
        if h["is_cash"]:
            continue
        if h["unrealized_pnl_native"] is None:
            continue
        ccy = h["native_currency"]
        total_by_ccy[ccy] += h["unrealized_pnl_native"]

        # Convertir a anchor proporcionalmente
        if h["mv_anchor"] is not None and h["mv_native"] not in (None, 0):
            ratio = h["mv_anchor"] / h["mv_native"]
            total_anchor += h["unrealized_pnl_native"] * ratio

        if h["unrealized_pnl_native"] > 0:
            winners.append(h)
        elif h["unrealized_pnl_native"] < 0:
            losers.append(h)

    winners.sort(key=lambda h: -h["unrealized_pnl_native"])
    losers.sort(key=lambda h: h["unrealized_pnl_native"])

    return {
        "total_unrealized_by_currency": dict(total_by_ccy),
        "total_unrealized_anchor": total_anchor,
        "best_position": winners[0] if winners else None,
        "worst_position": losers[0] if losers else None,
        "n_winners": len(winners),
        "n_losers": len(losers),
    }
