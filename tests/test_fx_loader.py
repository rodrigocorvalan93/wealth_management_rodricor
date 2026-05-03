# -*- coding: utf-8 -*-
"""
test_fx_loader.py

Tests offline para fx_loader.py. Mockea las APIs (dolarapi, argentinadatos, BCRA).

USO:
    python3 test_fx_loader.py

Salida esperada: "Todos los tests pasaron." y exit 0.
"""

from __future__ import annotations

import math
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import fx_loader as fx  # noqa: E402


# =============================================================================
# Fixtures (responses mockeados con shape real, basados en outputs reales)
# =============================================================================

DOLARAPI_RESPONSE = [
    {"moneda": "USD", "casa": "oficial", "nombre": "Oficial",
     "compra": 1365, "venta": 1415,
     "fechaActualizacion": "2026-04-30T17:03:00.000Z"},
    {"moneda": "USD", "casa": "blue", "nombre": "Blue",
     "compra": 1380, "venta": 1400,
     "fechaActualizacion": "2026-05-02T11:56:00.000Z"},
    {"moneda": "USD", "casa": "bolsa", "nombre": "Bolsa",
     "compra": 1437.5, "venta": 1448.5,
     "fechaActualizacion": "2026-05-02T11:56:00.000Z"},
    {"moneda": "USD", "casa": "contadoconliqui", "nombre": "Contado con liquidación",
     "compra": 1492.9, "venta": 1494.1,
     "fechaActualizacion": "2026-05-02T11:56:00.000Z"},
    {"moneda": "USD", "casa": "mayorista", "nombre": "Mayorista",
     "compra": 1382, "venta": 1391,
     "fechaActualizacion": "2026-04-30T15:46:00.000Z"},
    {"moneda": "USD", "casa": "cripto", "nombre": "Cripto",
     "compra": 1490.6, "venta": 1490.7,
     "fechaActualizacion": "2026-05-02T11:56:00.000Z"},
]

ARGENTINADATOS_RESPONSE = [
    # 2011 - solo blue/mayorista/oficial (MEP/CCL no existían aún)
    {"casa": "blue", "compra": 4, "venta": 4, "fecha": "2011-01-03"},
    {"casa": "mayorista", "compra": 3.97, "venta": 3.98, "fecha": "2011-01-03"},
    {"casa": "oficial", "compra": 4, "venta": 4, "fecha": "2011-01-03"},
    # 2024 - todas las casas
    {"casa": "bolsa", "compra": 1100.0, "venta": 1110.0, "fecha": "2024-01-02"},
    {"casa": "contadoconliqui", "compra": 1150.0, "venta": 1155.0, "fecha": "2024-01-02"},
    {"casa": "mayorista", "compra": 825.0, "venta": 826.0, "fecha": "2024-01-02"},
    {"casa": "blue", "compra": 1080.0, "venta": 1100.0, "fecha": "2024-01-02"},
    {"casa": "bolsa", "compra": 1437.5, "venta": 1448.5, "fecha": "2026-05-01"},
    {"casa": "contadoconliqui", "compra": 1492.9, "venta": 1494.1, "fecha": "2026-05-01"},
    {"casa": "mayorista", "compra": 1382.0, "venta": 1391.0, "fecha": "2026-05-01"},
    # algunos inválidos para testear robustez
    {"casa": "bolsa", "compra": None, "venta": 1500.0, "fecha": "2026-05-02"},  # compra None
    {"casa": "bolsa", "compra": 0, "venta": 0, "fecha": "2026-05-03"},  # cero
]

BCRA_RESPONSE = {
    "status": 200,
    "metadata": {"resultset": {"count": 1, "offset": 0, "limit": 1000}},
    "results": [
        {
            "fecha": "2026-04-30",
            "detalle": [
                {"codigoMoneda": "USD", "descripcion": "DOLAR E.E.U.U.",
                 "tipoPase": 0.0, "tipoCotizacion": 1391.0}
            ]
        }
    ]
}


# =============================================================================
# Tests
# =============================================================================

def test_1_mid():
    """mid promedia compra y venta y maneja inválidos."""
    print("\nTest 1 (mid):")
    cases = [
        ((100, 110), 105.0),
        ((1437.5, 1448.5), 1443.0),  # MEP de los datos reales
        ((None, 100), float("nan")),
        ((100, "xx"), float("nan")),
        ((0, 0), 0.0),
    ]
    for (c, v), expected in cases:
        got = fx.mid(c, v)
        if math.isnan(expected):
            assert math.isnan(got), f"  ({c},{v}): esperaba NaN, got {got}"
        else:
            assert abs(got - expected) < 1e-9, \
                f"  ({c},{v}): esperaba {expected}, got {got}"
        print(f"  ({c!s:<8},{v!s:<8}) → {got}")
    print("  PASS")


def test_2_parse_iso_date():
    """parse_iso_date extrae YYYY-MM-DD de varios formatos."""
    print("\nTest 2 (parse_iso_date):")
    cases = [
        ("2026-05-02T11:56:00.000Z", "2026-05-02"),  # ISO con tz
        ("2026-05-02", "2026-05-02"),                 # solo fecha
        ("2026-05-02T15:30:00", "2026-05-02"),        # ISO sin tz
        (None, None),
        ("xx", None),
        ("", None),
        (123, None),
    ]
    for inp, expected in cases:
        got = fx.parse_iso_date(inp)
        assert got == expected, f"  {inp!r}: esperaba {expected}, got {got}"
        print(f"  {inp!r:<35} → {got}")
    print("  PASS")


def test_3_fetch_dolarapi_today():
    """fetch_dolarapi_today filtra a las 3 casas target con mid correcto."""
    print("\nTest 3 (fetch_dolarapi_today):")
    with patch.object(fx, "_get_json", return_value=DOLARAPI_RESPONSE):
        rows = fx.fetch_dolarapi_today()
    # Tienen que ser exactamente 3
    assert len(rows) == 3, f"esperaba 3 filas, got {len(rows)}"
    # Ordenadas por casa target
    assert rows[0].moneda == "USB"  # bolsa
    assert rows[1].moneda == "USD"  # contadoconliqui
    assert rows[2].moneda == "USD_OFICIAL"  # mayorista
    # MEP: (1437.5+1448.5)/2 = 1443.0
    assert abs(rows[0].rate - 1443.0) < 1e-9, f"MEP rate: {rows[0].rate}"
    # CCL: (1492.9+1494.1)/2 = 1493.5
    assert abs(rows[1].rate - 1493.5) < 1e-9, f"CCL rate: {rows[1].rate}"
    # Mayorista: (1382+1391)/2 = 1386.5
    assert abs(rows[2].rate - 1386.5) < 1e-9, f"Mayorista rate: {rows[2].rate}"
    # Fechas
    assert rows[0].fecha == "2026-05-02"  # MEP fresh
    assert rows[2].fecha == "2026-04-30"  # Mayorista último día hábil
    # Fuente
    assert all(r.fuente == "dolarapi mid" for r in rows)
    for r in rows:
        print(f"  {r.moneda:<13} fecha={r.fecha} rate={r.rate:>10,.4f}")
    print("  PASS")


def test_4_fetch_argentinadatos_filtros():
    """fetch_argentinadatos_historico filtra por casa y por rango de fechas."""
    print("\nTest 4 (fetch_argentinadatos histórico, filtros):")
    # Sin filtros: solo target casas, sin invalid
    with patch.object(fx, "_get_json", return_value=ARGENTINADATOS_RESPONSE):
        rows = fx.fetch_argentinadatos_historico()
    # Las casas válidas del fixture son: bolsa(1), CCL(1), mayorista(2 — 2011 y 2026)
    # MAS bolsa 2024 y CCL 2024 → total 6 filas en CASAS_TARGET con datos válidos
    # Los 2 con compra inválida quedan filtrados
    casas_count = {}
    for r in rows:
        casas_count[r.moneda] = casas_count.get(r.moneda, 0) + 1
    print(f"  Sin filtros: {len(rows)} filas | por casa: {casas_count}")
    assert "USB" in casas_count
    assert "USD" in casas_count
    assert "USD_OFICIAL" in casas_count

    # Con filtro de rango: solo 2024 en adelante
    with patch.object(fx, "_get_json", return_value=ARGENTINADATOS_RESPONSE):
        rows_2024 = fx.fetch_argentinadatos_historico(desde=date(2024, 1, 1))
    print(f"  desde 2024-01-01: {len(rows_2024)} filas")
    for r in rows_2024:
        assert r.fecha >= "2024-01-01"
    # Debería tener: 3 (jan 2024) + 3 (may 2026) = 6 filas
    assert len(rows_2024) == 6, f"esperaba 6, got {len(rows_2024)}"

    # Filtro estrecho: solo 2024-01-02
    with patch.object(fx, "_get_json", return_value=ARGENTINADATOS_RESPONSE):
        rows_jan = fx.fetch_argentinadatos_historico(
            desde=date(2024, 1, 2), hasta=date(2024, 1, 2)
        )
    assert len(rows_jan) == 3, f"esperaba 3 (1 por casa target), got {len(rows_jan)}"
    print(f"  solo 2024-01-02: {len(rows_jan)} filas (1 por casa target)")
    print("  PASS")


def test_5_crosscheck_mayorista_ok():
    """crosscheck cuando dolarapi y BCRA están dentro de tolerancia."""
    print("\nTest 5 (crosscheck mayorista OK):")
    # dolarapi: 1386.5, BCRA: 1391 → diff = (1391-1386.5)/1391 = 0.32% = 32 bps
    # ESTO ES > 10bps → debería dar WARN
    rows = [fx.FXRow("2026-04-30", "USD_OFICIAL", 1386.5, "ARS", "dolarapi mid")]
    bcra = ("2026-04-30", 1391.0)
    fx.crosscheck_mayorista(rows, bcra)
    # No assertions porque es solo log, pero verifico que no crashee
    # Caso dentro de tolerancia: 5bps
    rows_close = [fx.FXRow("2026-04-30", "USD_OFICIAL", 1390.3, "ARS", "dolarapi mid")]
    fx.crosscheck_mayorista(rows_close, bcra)  # debería decir OK
    print("  PASS (loggeo OK, sin crash)")


def test_6_crosscheck_bcra_none():
    """crosscheck no rompe si BCRA no devolvió datos."""
    print("\nTest 6 (crosscheck con BCRA None):")
    rows = [fx.FXRow("2026-04-30", "USD_OFICIAL", 1386.5, "ARS", "dolarapi mid")]
    fx.crosscheck_mayorista(rows, None)  # no debe crashear
    print("  PASS")


def test_7_upsert_csv():
    """upsert_csv crea, anexa y actualiza correctamente con clave (Fecha, Moneda)."""
    print("\nTest 7 (upsert CSV):")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fx_historico.csv"

        # 1ra: 3 filas nuevas
        rows1 = [
            {"Fecha": "2026-04-30", "Moneda": "USB",
             "Rate": 1443.0, "Cotiza vs": "ARS", "Fuente": "dolarapi mid"},
            {"Fecha": "2026-04-30", "Moneda": "USD",
             "Rate": 1493.5, "Cotiza vs": "ARS", "Fuente": "dolarapi mid"},
            {"Fecha": "2026-04-30", "Moneda": "USD_OFICIAL",
             "Rate": 1386.5, "Cotiza vs": "ARS", "Fuente": "dolarapi mid"},
        ]
        n_new, n_upd = fx.upsert_csv(
            path, rows1,
            key_cols=["Fecha", "Moneda"],
            column_order=["Fecha", "Moneda", "Rate", "Cotiza vs", "Fuente"],
        )
        assert (n_new, n_upd) == (3, 0)
        print(f"  1ra escritura: {n_new} nuevos, {n_upd} updates (esperado 3, 0)")

        # 2da: actualiza USB del 30/4 + agrega fila del 02/05
        rows2 = [
            {"Fecha": "2026-04-30", "Moneda": "USB",   # update
             "Rate": 1444.0, "Cotiza vs": "ARS", "Fuente": "dolarapi mid (corregido)"},
            {"Fecha": "2026-05-02", "Moneda": "USB",   # nueva
             "Rate": 1450.0, "Cotiza vs": "ARS", "Fuente": "dolarapi mid"},
        ]
        n_new, n_upd = fx.upsert_csv(
            path, rows2,
            key_cols=["Fecha", "Moneda"],
            column_order=["Fecha", "Moneda", "Rate", "Cotiza vs", "Fuente"],
        )
        assert (n_new, n_upd) == (1, 1)
        print(f"  2da escritura: {n_new} nuevos, {n_upd} updates (esperado 1, 1)")

        df = pd.read_csv(path)
        assert len(df) == 4, f"esperaba 4 filas, got {len(df)}"
        usb_apr30 = df[(df["Fecha"] == "2026-04-30") & (df["Moneda"] == "USB")]
        assert abs(usb_apr30["Rate"].iloc[0] - 1444.0) < 1e-9
        assert "corregido" in usb_apr30["Fuente"].iloc[0]
        print(f"  USB 30/4 → rate={usb_apr30['Rate'].iloc[0]} (corregido)")
        print(f"  Filas finales: {len(df)} (esperado 4)")
        print("  PASS")


def test_8_dolarapi_casa_ausente():
    """Si dolarapi no devuelve alguna casa target, no rompe (skip + warn)."""
    print("\nTest 8 (dolarapi sin alguna casa):")
    response_sin_mayorista = [d for d in DOLARAPI_RESPONSE if d["casa"] != "mayorista"]
    with patch.object(fx, "_get_json", return_value=response_sin_mayorista):
        rows = fx.fetch_dolarapi_today()
    assert len(rows) == 2  # solo bolsa y CCL
    monedas = {r.moneda for r in rows}
    assert monedas == {"USB", "USD"}
    print(f"  Sin mayorista: {len(rows)} filas, monedas={monedas}")
    print("  PASS")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_1_mid,
        test_2_parse_iso_date,
        test_3_fetch_dolarapi_today,
        test_4_fetch_argentinadatos_filtros,
        test_5_crosscheck_mayorista_ok,
        test_6_crosscheck_bcra_none,
        test_7_upsert_csv,
        test_8_dolarapi_casa_ausente,
    ]
    for t in tests:
        t()
    print("\n" + "=" * 50)
    print("Todos los tests pasaron.")
