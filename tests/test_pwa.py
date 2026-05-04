# -*- coding: utf-8 -*-
"""
test_pwa.py — Tests del PWA y métricas avanzadas de equity curve.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_master import build_master
from engine.snapshots import calculate_returns


TOKEN = "test-token-pwa"


def _setup_env(tmp: Path):
    xlsx = tmp / "wm.xlsx"
    db = tmp / "wealth.db"
    data = tmp / "data"
    data.mkdir(exist_ok=True)
    backups = data / "excel_backups"
    backups.mkdir(exist_ok=True)
    build_master(xlsx)

    os.environ["WM_API_TOKEN"] = TOKEN
    os.environ["WM_BASE_DIR"] = str(tmp)
    os.environ["WM_XLSX_PATH"] = str(xlsx)
    os.environ["WM_DB_PATH"] = str(db)
    os.environ["WM_DATA_DIR"] = str(data)
    os.environ["WM_BACKUPS_DIR"] = str(backups)
    os.environ["WM_ANCHOR"] = "ARS"

    from api.state import reset_settings
    reset_settings()
    from api.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app, xlsx


def _client(tmp):
    app, xlsx = _setup_env(tmp)
    from api.state import reimport_excel
    reimport_excel(date(2026, 5, 2))
    return app.test_client()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


# =============================================================================
# PWA endpoints
# =============================================================================

def test_1_pwa_root_serves_html():
    print("\n[PWA 1] GET / sirve el HTML shell:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        r = client.get("/")
        assert r.status_code == 200
        assert b"<title>" in r.data
        assert b'rel="manifest"' in r.data
        assert b"/static/app.js" in r.data
        assert b"apple-mobile-web-app" in r.data
        print(f"  ✓ HTML shell OK ({len(r.data):,} bytes)")


def test_2_pwa_static_assets():
    print("\n[PWA 2] static assets servidos:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        for path, mime in [
            ("/static/manifest.json", "application/json"),
            ("/static/style.css", "text/css"),
            ("/static/app.js", ""),
            ("/static/icon.svg", "image/svg+xml"),
        ]:
            r = client.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"
            assert len(r.data) > 100
            print(f"  ✓ {path} ({len(r.data):,} bytes)")


def test_3_service_worker_scope_header():
    print("\n[PWA 3] /sw.js sirve con Service-Worker-Allowed:"  )
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        r = client.get("/sw.js")
        assert r.status_code == 200
        assert r.headers.get("Service-Worker-Allowed") == "/"
        assert b"caches" in r.data
        print(f"  ✓ /sw.js scope='/' OK")


def test_4_manifest_valid_json():
    print("\n[PWA 4] manifest.json es JSON válido y tiene campos PWA:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        r = client.get("/static/manifest.json")
        m = json.loads(r.data)
        for key in ["name", "short_name", "start_url", "display", "icons"]:
            assert key in m, f"manifest.json falta '{key}'"
        assert m["display"] in ("standalone", "fullscreen")
        assert len(m["icons"]) > 0
        print(f"  ✓ manifest.json válido: {m['name']}")


def test_5_pwa_unauth_dashboard_endpoints_blocked():
    print("\n[PWA 5] endpoints API requieren auth desde la PWA:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        # /api/summary sin token
        r = client.get("/api/summary")
        assert r.status_code == 401
        # /api/sheets/blotter sin token
        r = client.get("/api/sheets/blotter")
        assert r.status_code == 401
        # Pero /api/health sí responde
        r = client.get("/api/health")
        assert r.status_code == 200
        print(f"  ✓ Auth correctamente requerida en endpoints sensibles")


# =============================================================================
# Métricas avanzadas (Sharpe, Sortino, Calmar, Vol)
# =============================================================================

def test_6_returns_basic_curve():
    print("\n[METRICS 1] curve básica → métricas básicas:"  )
    curve = [
        {"fecha": "2026-01-01", "mv_anchor": 1000.0},
        {"fecha": "2026-04-01", "mv_anchor": 1100.0},
        {"fecha": "2026-12-31", "mv_anchor": 1200.0},
    ]
    m = calculate_returns(curve)
    assert m["first_value"] == 1000
    assert m["last_value"] == 1200
    assert abs(m["total_return_pct"] - 0.20) < 1e-6
    assert m["max_drawdown_pct"] == 0  # Curva monótona creciente
    print(f"  ✓ Retorno total: {m['total_return_pct']*100:.2f}%, DD: {m['max_drawdown_pct']*100:.2f}%")


def test_7_returns_with_drawdown():
    print("\n[METRICS 2] curve con drawdown:"  )
    curve = [
        {"fecha": "2026-01-01", "mv_anchor": 1000.0},
        {"fecha": "2026-02-01", "mv_anchor": 1100.0},
        {"fecha": "2026-03-01", "mv_anchor": 950.0},   # DD desde 1100
        {"fecha": "2026-04-01", "mv_anchor": 1050.0},
        {"fecha": "2026-05-01", "mv_anchor": 1200.0},
    ]
    m = calculate_returns(curve)
    # DD = (950 - 1100) / 1100 = -13.6%
    expected_dd = (950 - 1100) / 1100
    assert abs(m["max_drawdown_pct"] - expected_dd) < 1e-6
    print(f"  ✓ Max DD: {m['max_drawdown_pct']*100:.2f}% (esperado {expected_dd*100:.2f}%)")


def test_8_returns_advanced_metrics():
    print("\n[METRICS 3] curve daily → Sharpe / Sortino / Calmar:"  )
    # Curve simulada: 252 días con retornos ~normales ish
    import random
    random.seed(42)
    curve = []
    val = 1000.0
    from datetime import date as _date, timedelta
    d = _date(2026, 1, 1)
    for i in range(252):
        curve.append({"fecha": (d + timedelta(days=i)).isoformat(),
                      "mv_anchor": val})
        # Random walk con leve drift positivo
        ret = random.gauss(0.0005, 0.012)  # 0.05% mean, 1.2% std daily
        val *= (1 + ret)
    m = calculate_returns(curve, risk_free_rate=0.0)
    print(f"  Total return: {m['total_return_pct']*100:.2f}%")
    print(f"  Vol anual:    {m['volatility_annual']*100:.2f}%")
    print(f"  Sharpe:       {m['sharpe_ratio']:.2f}")
    print(f"  Sortino:      {m['sortino_ratio']:.2f}")
    print(f"  Calmar:       {m['calmar_ratio']:.2f}")
    print(f"  Max DD:       {m['max_drawdown_pct']*100:.2f}%")
    # Sanity: con drift positivo small, Sharpe > 0
    assert m["sharpe_ratio"] is not None
    assert m["volatility_annual"] > 0
    assert m["max_drawdown_pct"] < 0  # Hubo algún drawdown
    print(f"  ✓ Métricas calculables sobre serie de 252 puntos")


def test_9_returns_empty_curve():
    print("\n[METRICS 4] curve vacía → defaults sin crashear:"  )
    m = calculate_returns([])
    assert m["first_value"] == 0
    assert m["sharpe_ratio"] is None
    assert m["calmar_ratio"] is None
    print(f"  ✓ curve vacía no crashea")

    m = calculate_returns([{"fecha": "2026-01-01", "mv_anchor": 1000}])
    assert m["first_value"] == 1000
    assert m["last_value"] == 1000
    print(f"  ✓ curve de 1 punto no crashea")


# =============================================================================
# Equity curve endpoint con métricas
# =============================================================================

def test_master_especies_crud():
    """CRUD de especies vía /api/sheets/especies con Ticker como natural key."""
    print("\n[MASTER 1] CRUD de especies:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))

        # LIST: ya hay ejemplos del build_master
        r = client.get("/api/sheets/especies", headers=_auth())
        assert r.status_code == 200
        body = r.get_json()
        n_initial = body["n"]
        # Cada item debe tener row_id (natural key = Ticker)
        for item in body["items"]:
            assert "row_id" in item
            assert item["row_id"] == item["Ticker"]
        print(f"  ✓ LIST: {n_initial} especies, todas con row_id=Ticker")

        # CREATE
        r = client.post("/api/sheets/especies", headers=_auth(), json={
            "Ticker": "TEST_API",
            "Name": "Test ticker creado vía API",
            "Asset Class": "EQUITY_AR",
            "Currency": "ARS",
        })
        assert r.status_code == 201, r.data
        assert r.get_json()["row_id"] == "TEST_API"
        print(f"  ✓ POST: TEST_API creado")

        # GET specific
        r = client.get("/api/sheets/especies/TEST_API", headers=_auth())
        assert r.status_code == 200
        body = r.get_json()
        assert body["Ticker"] == "TEST_API"
        assert body["Asset Class"] == "EQUITY_AR"
        print(f"  ✓ GET: encontrado")

        # CREATE duplicado → 400
        r = client.post("/api/sheets/especies", headers=_auth(), json={
            "Ticker": "TEST_API",
            "Name": "duplicado",
            "Asset Class": "EQUITY_AR",
            "Currency": "ARS",
        })
        assert r.status_code == 400, f"esperaba 400, got {r.status_code}"
        print(f"  ✓ POST duplicado → 400")

        # UPDATE
        r = client.put("/api/sheets/especies/TEST_API", headers=_auth(), json={
            "Name": "Test ticker EDITADO",
        })
        assert r.status_code == 200, r.data
        body = r.get_json()
        assert body["row"]["Name"] == "Test ticker EDITADO"
        # Ticker (natural key) no cambia aunque venga en body
        assert body["row"]["Ticker"] == "TEST_API"
        print(f"  ✓ PUT: name actualizado")

        # DELETE (hard delete)
        r = client.delete("/api/sheets/especies/TEST_API", headers=_auth())
        assert r.status_code == 200
        print(f"  ✓ DELETE: borrada")

        # LIST: vuelta a n_initial
        r = client.get("/api/sheets/especies", headers=_auth())
        assert r.get_json()["n"] == n_initial
        print(f"  ✓ LIST después: {r.get_json()['n']} especies (back to {n_initial})")


def test_master_cuentas_crud():
    """CRUD de cuentas con Code como natural key + flag Investible."""
    print("\n[MASTER 2] CRUD de cuentas:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        # CREATE
        r = client.post("/api/sheets/cuentas", headers=_auth(), json={
            "Code": "test_account",
            "Name": "Cuenta de prueba",
            "Kind": "CASH_BANK",
            "Currency": "USD",
            "Investible": "NO",
            "Cash Purpose": "RESERVA_NO_DECLARADO",
        })
        assert r.status_code == 201, r.data
        body = r.get_json()
        assert body["row_id"] == "test_account"
        # El re-import debe haber leído la nueva cuenta (Investible=0)
        import sqlite3
        from api.state import db_conn
        conn = db_conn()
        cur = conn.execute("SELECT investible, cash_purpose FROM accounts WHERE code='test_account'")
        row = cur.fetchone()
        assert row is not None
        assert row["investible"] == 0
        assert row["cash_purpose"] == "RESERVA_NO_DECLARADO"
        conn.close()
        print(f"  ✓ Cuenta agregada y reimport OK con investible=0")

        # DELETE
        r = client.delete("/api/sheets/cuentas/test_account", headers=_auth())
        assert r.status_code == 200
        # Verificar que ya no existe
        conn = db_conn()
        cur = conn.execute("SELECT * FROM accounts WHERE code='test_account'")
        assert cur.fetchone() is None
        conn.close()
        print(f"  ✓ DELETE OK, ya no existe en DB")


def test_master_monedas_crud():
    """CRUD de monedas con Code como key."""
    print("\n[MASTER 3] CRUD de monedas:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        r = client.post("/api/sheets/monedas", headers=_auth(), json={
            "Code": "TEST_CCY",
            "Name": "Moneda de prueba",
            "Quote vs": "USD",
            "Is Stable": 1,
            "Is Base": 0,
        })
        assert r.status_code == 201
        # GET
        r = client.get("/api/sheets/monedas/TEST_CCY", headers=_auth())
        body = r.get_json()
        assert body["Code"] == "TEST_CCY"
        # DELETE
        r = client.delete("/api/sheets/monedas/TEST_CCY", headers=_auth())
        assert r.status_code == 200
        print(f"  ✓ CRUD monedas OK")


def test_prices_endpoint():
    """/api/prices devuelve la última cotización por ticker."""
    print("\n[QUOTES 1] /api/prices:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        # Sin precios cargados (no corrieron loaders en test) → devuelve lista vacía
        r = client.get("/api/prices", headers=_auth())
        assert r.status_code == 200
        body = r.get_json()
        assert "items" in body
        print(f"  ✓ /api/prices: {body['n']} tickers")

        # Insertar precios manualmente y verificar
        from api.state import db_conn
        conn = db_conn()
        conn.execute("INSERT INTO prices (fecha, ticker, price, currency, source) VALUES (?, ?, ?, ?, ?)",
                     ("2026-05-01", "TEST_T", 100.0, "USD", "manual"))
        conn.execute("INSERT INTO prices (fecha, ticker, price, currency, source) VALUES (?, ?, ?, ?, ?)",
                     ("2026-05-02", "TEST_T", 105.0, "USD", "manual"))  # más reciente
        conn.commit()
        conn.close()

        r = client.get("/api/prices?ticker=TEST_T", headers=_auth())
        body = r.get_json()
        assert body["n"] == 1
        assert body["items"][0]["price"] == 105.0
        assert body["items"][0]["fecha"] == "2026-05-02"
        print(f"  ✓ Latest price: 105.0 (no 100.0)")


def test_fx_rates_endpoint():
    """/api/fx-rates devuelve la última FX por (moneda, base)."""
    print("\n[QUOTES 2] /api/fx-rates:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        from api.state import db_conn
        conn = db_conn()
        conn.execute("INSERT INTO fx_rates (fecha, moneda, rate, base, source) VALUES (?, ?, ?, ?, ?)",
                     ("2026-05-01", "USD", 1380.0, "ARS", "test"))
        conn.execute("INSERT INTO fx_rates (fecha, moneda, rate, base, source) VALUES (?, ?, ?, ?, ?)",
                     ("2026-05-02", "USD", 1400.0, "ARS", "test"))
        conn.commit()
        conn.close()

        r = client.get("/api/fx-rates", headers=_auth())
        body = r.get_json()
        usd_rates = [x for x in body["items"] if x["moneda"] == "USD"]
        assert len(usd_rates) == 1
        assert usd_rates[0]["rate"] == 1400.0
        assert usd_rates[0]["fecha"] == "2026-05-02"
        print(f"  ✓ Latest FX USD/ARS: 1400 (no 1380)")


def test_cash_endpoint():
    """/api/cash devuelve saldos cash por cuenta + subtotales por moneda."""
    print("\n[CASH] /api/cash:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        r = client.get("/api/cash?anchor=ARS", headers=_auth())
        assert r.status_code == 200
        body = r.get_json()
        assert "items" in body
        assert "by_currency" in body
        assert "total_anchor" in body
        # cocos debe estar (tiene cash ARS por las trades)
        cocos = [c for c in body["items"] if c["account"] == "cocos"]
        assert cocos
        print(f"  ✓ {body['n']} cuentas con cash, total: {body['total_anchor']:,.0f} ARS")
        print(f"  ✓ Por moneda: {list(body['by_currency'].keys())}")


def test_10_equity_curve_endpoint_includes_metrics():
    print("\n[PWA 6] /api/equity-curve incluye Sharpe/Calmar/etc:"  )
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(Path(tmp))
        # Generar varios snapshots simulados
        from api.state import db_conn
        from engine.schema import insert_pn_snapshot
        from engine.snapshots import TOTAL_KEY
        conn = db_conn()
        from datetime import date as _date, timedelta
        d = _date(2026, 1, 1)
        for i, v in enumerate([1000, 1050, 980, 1020, 1100, 1150, 1080, 1200]):
            insert_pn_snapshot(conn, (d + timedelta(days=i*7)).isoformat(),
                                TOTAL_KEY, "ARS", v)
        conn.commit()
        conn.close()

        r = client.get("/api/equity-curve?anchor=ARS", headers=_auth())
        assert r.status_code == 200
        body = r.get_json()
        assert "metrics" in body
        m = body["metrics"]
        # Métricas extendidas presentes
        assert "sharpe_ratio" in m
        assert "sortino_ratio" in m
        assert "calmar_ratio" in m
        assert "volatility_annual" in m
        print(f"  ✓ Sharpe={m['sharpe_ratio']}, Calmar={m['calmar_ratio']}, Vol={m['volatility_annual']}")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_1_pwa_root_serves_html,
        test_2_pwa_static_assets,
        test_3_service_worker_scope_header,
        test_4_manifest_valid_json,
        test_5_pwa_unauth_dashboard_endpoints_blocked,
        test_6_returns_basic_curve,
        test_7_returns_with_drawdown,
        test_8_returns_advanced_metrics,
        test_9_returns_empty_curve,
        test_10_equity_curve_endpoint_includes_metrics,
        test_master_especies_crud,
        test_master_cuentas_crud,
        test_master_monedas_crud,
        test_prices_endpoint,
        test_fx_rates_endpoint,
        test_cash_endpoint,
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
