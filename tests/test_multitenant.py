# -*- coding: utf-8 -*-
"""
test_multitenant.py — Tests del sistema multi-tenant.

Cubre:
  - Resolución de user desde token (single y multi)
  - Aislamiento de datos entre users
  - Admin: crear / listar / borrar / switch
  - Bloqueo de mutations cuando admin está switched
  - Back-compat con WM_API_TOKEN single-tenant
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _setup_multitenant(tmp: Path):
    """Setup multi-tenant con admin + amigo."""
    os.environ["WM_BASE_DIR"] = str(tmp)
    os.environ["WM_USERS_JSON"] = json.dumps({
        "rodricor": {"token": "admin-tok", "is_admin": True, "display_name": "Admin"},
        "amigo": {"token": "amigo-tok", "display_name": "Amigo"},
    })
    os.environ["WM_ADMIN_USER"] = "rodricor"
    os.environ.pop("WM_API_TOKEN", None)
    from api.state import reset_settings
    reset_settings()
    from api.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _setup_singletenant(tmp: Path):
    """Setup back-compat single-tenant."""
    os.environ["WM_BASE_DIR"] = str(tmp)
    os.environ.pop("WM_USERS_JSON", None)
    os.environ.pop("WM_ADMIN_USER", None)
    os.environ["WM_API_TOKEN"] = "legacy-tok"
    from api.state import reset_settings
    reset_settings()
    from api.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_1_backcompat_single_tenant():
    print("\n[MT 1] back-compat single-tenant con WM_API_TOKEN:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _setup_singletenant(Path(tmp))
        r = client.get("/api/health")
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["multi_tenant"] is False
        assert body["n_users"] == 1
        assert body["xlsx_present"] is False
        assert body["auth_configured"] is True
        print(f"  ✓ Single-tenant detectado, n_users=1")
        # Token funciona
        r = client.get("/api/config", headers=auth("legacy-tok"))
        assert r.status_code == 200
        cfg = r.get_json()
        assert cfg["user_id"] == "default"
        assert cfg["is_admin"] is True
        print(f"  ✓ Token legacy funciona, user_id=default, is_admin=True")


def test_2_multitenant_resolution():
    print("\n[MT 2] resolución de user en multi-tenant:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _setup_multitenant(Path(tmp))
        # Token admin
        r = client.get("/api/config", headers=auth("admin-tok"))
        cfg = r.get_json()
        assert cfg["user_id"] == "rodricor"
        assert cfg["is_admin"] is True
        # Token amigo
        r = client.get("/api/config", headers=auth("amigo-tok"))
        cfg = r.get_json()
        assert cfg["user_id"] == "amigo"
        assert cfg["is_admin"] is False
        # Token invalido
        r = client.get("/api/config", headers=auth("bogus"))
        assert r.status_code == 401
        print("  ✓ admin → rodricor, amigo → amigo, bogus → 401")


def test_3_data_isolation():
    print("\n[MT 3] cada user tiene paths separados:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _setup_multitenant(Path(tmp))
        # admin config
        r = client.get("/api/config", headers=auth("admin-tok"))
        admin_xlsx = r.get_json()["xlsx_path"]
        # amigo config
        r = client.get("/api/config", headers=auth("amigo-tok"))
        amigo_xlsx = r.get_json()["xlsx_path"]
        assert admin_xlsx != amigo_xlsx
        assert "rodricor" in admin_xlsx
        assert "amigo" in amigo_xlsx
        print(f"  ✓ admin: {admin_xlsx}")
        print(f"  ✓ amigo: {amigo_xlsx}")


def test_4_admin_only_endpoints():
    print("\n[MT 4] endpoints admin solo accesibles por admin:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _setup_multitenant(Path(tmp))
        # admin: 200
        r = client.get("/api/admin/users", headers=auth("admin-tok"))
        assert r.status_code == 200
        # amigo: 403
        r = client.get("/api/admin/users", headers=auth("amigo-tok"))
        assert r.status_code == 403
        # sin token: 401
        r = client.get("/api/admin/users")
        assert r.status_code == 401
        print(f"  ✓ admin 200, amigo 403, sin token 401")


def test_5_admin_create_user():
    print("\n[MT 5] admin crea user → folder + master en disk:")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = _setup_multitenant(tmp)
        # Crear user 'tester'
        r = client.post("/api/admin/users", headers=auth("admin-tok"),
                         json={"user_id": "tester", "display_name": "T"})
        assert r.status_code == 201
        body = r.get_json()
        assert body["user_id"] == "tester"
        assert len(body["token"]) >= 32
        # Folder creado
        master_path = tmp / "inputs" / "tester" / "wealth_management.xlsx"
        assert master_path.is_file()
        # User aparece en list
        r = client.get("/api/admin/users", headers=auth("admin-tok"))
        users = r.get_json()["users"]
        ids = [u["user_id"] for u in users]
        assert "tester" in ids
        # No se puede crear duplicado
        r = client.post("/api/admin/users", headers=auth("admin-tok"),
                         json={"user_id": "tester"})
        assert r.status_code == 400
        print(f"  ✓ Tester creado con master en {master_path.name}, duplicado rechazado")


def test_6_admin_switch_user():
    print("\n[MT 6] admin switch a otro user (read-only):")
    with tempfile.TemporaryDirectory() as tmp:
        client = _setup_multitenant(Path(tmp))
        # Switch
        r = client.post("/api/admin/switch", headers=auth("admin-tok"),
                         json={"user_id": "amigo"})
        assert r.status_code == 200
        assert r.get_json()["switched"] is True
        # /api/config ahora muestra amigo como active pero auth_user = rodricor
        r = client.get("/api/config", headers=auth("admin-tok"))
        cfg = r.get_json()
        assert cfg["user_id"] == "amigo"        # active
        assert cfg["auth_user_id"] == "rodricor"  # real owner
        assert cfg["is_switched"] is True
        # Mutations bloqueadas
        r = client.post("/api/sheets/blotter", headers=auth("admin-tok"),
                         json={"Trade ID": "X", "Cuenta": "x", "Ticker": "x",
                                "Side": "BUY", "Qty": 1, "Precio": 1,
                                "Moneda Trade": "ARS", "Trade Date": "2026-01-01",
                                "Cuenta Cash": "x"})
        assert r.status_code == 403
        # Volver del switch
        r = client.post("/api/admin/switch", headers=auth("admin-tok"),
                         json={"user_id": None})
        assert r.status_code == 200
        # Ahora active vuelve a ser admin
        r = client.get("/api/config", headers=auth("admin-tok"))
        cfg = r.get_json()
        assert cfg["user_id"] == "rodricor"
        assert cfg["is_switched"] is False
        print(f"  ✓ Switch + block mutation + unswitch OK")


def test_7_amigo_cannot_switch():
    print("\n[MT 7] amigo no puede usar /admin/switch:")
    with tempfile.TemporaryDirectory() as tmp:
        client = _setup_multitenant(Path(tmp))
        r = client.post("/api/admin/switch", headers=auth("amigo-tok"),
                         json={"user_id": "rodricor"})
        assert r.status_code == 403
        print(f"  ✓ amigo blocked (403)")


def test_8_delete_user():
    print("\n[MT 8] admin borra user (config solo, data preservada):")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = _setup_multitenant(tmp)
        # Crear y luego borrar
        r = client.post("/api/admin/users", headers=auth("admin-tok"),
                         json={"user_id": "victim"})
        assert r.status_code == 201
        master_path = tmp / "inputs" / "victim" / "wealth_management.xlsx"
        assert master_path.is_file()
        # Delete sin delete_data → archivos quedan
        r = client.delete("/api/admin/users/victim", headers=auth("admin-tok"))
        assert r.status_code == 200
        assert master_path.is_file()  # NO se borró
        # admin no se puede auto-borrar
        r = client.delete("/api/admin/users/rodricor", headers=auth("admin-tok"))
        assert r.status_code == 400
        print(f"  ✓ Delete preservó data; admin no puede auto-borrar")


def test_9_tickers_union():
    print("\n[MT 9] tickers_union escanea inputs/<user>/master:")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = _setup_multitenant(tmp)
        # Crear 2 users con sus masters
        client.post("/api/admin/users", headers=auth("admin-tok"),
                     json={"user_id": "u1"})
        client.post("/api/admin/users", headers=auth("admin-tok"),
                     json={"user_id": "u2"})
        # Verificar que los masters existen
        assert (tmp / "inputs" / "u1" / "wealth_management.xlsx").is_file()
        assert (tmp / "inputs" / "u2" / "wealth_management.xlsx").is_file()
        # Cambiar HERE temporalmente para que tickers_union lea de tmp
        import tickers_union as tu
        original_inputs = tu.INPUTS_DIR
        tu.INPUTS_DIR = tmp / "inputs"
        try:
            masters = tu.find_master_files()
            assert len(masters) >= 2
            by_class = tu.union_tickers()
            # Cada master tiene 12 especies de ejemplo, así que >= 12 tickers
            total = sum(len(s) for s in by_class.values())
            assert total >= 10
            print(f"  ✓ Encontró {len(masters)} masters, {total} tickers en union")
        finally:
            tu.INPUTS_DIR = original_inputs


def test_10_admin_can_still_mutate_own_data():
    print("\n[MT 10] admin no-switched puede mutar SUS datos:"  )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = _setup_multitenant(tmp)
        # Crear master para admin
        from build_master import build_master
        admin_xlsx = tmp / "inputs" / "rodricor" / "wealth_management.xlsx"
        admin_xlsx.parent.mkdir(parents=True, exist_ok=True)
        build_master(admin_xlsx)
        # Refresh del admin
        r = client.post("/api/refresh", headers=auth("admin-tok"))
        assert r.status_code == 200, r.data
        # Crear un trade — debería funcionar
        r = client.post("/api/sheets/blotter", headers=auth("admin-tok"),
                         json={"Trade ID": "ADMIN_T", "Trade Date": "2026-05-01",
                                "Cuenta": "cocos", "Cuenta Cash": "cocos",
                                "Ticker": "TXMJ9", "Side": "BUY", "Qty": 1000,
                                "Precio": 0.85, "Moneda Trade": "ARS"})
        assert r.status_code == 201
        print(f"  ✓ Admin mutation OK cuando no está switched")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_1_backcompat_single_tenant,
        test_2_multitenant_resolution,
        test_3_data_isolation,
        test_4_admin_only_endpoints,
        test_5_admin_create_user,
        test_6_admin_switch_user,
        test_7_amigo_cannot_switch,
        test_8_delete_user,
        test_9_tickers_union,
        test_10_admin_can_still_mutate_own_data,
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
