"""Tests offline (sin OMS) de la lógica de FX y upsert."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from historico_byma_loader import (
    TickerSnapshot, FXResult,
    compute_fx, upsert_csv, infer_moneda_nativa,
    FX_USB_PAIRS, FX_USD_PAIRS,
)

# ============================================================
# TEST 1: FX con los 3 pares y volúmenes razonables
# ============================================================
def test_fx_3_pares_con_volumen():
    f = date(2026, 4, 30)
    # Asumamos AL30 cotiza 75500 ARS, AL30D cotiza 65 USB → MEP implícito ≈ 1161.5
    # GD30 cotiza 78000 ARS, GD30D cotiza 67 USB → 1164.2
    # GD35 cotiza 80000 ARS, GD35D cotiza 69 USB → 1159.4
    snaps = {
        "AL30":  TickerSnapshot("AL30",  f, 75500.0, "CL", 50_000_000),
        "AL30D": TickerSnapshot("AL30D", f, 65.0,    "CL", 30_000_000),
        "GD30":  TickerSnapshot("GD30",  f, 78000.0, "CL", 40_000_000),
        "GD30D": TickerSnapshot("GD30D", f, 67.0,    "CL", 25_000_000),
        "GD35":  TickerSnapshot("GD35",  f, 80000.0, "CL", 10_000_000),
        "GD35D": TickerSnapshot("GD35D", f, 69.0,    "CL",  5_000_000),
    }
    result = compute_fx(snaps, FX_USB_PAIRS, "USB", f)
    print(f"Test 1 (3 pares con volumen):")
    print(f"  Rate: {result.rate:.4f}")
    print(f"  n_pares: {result.n_pares}")
    print(f"  modo: {result.pesos_modo}")
    print(f"  detalle: {result.detalle}")
    assert result.is_valid
    assert result.n_pares == 3
    assert result.pesos_modo == "volumen"
    # Pares con más volumen pesan más → rate cerca de los pares líquidos
    # AL30/AL30D y GD30/GD30D tienen ~10x el volumen de GD35/GD35D
    # Sus implícitos: 75500/65=1161.5, 78000/67=1164.2 → ~1162.8
    # GD35: 80000/69=1159.4 — pesa poco
    assert 1160 < result.rate < 1165, f"rate={result.rate} fuera de rango esperado"
    print("  PASS\n")


# ============================================================
# TEST 2: FX con solo 1 par disponible
# ============================================================
def test_fx_1_par():
    f = date(2026, 4, 30)
    snaps = {
        "AL30":  TickerSnapshot("AL30",  f, 75500.0, "CL", 50_000_000),
        "AL30D": TickerSnapshot("AL30D", f, 65.0,    "CL", 30_000_000),
        # GD30 y GD35 no tienen datos
    }
    result = compute_fx(snaps, FX_USB_PAIRS, "USB", f)
    print(f"Test 2 (1 par disponible):")
    print(f"  Rate: {result.rate:.4f}")
    print(f"  n_pares: {result.n_pares}")
    assert result.is_valid
    assert result.n_pares == 1
    assert abs(result.rate - 75500.0/65.0) < 0.01
    print("  PASS\n")


# ============================================================
# TEST 3: FX sin volumen en ningún par → promedio simple
# ============================================================
def test_fx_sin_volumen():
    f = date(2026, 4, 30)
    nan = float("nan")
    snaps = {
        "AL30":  TickerSnapshot("AL30",  f, 75500.0, "CL", nan),
        "AL30D": TickerSnapshot("AL30D", f, 65.0,    "CL", nan),
        "GD30":  TickerSnapshot("GD30",  f, 78000.0, "CL", nan),
        "GD30D": TickerSnapshot("GD30D", f, 67.0,    "CL", nan),
    }
    result = compute_fx(snaps, FX_USB_PAIRS, "USB", f)
    print(f"Test 3 (sin volumen):")
    print(f"  Rate: {result.rate:.4f}")
    print(f"  modo: {result.pesos_modo}")
    assert result.is_valid
    assert result.pesos_modo == "simple"
    expected = (75500.0/65.0 + 78000.0/67.0) / 2
    assert abs(result.rate - expected) < 0.01
    print("  PASS\n")


# ============================================================
# TEST 4: FX sin pares disponibles → no_valid
# ============================================================
def test_fx_sin_pares():
    f = date(2026, 4, 30)
    result = compute_fx({}, FX_USB_PAIRS, "USB", f)
    print(f"Test 4 (snapshots vacíos):")
    print(f"  is_valid: {result.is_valid}")
    print(f"  n_pares: {result.n_pares}")
    assert not result.is_valid
    assert result.n_pares == 0
    print("  PASS\n")


# ============================================================
# TEST 5: Inferencia de moneda nativa
# ============================================================
def test_moneda_nativa():
    print("Test 5 (moneda nativa):")
    cases = [
        ("AL30D", "USB"),
        ("GD30D", "USB"),
        ("BPC7D", "USB"),
        ("AL30C", "USD"),
        ("GD30C", "USD"),
        ("AL30",  "ARS"),
        ("GD30",  "ARS"),
        ("TX26",  "ARS"),
        ("TXMJ9", "ARS"),  # no termina ni en C ni en D
        ("S31L6", "ARS"),  # LECAP
        ("ggal",  "ARS"),  # acción
    ]
    for ticker, expected in cases:
        actual = infer_moneda_nativa(ticker)
        status = "OK" if actual == expected else "FAIL"
        print(f"  {ticker:8s} → {actual:4s} (esperado {expected}) [{status}]")
        assert actual == expected, f"{ticker}: {actual} != {expected}"
    print("  PASS\n")


# ============================================================
# TEST 6: Upsert CSV — anexar y actualizar
# ============================================================
def test_upsert():
    import tempfile
    print("Test 6 (upsert CSV):")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "precios.csv"
        cols = ["Fecha", "Ticker", "Precio", "Moneda", "Fuente"]

        # Primera escritura: 2 filas
        rows1 = [
            {"Fecha": "2026-04-30", "Ticker": "AL30D", "Precio": 65.0, "Moneda": "USB", "Fuente": "BYMA CL 24hs"},
            {"Fecha": "2026-04-30", "Ticker": "GD30C", "Precio": 67.0, "Moneda": "USD", "Fuente": "BYMA CL 24hs"},
        ]
        n_new, n_upd = upsert_csv(path, rows1, ["Fecha", "Ticker"], cols)
        print(f"  Primera escritura: {n_new} nuevos, {n_upd} updates (esperado 2, 0)")
        assert n_new == 2 and n_upd == 0

        # Segunda escritura: 1 update + 1 fila nueva fecha distinta
        rows2 = [
            {"Fecha": "2026-04-30", "Ticker": "AL30D", "Precio": 65.5, "Moneda": "USB", "Fuente": "BYMA CL 24hs (corregido)"},
            {"Fecha": "2026-05-01", "Ticker": "AL30D", "Precio": 66.0, "Moneda": "USB", "Fuente": "BYMA CL 24hs"},
        ]
        n_new, n_upd = upsert_csv(path, rows2, ["Fecha", "Ticker"], cols)
        print(f"  Segunda escritura: {n_new} nuevos, {n_upd} updates (esperado 1, 1)")
        assert n_new == 1 and n_upd == 1

        # Verificar contenido final: 3 filas únicas
        import pandas as pd
        df = pd.read_csv(path)
        print(f"  Filas finales: {len(df)} (esperado 3)")
        assert len(df) == 3
        # Verificar que el AL30D del 30/4 quedó actualizado
        al30d_30 = df[(df["Fecha"] == "2026-04-30") & (df["Ticker"] == "AL30D")].iloc[0]
        print(f"  AL30D 30/4 → precio={al30d_30['Precio']}, fuente={al30d_30['Fuente']}")
        assert al30d_30["Precio"] == 65.5
        assert "corregido" in al30d_30["Fuente"]
    print("  PASS\n")


if __name__ == "__main__":
    test_fx_3_pares_con_volumen()
    test_fx_1_par()
    test_fx_sin_volumen()
    test_fx_sin_pares()
    test_moneda_nativa()
    test_upsert()
    print("=" * 50)
    print("Todos los tests pasaron.")
