# -*- coding: utf-8 -*-
"""
test_performance.py — TWR + MWR + flujos.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_1_twr_no_flows():
    print("\n[P 1] TWR sin flujos = simple return:")
    from engine.performance import calculate_twr
    snap = [{"fecha": "2026-01-01", "mv_anchor": 100},
            {"fecha": "2026-12-31", "mv_anchor": 110}]
    twr = calculate_twr(snap, [])
    assert abs(twr["twr_pct"] - 0.10) < 1e-9, f"TWR esperado 10%, got {twr['twr_pct']}"
    print(f"  ✓ TWR 10% sin flujos")


def test_2_twr_isolates_flows():
    print("\n[P 2] TWR aísla flujos correctamente:")
    from engine.performance import calculate_twr
    # 5% antes del aporte, 4.76% después → TWR = 1.05*1.0476 - 1 ≈ 10%
    snap = [
        {"fecha": "2026-01-01", "mv_anchor": 100},
        {"fecha": "2026-07-01", "mv_anchor": 105},
        {"fecha": "2026-12-31", "mv_anchor": 160},
    ]
    flows = [{"fecha": "2026-07-15", "amount_anchor": 50}]
    twr = calculate_twr(snap, flows)
    # TWR = 1.05 * (110/105) - 1 = 1.05 * 1.0476 - 1 ≈ 10%
    assert abs(twr["twr_pct"] - 0.10) < 0.001, \
        f"TWR esperado ≈10%, got {twr['twr_pct']}"
    assert twr["n_periods"] == 2
    print(f"  ✓ TWR={twr['twr_pct']*100:.2f}% (independente del flujo de 50)")


def test_3_mwr_dietz_simple():
    print("\n[P 3] MWR Dietz sin flujos = TWR:")
    from engine.performance import calculate_mwr_dietz
    snap = [{"fecha": "2026-01-01", "mv_anchor": 100},
            {"fecha": "2026-12-31", "mv_anchor": 110}]
    mwr = calculate_mwr_dietz(snap, [])
    assert abs(mwr["mwr_pct"] - 0.10) < 1e-9
    print(f"  ✓ MWR=TWR=10% sin flujos")


def test_4_mwr_with_flow():
    print("\n[P 4] MWR pondera por timing:"  )
    from engine.performance import calculate_mwr_dietz
    # Aporte 50 a mitad de período. Total: V_begin=100, V_end=160, flow=50.
    # Modified Dietz: (160-100-50) / (100 + 0.5*50) = 10/125 = 8.0%
    # (con peso ~0.5 si el aporte cae justo a la mitad)
    snap = [
        {"fecha": "2026-01-01", "mv_anchor": 100},
        {"fecha": "2026-12-31", "mv_anchor": 160},
    ]
    flows = [{"fecha": "2026-07-01", "amount_anchor": 50}]
    mwr = calculate_mwr_dietz(snap, flows)
    # Debería estar entre 7% y 9% (dependiendo del peso exacto)
    assert mwr["mwr_pct"] > 0.07 and mwr["mwr_pct"] < 0.09, \
        f"MWR esperado ~8%, got {mwr['mwr_pct']}"
    assert mwr["total_flow"] == 50
    print(f"  ✓ MWR={mwr['mwr_pct']*100:.2f}% (con aporte mid-período)")


def test_5_no_complex_on_extreme_loss():
    print("\n[P 5] No devuelve numbers complejos cuando ret < -100%:")
    from engine.performance import calculate_twr, calculate_mwr_dietz
    # Pérdida extrema: V_end=0, V_begin=100, flow=200 → ret muy negativo
    snap = [{"fecha": "2026-01-01", "mv_anchor": 100},
            {"fecha": "2026-12-31", "mv_anchor": 0}]
    flows = [{"fecha": "2026-06-01", "amount_anchor": 200}]
    twr = calculate_twr(snap, flows)
    mwr = calculate_mwr_dietz(snap, flows)
    # Anuales pueden ser None pero NO complejos
    for v in (twr["twr_annual"], mwr["mwr_annual"]):
        assert v is None or isinstance(v, (int, float)), \
            f"Esperaba None o float, got {type(v).__name__}: {v}"
    print(f"  ✓ TWR annual={twr['twr_annual']}, MWR annual={mwr['mwr_annual']}")


def test_6_get_external_flows():
    print("\n[P 6] get_external_flows detecta sueldo + gasto + opening:")
    import sqlite3
    from engine.schema import (init_db, insert_currency, insert_account,
                                 insert_event, insert_movement)
    from engine.performance import get_external_flows

    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(Path(tmp) / "w.db", drop_existing=True)
        insert_currency(conn, "USD", "Dólar")
        insert_account(conn, "ibkr", "IBKR", "CASH_BROKER")
        insert_account(conn, "external_income", "ext-in", "EXTERNAL")
        insert_account(conn, "external_expense", "ext-ex", "EXTERNAL")
        insert_account(conn, "opening_balance", "open", "OPENING_BALANCE")

        # Opening balance: aporte inicial 1000 USD
        e1 = insert_event(conn, "ACCOUNTING_ADJUSTMENT", "2026-01-01")
        insert_movement(conn, e1, account="ibkr", asset="USD", qty=1000)
        insert_movement(conn, e1, account="opening_balance", asset="USD", qty=-1000)

        # Income: sueldo 500 USD
        e2 = insert_event(conn, "INCOME", "2026-02-15")
        insert_movement(conn, e2, account="ibkr", asset="USD", qty=500)
        insert_movement(conn, e2, account="external_income", asset="USD", qty=-500)

        # Expense: 100 USD
        e3 = insert_event(conn, "EXPENSE", "2026-03-10")
        insert_movement(conn, e3, account="ibkr", asset="USD", qty=-100)
        insert_movement(conn, e3, account="external_expense", asset="USD", qty=100)
        conn.commit()

        flows = get_external_flows(conn, "2026-01-01", "2026-12-31", "USD")
        # Esperamos 3 flujos: +1000 (opening), +500 (sueldo), -100 (gasto)
        by_fecha = {f["fecha"]: f["amount_anchor"] for f in flows}
        assert by_fecha.get("2026-01-01") == 1000, by_fecha
        assert by_fecha.get("2026-02-15") == 500, by_fecha
        assert by_fecha.get("2026-03-10") == -100, by_fecha
        total = sum(f["amount_anchor"] for f in flows)
        assert total == 1400
        print(f"  ✓ 3 flujos: +1000 (open) +500 (sueldo) -100 (gasto), neto +1400")


if __name__ == "__main__":
    tests = [
        test_1_twr_no_flows,
        test_2_twr_isolates_flows,
        test_3_mwr_dietz_simple,
        test_4_mwr_with_flow,
        test_5_no_complex_on_extreme_loss,
        test_6_get_external_flows,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            import traceback; traceback.print_exc()
            failed.append(t.__name__)
    print("\n" + "=" * 70)
    if failed:
        print(f"✗ {len(failed)}/{len(tests)} tests FALLARON: {failed}")
        sys.exit(1)
    else:
        print(f"✓ Todos los {len(tests)} tests pasaron")
    print("=" * 70)
