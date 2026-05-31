# -*- coding: utf-8 -*-
"""Tests del reporte de flujo de caja (engine/cashflow.py), categorías y
carga consolidada de tarjeta (resumen + detección de doble conteo)."""

import sqlite3

import pytest

from engine import schema
from engine.schema import insert_event, insert_movement
from engine.cashflow import monthly_cashflow
from engine.categories import normalize_category, CANONICAL_CATEGORIES
from engine.liabilities import detect_double_count


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(schema.SCHEMA_DDL)
    return c


def _seed_accounts(conn):
    rows = [
        ("banco", "Banco", "CASH_BANK", 1),
        ("efectivo", "Efectivo no declarado", "CASH_PHYSICAL", 0),
        ("visa", "Visa", "CARD_CREDIT", 1),
        ("external_income", "Ext", "EXTERNAL", 0),
        ("external_expense", "Ext", "EXTERNAL", 0),
    ]
    for code, name, kind, inv in rows:
        conn.execute(
            "INSERT INTO accounts(code,name,kind,currency,card_currency,investible) "
            "VALUES(?,?,?,?,?,?)",
            (code, name, kind, "ARS", "ARS" if kind == "CARD_CREDIT" else None, inv),
        )
    conn.commit()


def _income(conn, fecha, cuenta, monto, categoria=None):
    eid = insert_event(conn, event_type="INCOME", event_date=fecha, category=categoria)
    insert_movement(conn, eid, cuenta, "ARS", monto)
    insert_movement(conn, eid, "external_income", "ARS", -monto)


def _expense(conn, fecha, cuenta, monto, categoria=None):
    eid = insert_event(conn, event_type="EXPENSE", event_date=fecha, category=categoria)
    insert_movement(conn, eid, cuenta, "ARS", -monto)
    insert_movement(conn, eid, "external_expense", "ARS", monto)


def _card_charge(conn, fecha, monto, categoria=None, external_id=None):
    eid = insert_event(conn, event_type="CARD_CHARGE", event_date=fecha,
                       category=categoria, external_id=external_id)
    insert_movement(conn, eid, "visa", "ARS", monto)
    insert_movement(conn, eid, "external_expense", "ARS", -monto)


def _card_payment(conn, fecha, monto, desc="Pago Visa"):
    eid = insert_event(conn, event_type="CARD_PAYMENT", event_date=fecha, description=desc)
    insert_movement(conn, eid, "banco", "ARS", -monto)
    insert_movement(conn, eid, "visa", "ARS", -monto)


@pytest.fixture
def seeded(conn):
    _seed_accounts(conn)
    _income(conn, "2026-01-05", "banco", 1000, "Sueldo")
    _income(conn, "2026-01-10", "efectivo", 500, "Honorarios")
    _expense(conn, "2026-01-15", "banco", 200, "Edenor luz")
    _card_charge(conn, "2026-01-20", 300, "Supermercado")
    _card_payment(conn, "2026-02-10", 300)
    conn.commit()
    return conn


def test_accrual_counts_card_charge_not_payment(seeded):
    r = monthly_cashflow(seeded, basis="accrual")
    assert r["totales"]["ingresos"]["2026-01"] == 1500
    # luz 200 + card_charge 300
    assert r["totales"]["egresos"]["2026-01"] == 500
    # el pago de tarjeta NO cuenta en devengado
    assert r["totales"]["egresos"].get("2026-02", 0) == 0


def test_cash_counts_payment_not_charge(seeded):
    r = monthly_cashflow(seeded, basis="cash")
    # solo la luz (el card_charge NO es caja)
    assert r["totales"]["egresos"]["2026-01"] == 200
    # el pago de tarjeta SÍ
    assert r["totales"]["egresos"]["2026-02"] == 300


def test_investible_split(seeded):
    inv = monthly_cashflow(seeded, basis="cash", investible=1)
    noinv = monthly_cashflow(seeded, basis="cash", investible=0)
    assert inv["totales"]["ingresos"]["2026-01"] == 1000     # sueldo al banco
    assert noinv["totales"]["ingresos"]["2026-01"] == 500    # honorarios efectivo


def test_neto(seeded):
    r = monthly_cashflow(seeded, basis="cash")
    assert r["totales"]["neto"]["2026-01"] == 1300   # 1500 - 200
    assert r["totales"]["neto"]["2026-02"] == -300   # 0 - 300


def test_group_totals_consistent(seeded):
    r = monthly_cashflow(seeded, basis="accrual")
    for seccion in r["secciones"].values():
        for grupo in seccion:
            suma_cats = sum(c["total"] for c in grupo["categorias"])
            assert round(grupo["total_periodo"], 2) == round(suma_cats, 2)
            for m in r["months"]:
                suma_m = sum(c["values"].get(m, 0) for c in grupo["categorias"])
                assert round(grupo["total"].get(m, 0), 2) == round(suma_m, 2)


def test_date_range_filter(seeded):
    r = monthly_cashflow(seeded, basis="accrual", desde="2026-02-01", hasta="2026-02-28")
    assert r["months"] == []


def test_resumen_wins_no_double_count(conn):
    """Si hay resumen consolidado + cargas granulares en el mismo período,
    el devengado cuenta solo el resumen (no duplica)."""
    _seed_accounts(conn)
    _card_charge(conn, "2026-01-05", 100, "Super")
    _card_charge(conn, "2026-01-12", 150, "Resto")
    _card_charge(conn, "2026-01-31", 400, "Resumen tarjeta",
                 external_id="CARDSUM:visa:2026-01")
    conn.commit()
    r = monthly_cashflow(conn, basis="accrual")
    # debe contar SOLO el resumen (400), no 100+150+400
    assert r["totales"]["egresos"]["2026-01"] == 400


def test_detect_double_count(conn):
    _seed_accounts(conn)
    _card_charge(conn, "2026-01-05", 100, "Super")
    _card_charge(conn, "2026-01-31", 400, "Resumen",
                 external_id="CARDSUM:visa:2026-01")
    conn.commit()
    warns = detect_double_count(conn)
    assert len(warns) == 1
    assert warns[0]["tarjeta"] == "visa"
    assert warns[0]["periodo"] == "2026-01"
    assert warns[0]["n_granulares"] == 1


def test_no_double_count_when_only_resumen(conn):
    _seed_accounts(conn)
    _card_charge(conn, "2026-01-31", 400, "Resumen",
                 external_id="CARDSUM:visa:2026-01")
    conn.commit()
    assert detect_double_count(conn) == []


def test_category_column_roundtrip(conn):
    _seed_accounts(conn)
    eid = insert_event(conn, event_type="EXPENSE", event_date="2026-01-01",
                       category="Alquiler")
    row = conn.execute("SELECT category FROM events WHERE event_id=?", (eid,)).fetchone()
    assert row["category"] == "Alquiler"


def test_normalize_category():
    assert normalize_category("Pago de Edenor")[1] == "Luz"
    assert normalize_category("metrogas")[0] == "Servicios"
    assert normalize_category("Sueldo mayo") == ("Ingresos", "Sueldo")
    grupo, cat = normalize_category("xyz cosa rara")
    assert grupo == "Otros"
    assert cat == "xyz cosa rara"
    assert normalize_category("")[0] == "Otros"
    assert len(CANONICAL_CATEGORIES) > 0
