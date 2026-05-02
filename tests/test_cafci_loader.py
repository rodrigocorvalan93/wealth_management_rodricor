# -*- coding: utf-8 -*-
"""
test_cafci_loader.py

Tests offline para cafci_loader.py. No tocan la API real, mockean los
responses para validar la lógica de parseo, lookup, normalización, y CSV.

USO:
    python3 test_cafci_loader.py

Salida esperada: "Todos los tests pasaron." y exit 0.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

# Permitir import del loader desde la misma carpeta
sys.path.insert(0, str(Path(__file__).parent))

# Setear un token dummy ANTES de importar el loader (por si chequea)
os.environ.setdefault("CAFCI_TOKEN", "Bearer dummy_test_token")

import cafci_loader as cl  # noqa: E402


# =============================================================================
# Tests
# =============================================================================

def test_1_vcp_normalizada():
    """vcp_normalizada divide por 1000 y maneja inválidos."""
    print("\nTest 1 (vcp_normalizada):")
    cases = [
        (1234567.0, 1234.567),       # número
        ("1500000", 1500.0),          # string numérico
        (None, float("nan")),         # None
        ("xx", float("nan")),         # no numérico
        (0, 0.0),                     # cero
    ]
    import math
    for raw, expected in cases:
        got = cl.vcp_normalizada(raw)
        if math.isnan(expected):
            assert math.isnan(got), f"  raw={raw!r}: esperaba NaN, got {got}"
        else:
            assert abs(got - expected) < 1e-9, f"  raw={raw!r}: esperaba {expected}, got {got}"
        print(f"  {raw!r:<15} → {got}")
    print("  PASS")


def test_2_parse_fcis_file_valido():
    """parse_fcis_file lee correctamente formato TICKER|NOMBRE."""
    print("\nTest 2 (parse_fcis_file válido):")
    content = """\
# comentario
FIMA_RFD_C|Fima Renta Fija Dólares - Clase C
DELTA_ACC_A|Delta Acciones - Clase A

# linea vacía arriba
DELTA_RR_A|Delta Retorno Real - Clase A
"""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        fcis = cl.parse_fcis_file(path)
        assert len(fcis) == 3, f"Esperaba 3 FCIs, got {len(fcis)}"
        assert fcis[0].ticker == "FIMA_RFD_C"
        assert fcis[0].nombre_cafci == "Fima Renta Fija Dólares - Clase C"
        assert fcis[2].ticker == "DELTA_RR_A"
        for f in fcis:
            print(f"  {f.ticker:<15} → {f.nombre_cafci}")
        print("  PASS")
    finally:
        path.unlink()


def test_3_parse_fcis_file_malformado():
    """parse_fcis_file tira error claro si falta el separador |."""
    print("\nTest 3 (parse_fcis_file línea malformada):")
    content = """FIMA_OK|Fima Premium - Clase A
LINEA_SIN_PIPE_INVALIDA
"""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        try:
            cl.parse_fcis_file(path)
            assert False, "Esperaba ValueError"
        except ValueError as e:
            msg = str(e)
            assert "|" in msg or "malformada" in msg, f"Mensaje no informativo: {msg}"
            print(f"  Error capturado correctamente:")
            print(f"    {msg.splitlines()[0]}")
            print("  PASS")
    finally:
        path.unlink()


def test_4_lookup_fcis_in_report():
    """lookup_fcis_in_report encuentra fondos que existen y reporta los que no."""
    print("\nTest 4 (lookup_fcis_in_report):")
    # Mock del report (lo que devuelve get_daily_report tras json_normalize)
    df_report = pd.DataFrame([
        {
            "nombreDeLaClaseDeFondo": "Delta Ahorro Plus - Clase A",
            "vcp": 1234567.0,
            "fecha": "2026-05-02",
            "moneda": "ARS",
        },
        {
            "nombreDeLaClaseDeFondo": "Fima Premium Dólares - Clase A",
            "vcp": 1500000.0,
            "fecha": "2026-05-02",
            "moneda": "USD",
        },
        {
            "nombreDeLaClaseDeFondo": "Otro Fondo Que No Trackeamos",
            "vcp": 999999.0,
            "fecha": "2026-05-02",
            "moneda": "ARS",
        },
    ])

    fcis = [
        cl.FCIMapping(ticker="DELTA_AHORRO_A",
                      nombre_cafci="Delta Ahorro Plus - Clase A"),
        cl.FCIMapping(ticker="FIMA_PREM_A",
                      nombre_cafci="Fima Premium Dólares - Clase A"),
        cl.FCIMapping(ticker="GHOST_FCI",
                      nombre_cafci="FCI Que No Existe"),
    ]

    snapshots, not_found = cl.lookup_fcis_in_report(df_report, fcis)

    assert len(snapshots) == 2, f"Esperaba 2 snapshots, got {len(snapshots)}"
    assert len(not_found) == 1, f"Esperaba 1 no encontrado, got {len(not_found)}"
    assert "GHOST_FCI" in not_found
    assert snapshots[0].ticker == "DELTA_AHORRO_A"
    assert abs(snapshots[0].vcp - 1234.567) < 1e-9, \
        f"VCP normalizada incorrecta: {snapshots[0].vcp}"
    assert snapshots[0].moneda == "ARS"
    assert snapshots[1].moneda == "USD"
    assert snapshots[1].vcp == 1500.0

    for s in snapshots:
        print(f"  {s.ticker:<15} VCP={s.vcp:>10.4f} {s.moneda} ({s.nombre_cafci})")
    print(f"  Sin datos: {not_found}")
    print("  PASS")


def test_5_lookup_columnas_faltantes():
    """lookup_fcis_in_report tira error si el report no tiene las columnas esperadas."""
    print("\nTest 5 (lookup con columnas faltantes):")
    df_report = pd.DataFrame([{"nombreDeLaClaseDeFondo": "X", "otra_col": 1}])
    fcis = [cl.FCIMapping(ticker="X", nombre_cafci="X")]
    try:
        cl.lookup_fcis_in_report(df_report, fcis)
        assert False, "Esperaba RuntimeError"
    except RuntimeError as e:
        msg = str(e)
        assert "vcp" in msg.lower() or "fecha" in msg.lower() or "moneda" in msg.lower(), \
            f"Mensaje poco informativo: {msg}"
        print(f"  Error capturado: {msg.splitlines()[0]}")
        print("  PASS")


def test_6_upsert_csv():
    """upsert_csv crea, anexa y actualiza correctamente."""
    print("\nTest 6 (upsert CSV):")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "precios_historico.csv"

        # 1ra escritura: 2 filas nuevas
        rows1 = [
            {"Fecha": "2026-04-30", "Ticker": "DELTA_ACC_A",
             "Precio": 1000.5, "Moneda": "ARS", "Fuente": "CAFCI daily VCP"},
            {"Fecha": "2026-04-30", "Ticker": "FIMA_PREM_A",
             "Precio": 1500.0, "Moneda": "USD", "Fuente": "CAFCI daily VCP"},
        ]
        n_new, n_upd = cl.upsert_csv(
            path, rows1,
            key_cols=["Fecha", "Ticker"],
            column_order=["Fecha", "Ticker", "Precio", "Moneda", "Fuente"],
        )
        assert (n_new, n_upd) == (2, 0), f"1ra: esperaba (2, 0), got ({n_new}, {n_upd})"
        print(f"  1ra escritura: {n_new} nuevos, {n_upd} updates (esperado 2, 0)")

        # 2da escritura: 1 actualización (mismo Fecha+Ticker, distinto precio) + 1 nueva
        rows2 = [
            {"Fecha": "2026-04-30", "Ticker": "DELTA_ACC_A",  # actualiza
             "Precio": 1010.5, "Moneda": "ARS", "Fuente": "CAFCI daily VCP (corregido)"},
            {"Fecha": "2026-05-02", "Ticker": "DELTA_ACC_A",  # nueva
             "Precio": 1015.0, "Moneda": "ARS", "Fuente": "CAFCI daily VCP"},
        ]
        n_new, n_upd = cl.upsert_csv(
            path, rows2,
            key_cols=["Fecha", "Ticker"],
            column_order=["Fecha", "Ticker", "Precio", "Moneda", "Fuente"],
        )
        assert (n_new, n_upd) == (1, 1), f"2da: esperaba (1, 1), got ({n_new}, {n_upd})"
        print(f"  2da escritura: {n_new} nuevos, {n_upd} updates (esperado 1, 1)")

        # Verificar contenido final
        df = pd.read_csv(path)
        assert len(df) == 3, f"Esperaba 3 filas, got {len(df)}"
        delta_apr30 = df[(df["Fecha"] == "2026-04-30") & (df["Ticker"] == "DELTA_ACC_A")]
        assert len(delta_apr30) == 1
        assert abs(delta_apr30["Precio"].iloc[0] - 1010.5) < 1e-9, "VCP no se actualizó"
        assert "corregido" in delta_apr30["Fuente"].iloc[0]
        print(f"  DELTA_ACC_A 30/4 → precio={delta_apr30['Precio'].iloc[0]} (corregido)")
        print(f"  Filas finales: {len(df)} (esperado 3)")
        print("  PASS")


def test_7_fcis_file_default_existe():
    """El archivo fcis_cafci.txt provisto se parsea sin errores."""
    print("\nTest 7 (fcis_cafci.txt default es válido):")
    path = Path(__file__).parent / "fcis_cafci.txt"
    if not path.is_file():
        print(f"  SKIP — no existe {path}")
        return
    fcis = cl.parse_fcis_file(path)
    assert len(fcis) > 0, "fcis_cafci.txt vacío"
    print(f"  {len(fcis)} FCIs parseados correctamente")
    for fci in fcis[:3]:
        print(f"    {fci.ticker:<18} → {fci.nombre_cafci}")
    if len(fcis) > 3:
        print(f"    ... y {len(fcis)-3} más")
    print("  PASS")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_1_vcp_normalizada,
        test_2_parse_fcis_file_valido,
        test_3_parse_fcis_file_malformado,
        test_4_lookup_fcis_in_report,
        test_5_lookup_columnas_faltantes,
        test_6_upsert_csv,
        test_7_fcis_file_default_existe,
    ]
    for t in tests:
        t()
    print("\n" + "=" * 50)
    print("Todos los tests pasaron.")
