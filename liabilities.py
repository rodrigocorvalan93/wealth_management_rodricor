# -*- coding: utf-8 -*-
"""
engine/liabilities.py

Calcula vistas de tarjetas de crédito:

  1. Saldo Actual: todo lo cargado en la tarjeta hasta hoy, no pagado.
  2. Último Resumen Cerrado: saldo del extracto más reciente que ya cerró.
  3. Próximo Vencimiento: saldo que va a vencer en el próximo ciclo.

Y de pasivos no-tarjeta:
  - Saldo de capital pendiente
  - Próximas cuotas (si tienen cronograma)
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import List, Optional

from .schema import AccountKind


@dataclass
class CardSnapshot:
    """Snapshot de una tarjeta para una fecha de referencia."""
    card_code: str
    card_name: str
    currency: str
    saldo_actual: float          # todo lo cargado, no pagado
    saldo_ultimo_resumen: float  # del último cierre
    fecha_ultimo_cierre: Optional[str]
    saldo_proximo_vto: float     # cierre actual (lo que vence próximamente)
    fecha_proximo_cierre: Optional[str]
    fecha_proximo_vto: Optional[str]


def _next_close_after(d: date, close_day: int) -> date:
    """Próximo día de cierre después (o igual) a `d`."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    actual_close_day = min(close_day, last_day)
    candidate = date(d.year, d.month, actual_close_day)
    if candidate >= d:
        return candidate
    # Próximo mes
    y, m = d.year, d.month + 1
    if m > 12:
        y += 1
        m = 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(close_day, last_day))


def _previous_close_before(d: date, close_day: int) -> Optional[date]:
    """Último día de cierre antes (estricto) de `d`."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    actual_close_day = min(close_day, last_day)
    candidate = date(d.year, d.month, actual_close_day)
    if candidate < d:
        return candidate
    # Mes anterior
    y, m = d.year, d.month - 1
    if m < 1:
        y -= 1
        m = 12
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(close_day, last_day))


def _due_date_for_close(close_d: date, due_day: int) -> date:
    """Calcula fecha de vto a partir del cierre. Asume vto en mes siguiente al cierre.

    Si due_day <= close_day, el vto es el mes siguiente (ej cierra 28, vence 10
    del mes siguiente).
    """
    y, m = close_d.year, close_d.month
    # Asumimos vto en mes siguiente al cierre
    m += 1
    if m > 12:
        y += 1
        m = 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(due_day, last_day))


def _balance_until(conn, card_code: str, currency: str, until: date) -> float:
    """Suma todos los movements en (card_code, currency) hasta `until` inclusive.

    Como CARD_CHARGE pone qty positiva en la tarjeta y CARD_PAYMENT pone qty negativa,
    la suma da el saldo al cierre del día `until`.
    """
    cur = conn.execute(
        """SELECT COALESCE(SUM(m.qty), 0) AS bal
           FROM movements m
           JOIN events e ON e.event_id = m.event_id
           WHERE m.account = ? AND m.asset = ? AND e.event_date <= ?""",
        (card_code, currency, until.isoformat()),
    )
    row = cur.fetchone()
    return float(row["bal"] or 0)


def card_snapshot(conn, card_code: str, ref_date: Optional[date] = None) -> Optional[CardSnapshot]:
    """Genera snapshot de una tarjeta. None si no es CARD_CREDIT."""
    if ref_date is None:
        ref_date = date.today()

    cur = conn.execute("SELECT * FROM accounts WHERE code = ?", (card_code,))
    acc = cur.fetchone()
    if acc is None or acc["kind"] != AccountKind.CARD_CREDIT:
        return None

    currency = acc["card_currency"] or acc["currency"]
    if not currency:
        return None
    close_day = acc["card_close_day"]
    due_day = acc["card_due_day"]

    # 1. Saldo actual: todo hasta ref_date
    saldo_actual = _balance_until(conn, card_code, currency, ref_date)

    # Si no hay ciclo configurado, devolver solo saldo actual
    fecha_ultimo_cierre = None
    saldo_ultimo = 0.0
    fecha_prox_cierre = None
    fecha_prox_vto = None
    saldo_prox = 0.0

    if close_day:
        # 2. Último resumen cerrado: el cierre más reciente <= ref_date
        last_close = _previous_close_before(
            ref_date + timedelta(days=1), close_day
        )
        # last_close puede ser ref_date si hoy es el día de cierre
        # _previous_close_before es exclusivo, lo arreglamos: si ref_date ES el día
        # de cierre, contamos el de hoy.
        last_day_in_ref_month = calendar.monthrange(ref_date.year, ref_date.month)[1]
        actual_close_in_ref_month = min(close_day, last_day_in_ref_month)
        if ref_date.day >= actual_close_in_ref_month:
            last_close = date(ref_date.year, ref_date.month, actual_close_in_ref_month)
        else:
            # Mes anterior
            y, m = ref_date.year, ref_date.month - 1
            if m < 1:
                y -= 1
                m = 12
            last_day = calendar.monthrange(y, m)[1]
            last_close = date(y, m, min(close_day, last_day))

        fecha_ultimo_cierre = last_close.isoformat()
        saldo_ultimo = _balance_until(conn, card_code, currency, last_close)

        # 3. Próximo vencimiento: cierre más próximo > last_close
        # Si last_close == ref_date, próximo cierre es el del mes siguiente
        prox_close_candidate_y = last_close.year
        prox_close_candidate_m = last_close.month + 1
        if prox_close_candidate_m > 12:
            prox_close_candidate_y += 1
            prox_close_candidate_m = 1
        last_day = calendar.monthrange(prox_close_candidate_y, prox_close_candidate_m)[1]
        prox_close = date(
            prox_close_candidate_y, prox_close_candidate_m,
            min(close_day, last_day),
        )
        fecha_prox_cierre = prox_close.isoformat()
        # Saldo del próximo vencimiento = movimientos entre last_close y prox_close
        saldo_a_prox = _balance_until(conn, card_code, currency, prox_close)
        saldo_prox = saldo_a_prox - saldo_ultimo

        if due_day:
            fecha_prox_vto = _due_date_for_close(prox_close, due_day).isoformat()

    return CardSnapshot(
        card_code=card_code,
        card_name=acc["name"],
        currency=currency,
        saldo_actual=saldo_actual,
        saldo_ultimo_resumen=saldo_ultimo,
        fecha_ultimo_cierre=fecha_ultimo_cierre,
        saldo_proximo_vto=saldo_prox,
        fecha_proximo_cierre=fecha_prox_cierre,
        fecha_proximo_vto=fecha_prox_vto,
    )


def all_card_snapshots(conn, ref_date: Optional[date] = None) -> List[CardSnapshot]:
    """Snapshot de todas las tarjetas de crédito en la DB."""
    cur = conn.execute(
        "SELECT code FROM accounts WHERE kind = ? ORDER BY code",
        (AccountKind.CARD_CREDIT,),
    )
    out = []
    for row in cur.fetchall():
        snap = card_snapshot(conn, row["code"], ref_date)
        if snap is not None:
            out.append(snap)
    return out
