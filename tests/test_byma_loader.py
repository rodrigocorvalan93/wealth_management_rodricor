# -*- coding: utf-8 -*-
"""
test_byma_loader.py

Tests offline para byma_loader.py. No tocan la API real, mockean responses.

USO:
    python3 test_byma_loader.py

Salida esperada: "Todos los tests pasaron." y exit 0.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

# Setear creds dummy ANTES de importar
os.environ.setdefault("OMS_USER", "test")
os.environ.setdefault("OMS_PASS", "test")

import byma_loader as bl  # noqa: E402


# =============================================================================
# Tests
# =============================================================================

def test_1_extract_price():
    """_extract_price extrae precio de los varios formatos posibles."""
    print("\nTest 1 (_extract_price):")
    cases = [
        ([{"price": 100.5, "size": 10}], 100.5),       # lista de dicts
        ({"price": 200.0}, 200.0),                      # dict suelto
        ([{"px": 50.0}], 50.0),                          # alt key 'px'
        (75.0, 75.0),                                    # número directo
        (None, float("nan")),                            # None
        ([], float("nan")),                              # lista vacía
        ([{"size": 10}], float("nan")),                  # falta price
    ]
    for inp, expected in cases:
        got = bl._extract_price(inp)
        if math.isnan(expected):
            assert math.isnan(got), f"  inp={inp}: esperaba NaN, got {got}"
        else:
            assert abs(got - expected) < 1e-9, f"  inp={inp}: esperaba {expected}, got {got}"
        print(f"  {str(inp)[:40]:<42} → {got}")
    print("  PASS")


def test_2_parse_snapshot_prioridad_la():
    """parse_snapshot prioriza LA → CL → ACP."""
    print("\nTest 2 (parse_snapshot prioridad LA → CL → ACP):")
    fecha = date(2026, 5, 2)

    # Caso A: los 3 disponibles → toma LA (el más fresco)
    md = {
        "LA": [{"price": 100.0, "size": 10}],
        "CL": [{"price": 99.5, "size": 5}],
        "ACP": [{"price": 99.8, "size": 2}],
        "TV": [{"size": 1000}],
    }
    snap = bl.parse_snapshot(md, "AL30", fecha)
    assert snap.precio == 100.0, f"A: esperaba 100.0 (LA), got {snap.precio}"
    assert snap.fuente == "LA"
    print(f"  A: LA+CL+ACP → toma LA={snap.precio} ✓")

    # Caso B: sin LA, hay CL → toma CL
    md_b = {
        "LA": None,
        "CL": [{"price": 99.5}],
        "ACP": [{"price": 99.8}],
    }
    snap_b = bl.parse_snapshot(md_b, "AL30", fecha)
    assert snap_b.precio == 99.5
    assert snap_b.fuente == "CL"
    print(f"  B: sin LA → toma CL={snap_b.precio} ✓")

    # Caso C: solo ACP → toma ACP
    md_c = {"LA": None, "CL": None, "ACP": [{"price": 99.8}]}
    snap_c = bl.parse_snapshot(md_c, "AL30", fecha)
    assert snap_c.precio == 99.8
    assert snap_c.fuente == "ACP"
    print(f"  C: solo ACP → toma ACP={snap_c.precio} ✓")

    # Caso D: nada → NaN, no válido
    md_d = {"LA": None, "CL": None, "ACP": None}
    snap_d = bl.parse_snapshot(md_d, "AL30", fecha)
    assert math.isnan(snap_d.precio)
    assert snap_d.fuente == ""
    assert not snap_d.is_valid
    print(f"  D: nada → NaN, is_valid={snap_d.is_valid} ✓")
    print("  PASS")


def test_3_infer_moneda_nativa():
    """infer_moneda_nativa aplica la convención BYMA por sufijo."""
    print("\nTest 3 (infer_moneda_nativa):")
    cases = [
        ("AL30D", "USB"), ("GD30D", "USB"), ("BPC7D", "USB"),
        ("AL30C", "USD"), ("GD30C", "USD"),
        ("AL30", "ARS"), ("GD30", "ARS"), ("TX26", "ARS"),
        ("TXMJ9", "ARS"), ("S31L6", "ARS"), ("ggal", "ARS"),
    ]
    for ticker, expected in cases:
        got = bl.infer_moneda_nativa(ticker)
        assert got == expected, f"  {ticker} → {got}, esperaba {expected}"
        print(f"  {ticker:<8} → {got:<4} (esperado {expected}) [OK]")
    print("  PASS")


def test_4_symbol_format():
    """_symbol arma el string 'MERV - XMEV - X - 24hs'."""
    print("\nTest 4 (_symbol):")
    cases = [
        ("AL30",  "MERV - XMEV - AL30 - 24hs"),
        ("GD30D", "MERV - XMEV - GD30D - 24hs"),
        ("TX26",  "MERV - XMEV - TX26 - 24hs"),
    ]
    for ticker, expected in cases:
        got = bl._symbol(ticker)
        assert got == expected, f"  {ticker}: got {got!r}"
        print(f"  {ticker:<8} → {got!r}")
    print("  PASS")


def test_5_upsert_csv():
    """upsert_csv crea, anexa y actualiza correctamente."""
    print("\nTest 5 (upsert CSV):")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "precios_historico.csv"

        # 1ra escritura: 2 nuevos
        rows1 = [
            {"Fecha": "2026-04-30", "Ticker": "AL30D",
             "Precio": 65.5, "Moneda": "USB", "Fuente": "BYMA LA 24hs"},
            {"Fecha": "2026-04-30", "Ticker": "GD30D",
             "Precio": 70.0, "Moneda": "USB", "Fuente": "BYMA LA 24hs"},
        ]
        n_new, n_upd = bl.upsert_csv(
            path, rows1,
            key_cols=["Fecha", "Ticker"],
            column_order=["Fecha", "Ticker", "Precio", "Moneda", "Fuente"],
        )
        assert (n_new, n_upd) == (2, 0)
        print(f"  1ra escritura: {n_new} nuevos, {n_upd} updates (esperado 2, 0)")

        # 2da: 1 update + 1 nuevo
        rows2 = [
            {"Fecha": "2026-04-30", "Ticker": "AL30D",   # update
             "Precio": 65.7, "Moneda": "USB", "Fuente": "BYMA CL 24hs (corregido)"},
            {"Fecha": "2026-05-02", "Ticker": "AL30D",   # nuevo
             "Precio": 66.0, "Moneda": "USB", "Fuente": "BYMA LA 24hs"},
        ]
        n_new, n_upd = bl.upsert_csv(
            path, rows2,
            key_cols=["Fecha", "Ticker"],
            column_order=["Fecha", "Ticker", "Precio", "Moneda", "Fuente"],
        )
        assert (n_new, n_upd) == (1, 1)
        print(f"  2da escritura: {n_new} nuevos, {n_upd} updates (esperado 1, 1)")

        df = pd.read_csv(path)
        assert len(df) == 3
        al30_apr30 = df[(df["Fecha"] == "2026-04-30") & (df["Ticker"] == "AL30D")]
        assert abs(al30_apr30["Precio"].iloc[0] - 65.7) < 1e-9
        assert "corregido" in al30_apr30["Fuente"].iloc[0]
        print(f"  AL30D 30/4 → precio={al30_apr30['Precio'].iloc[0]} (corregido)")
        print(f"  Filas finales: {len(df)} (esperado 3)")
        print("  PASS")


def test_6_parse_tickers_file():
    """parse_tickers_file lee tickers ignorando comentarios y vacíos."""
    print("\nTest 6 (parse_tickers_file):")
    content = """\
# tickers de prueba
AL30D
GD30C   # comentario inline ignorado por split

TX26
"""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        tickers = bl.parse_tickers_file(path)
        assert tickers == ["AL30D", "GD30C", "TX26"], f"got {tickers}"
        print(f"  {tickers}")
        print("  PASS")
    finally:
        path.unlink()


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_1_extract_price,
        test_2_parse_snapshot_prioridad_la,
        test_3_infer_moneda_nativa,
        test_4_symbol_format,
        test_5_upsert_csv,
        test_6_parse_tickers_file,
    ]
    for t in tests:
        t()
    print("\n" + "=" * 50)
    print("Todos los tests pasaron.")
