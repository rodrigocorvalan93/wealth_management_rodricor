# -*- coding: utf-8 -*-
"""
engine/cashflow.py

Reporte de flujo de caja del hogar estilo estado de resultados (P&L):

    Ingresos
      - Sueldo, Honorarios, ...
    Egresos
      Vivienda
        - Alquiler, Expensas
      Servicios
        - Luz, Gas, Internet
      ...
    Neto = Ingresos - Egresos

Dos "bases" de cálculo (las dos vistas que pidió el usuario):

- basis="cash" (caja real): el gasto se cuenta cuando sale la plata.
    Ingresos = INCOME
    Egresos  = EXPENSE + CARD_PAYMENT   (se EXCLUYE CARD_CHARGE/CARD_INSTALLMENT)

- basis="accrual" (devengado): el gasto se cuenta en el mes de la compra.
    Ingresos = INCOME
    Egresos  = EXPENSE + CARD_CHARGE + CARD_INSTALLMENT  (se EXCLUYE CARD_PAYMENT)

Cada flujo se clasifica como invertible / no invertible según el flag
`accounts.investible` de la cuenta real que toca el movimiento (reusa el
concepto que ya existe en el sistema; no hay tag nuevo).

Anti doble-conteo de tarjeta: si para una (tarjeta, período) existe un cargo
CONSOLIDADO (resumen, external_id 'CARDSUM:...'), en la base devengada se
ignoran los cargos GRANULARES de esa tarjeta en ese mes ("el resumen gana").
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .fx import convert as fx_convert, FxError
from .categories import normalize_category

# Cuentas "técnicas" / externas que NO representan plata propia.
_EXTERNAL_KINDS = ("EXTERNAL", "OPENING_BALANCE", "INTEREST_EXPENSE", "INTEREST_INCOME")
# Cuentas que son pasivos (tarjeta/préstamo): no son "caja".
_LIABILITY_KINDS = ("CARD_CREDIT", "LIABILITY")

INCOME_GROUP = "Ingresos"
CARD_PAYMENT_GROUP = "Tarjeta"


def _account_info(conn) -> dict:
    """code -> {kind, investible}."""
    rows = conn.execute(
        "SELECT code, kind, COALESCE(investible, 1) FROM accounts"
    ).fetchall()
    return {r[0]: {"kind": r[1], "investible": int(r[2])} for r in rows}


def _convert(conn, amount, from_cur, to_cur, fecha, max_fallback_days) -> float:
    if amount is None:
        return 0.0
    if not from_cur or from_cur == to_cur:
        return float(amount)
    try:
        return float(fx_convert(conn, amount, from_cur, to_cur, fecha, max_fallback_days))
    except (FxError, Exception):
        # Degradación: si no hay FX, dejamos el valor nominal antes que romper.
        return float(amount)


def _event_legs(conn, event_type: str):
    """Filas (event_id, event_date, category, description, external_id,
    account, qty, asset) para un tipo de evento."""
    return conn.execute(
        """
        SELECT e.event_id, e.event_date, e.category, e.description, e.external_id,
               m.account, m.qty, m.asset
        FROM events e
        JOIN movements m ON m.event_id = e.event_id
        WHERE e.event_type = ?
        ORDER BY e.event_id
        """,
        (event_type,),
    ).fetchall()


def _group_by_event(rows):
    """Agrupa filas de _event_legs por event_id, preservando metadata."""
    events: dict = {}
    for eid, edate, cat, desc, ext_id, account, qty, asset in rows:
        ev = events.get(eid)
        if ev is None:
            ev = {"event_id": eid, "date": edate, "category": cat,
                  "description": desc, "external_id": ext_id, "legs": []}
            events[eid] = ev
        ev["legs"].append({"account": account, "qty": qty, "asset": asset})
    return list(events.values())


def _resumen_periods(conn) -> set:
    """Set de (tarjeta, 'YYYY-MM') que tienen un cargo consolidado (resumen)."""
    rows = conn.execute(
        """SELECT m.account, substr(e.event_date,1,7)
           FROM events e
           JOIN movements m ON m.event_id = e.event_id
           JOIN accounts a ON a.code = m.account
           WHERE a.kind = 'CARD_CREDIT'
             AND e.event_type = 'CARD_CHARGE'
             AND e.external_id LIKE 'CARDSUM:%'
             AND m.qty > 0""",
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def monthly_cashflow(
    conn: sqlite3.Connection,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    basis: str = "cash",
    investible: Optional[int] = None,
    moneda: str = "ARS",
    max_fallback_days: int = 7,
) -> dict:
    """
    Agrega ingresos y egresos por mes × categoría.

    Args:
        desde / hasta: rango ISO YYYY-MM-DD (inclusive). Si None, sin límite.
        basis: "cash" (caja real) o "accrual" (devengado).
        investible: None=todo, 1=solo invertible, 0=solo no invertible.
        moneda: moneda destino para consolidar (default ARS, vista hogar).

    Returns dict con: months, moneda, basis, investible, secciones, totales.
    """
    basis = (basis or "cash").lower()
    if basis not in ("cash", "accrual"):
        basis = "cash"

    acct = _account_info(conn)

    def is_external(code):
        info = acct.get(code)
        return bool(info and info["kind"] in _EXTERNAL_KINDS)

    def is_liability(code):
        info = acct.get(code)
        return bool(info and info["kind"] in _LIABILITY_KINDS)

    def acct_investible(code):
        info = acct.get(code)
        return int(info["investible"]) if info else 1

    def passes_invest(code):
        return investible is None or acct_investible(code) == int(investible)

    def in_range(d):
        if desde and d < desde:
            return False
        if hasta and d > hasta:
            return False
        return True

    resumen_set = _resumen_periods(conn) if basis == "accrual" else set()

    ingresos: dict = {}
    egresos: dict = {}
    months: set = set()

    def add(bucket, grupo, categoria, month, amt):
        bucket.setdefault(grupo, {}).setdefault(categoria, {})
        bucket[grupo][categoria][month] = bucket[grupo][categoria].get(month, 0.0) + amt

    # -------- Ingresos (INCOME) --------
    for ev in _group_by_event(_event_legs(conn, "INCOME")):
        d = ev["date"]
        if not d or not in_range(d):
            continue
        picked = [l for l in ev["legs"] if not is_external(l["account"]) and l["qty"] > 0]
        if not picked:
            continue
        code = picked[0]["account"]
        if not passes_invest(code):
            continue
        amt = sum(_convert(conn, l["qty"], l["asset"], moneda, d, max_fallback_days)
                  for l in picked)
        _, categoria = normalize_category(ev["category"])
        if not ev["category"]:
            categoria = "Otros ingresos"
        month = d[:7]
        months.add(month)
        add(ingresos, INCOME_GROUP, categoria, month, amt)

    # -------- Egresos --------
    if basis == "cash":
        expense_types = ("EXPENSE", "CARD_PAYMENT")
    else:
        expense_types = ("EXPENSE", "CARD_CHARGE", "CARD_INSTALLMENT")

    for etype in expense_types:
        for ev in _group_by_event(_event_legs(conn, etype)):
            d = ev["date"]
            if not d or not in_range(d):
                continue
            month = d[:7]

            if etype in ("CARD_CHARGE", "CARD_INSTALLMENT"):
                # La "pata real" del gasto es la cuenta de tarjeta (qty > 0).
                picked = [l for l in ev["legs"]
                          if not is_external(l["account"])
                          and is_liability(l["account"]) and l["qty"] > 0]
                if not picked:
                    continue
                card = picked[0]["account"]
                is_resumen = bool(ev["external_id"] and ev["external_id"].startswith("CARDSUM:"))
                # Anti doble-conteo: si hay resumen para esta (tarjeta, período),
                # ignorar los cargos granulares de ese mes.
                if not is_resumen and (card, month) in resumen_set:
                    continue
                if not passes_invest(card):
                    continue
                amt = sum(_convert(conn, l["qty"], l["asset"], moneda, d, max_fallback_days)
                          for l in picked)
                grupo, categoria = normalize_category(ev["category"])
            elif etype == "CARD_PAYMENT":
                # Pata de caja: cuenta propia, no pasivo, qty < 0.
                picked = [l for l in ev["legs"]
                          if not is_external(l["account"])
                          and not is_liability(l["account"]) and l["qty"] < 0]
                if not picked:
                    continue
                code = picked[0]["account"]
                if not passes_invest(code):
                    continue
                amt = sum(_convert(conn, -l["qty"], l["asset"], moneda, d, max_fallback_days)
                          for l in picked)
                if ev["category"]:
                    grupo, categoria = normalize_category(ev["category"])
                else:
                    grupo, categoria = CARD_PAYMENT_GROUP, (ev["description"] or "Pago tarjeta")
            else:  # EXPENSE
                picked = [l for l in ev["legs"]
                          if not is_external(l["account"]) and l["qty"] < 0]
                if not picked:
                    continue
                code = picked[0]["account"]
                if not passes_invest(code):
                    continue
                amt = sum(_convert(conn, -l["qty"], l["asset"], moneda, d, max_fallback_days)
                          for l in picked)
                grupo, categoria = normalize_category(ev["category"])

            months.add(month)
            add(egresos, grupo, categoria, month, amt)

    months_sorted = sorted(months)

    def build_section(data):
        out = []
        for grupo in sorted(data.keys()):
            cats = data[grupo]
            cat_list = []
            grupo_tot = {m: 0.0 for m in months_sorted}
            for categoria in sorted(cats.keys()):
                values = {m: round(cats[categoria].get(m, 0.0), 2) for m in months_sorted}
                cat_total = round(sum(values.values()), 2)
                for m in months_sorted:
                    grupo_tot[m] += cats[categoria].get(m, 0.0)
                cat_list.append({"categoria": categoria, "values": values, "total": cat_total})
            grupo_tot = {m: round(v, 2) for m, v in grupo_tot.items()}
            out.append({
                "grupo": grupo,
                "categorias": cat_list,
                "total": grupo_tot,
                "total_periodo": round(sum(grupo_tot.values()), 2),
            })
        return out

    sec_ing = build_section(ingresos)
    sec_egr = build_section(egresos)

    def section_totals(section):
        tot = {m: 0.0 for m in months_sorted}
        for g in section:
            for m in months_sorted:
                tot[m] += g["total"].get(m, 0.0)
        return {m: round(v, 2) for m, v in tot.items()}

    tot_ing = section_totals(sec_ing)
    tot_egr = section_totals(sec_egr)
    neto = {m: round(tot_ing.get(m, 0.0) - tot_egr.get(m, 0.0), 2) for m in months_sorted}

    return {
        "months": months_sorted,
        "moneda": moneda,
        "basis": basis,
        "investible": investible,
        "secciones": {"ingresos": sec_ing, "egresos": sec_egr},
        "totales": {
            "ingresos": tot_ing,
            "egresos": tot_egr,
            "neto": neto,
            "ingresos_periodo": round(sum(tot_ing.values()), 2),
            "egresos_periodo": round(sum(tot_egr.values()), 2),
            "neto_periodo": round(sum(neto.values()), 2),
        },
    }
