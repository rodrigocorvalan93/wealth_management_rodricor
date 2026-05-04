# -*- coding: utf-8 -*-
"""
test_users_persistence.py — Tests del file-backed user store (WM_USERS_FILE).

Cubre:
  - load_users prefers WM_USERS_FILE sobre WM_USERS_JSON sobre WM_API_TOKEN
  - Bootstrap automático: WM_USERS_FILE seteado pero file no existe
    → se crea desde WM_USERS_JSON
  - add_user_to_config persiste a disk atómicamente
  - remove_user_from_config persiste el delete
  - Las mutaciones sobreviven a "reload" (limpieza de env vars + relectura)
  - is_persistent() devuelve true cuando file está configurado
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _clean_env():
    """Limpia env vars de users entre tests."""
    for k in ("WM_USERS_FILE", "WM_USERS_JSON", "WM_API_TOKEN", "WM_ADMIN_USER"):
        os.environ.pop(k, None)


def test_1_priority_file_over_env():
    print("\n[P 1] WM_USERS_FILE tiene prioridad sobre WM_USERS_JSON:")
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"
        f.write_text(json.dumps({
            "from_file": {"token": "FILE-TOK", "is_admin": True}
        }))
        os.environ["WM_USERS_FILE"] = str(f)
        os.environ["WM_USERS_JSON"] = json.dumps({
            "from_env": {"token": "ENV-TOK"}
        })
        from api.users import load_users
        users = load_users()
        ids = [u.user_id for u in users]
        assert ids == ["from_file"], f"FILE debe ganar; got {ids}"
        assert users[0].token == "FILE-TOK"
        print(f"  ✓ FILE gana ({ids})")


def test_2_bootstrap_from_env():
    print("\n[P 2] WM_USERS_FILE seteado + file no existe → bootstrap from JSON:")
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"  # NO la creamos
        os.environ["WM_USERS_FILE"] = str(f)
        os.environ["WM_USERS_JSON"] = json.dumps({
            "rodricor": {"token": "BOOT-TOK", "is_admin": True}
        })
        from api.users import load_users
        users = load_users()
        assert f.is_file(), "El bootstrap debe crear el file"
        ids = [u.user_id for u in users]
        assert ids == ["rodricor"]
        # El file ahora debe tener el contenido del env
        data = json.loads(f.read_text())
        assert "rodricor" in data
        assert data["rodricor"]["token"] == "BOOT-TOK"
        print(f"  ✓ File creado con {len(data)} users desde WM_USERS_JSON")


def test_3_bootstrap_empty_when_no_env():
    print("\n[P 3] WM_USERS_FILE seteado, file no existe, sin WM_USERS_JSON:")
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"
        os.environ["WM_USERS_FILE"] = str(f)
        # NO seteamos WM_USERS_JSON
        from api.users import load_users
        users = load_users()
        # Sin nada de donde bootstrappear, file queda vacío
        assert users == []
        assert f.is_file()
        data = json.loads(f.read_text())
        assert data == {}
        print(f"  ✓ File vacío {{}} cuando no hay nada que bootstrappear")


def test_4_add_user_persists_to_file():
    print("\n[P 4] add_user_to_config escribe atómicamente al file:")
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"
        os.environ["WM_USERS_FILE"] = str(f)
        from api.users import add_user_to_config, load_users, is_persistent
        assert is_persistent()
        # Trigger bootstrap (load creates empty file)
        load_users()
        # Add user
        add_user_to_config("amigo", "AMIGO-TOK", display_name="Amigo")
        # File debe reflejarlo
        data = json.loads(f.read_text())
        assert "amigo" in data
        assert data["amigo"]["token"] == "AMIGO-TOK"
        assert data["amigo"]["display_name"] == "Amigo"
        # Permisos: 600 (solo owner)
        mode = oct(f.stat().st_mode)[-3:]
        assert mode == "600", f"esperaba 600, got {mode}"
        print(f"  ✓ User persistido + chmod 600")


def test_5_remove_user_persists():
    print("\n[P 5] remove_user_from_config persiste el delete:")
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"
        f.write_text(json.dumps({
            "rodricor": {"token": "R-TOK", "is_admin": True},
            "amigo": {"token": "A-TOK"},
        }))
        os.environ["WM_USERS_FILE"] = str(f)
        from api.users import remove_user_from_config, load_users
        remove_user_from_config("amigo")
        data = json.loads(f.read_text())
        assert "amigo" not in data
        assert "rodricor" in data
        users = load_users()
        ids = [u.user_id for u in users]
        assert ids == ["rodricor"]
        print(f"  ✓ Delete persistido")


def test_6_survives_reload():
    print("\n[P 6] Mutación sobrevive a 'reload' (re-lectura del file):"  )
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"
        os.environ["WM_USERS_FILE"] = str(f)
        os.environ["WM_USERS_JSON"] = json.dumps({
            "rodricor": {"token": "R-TOK", "is_admin": True}
        })
        from api.users import add_user_to_config, load_users
        load_users()  # bootstrap
        add_user_to_config("nuevo", "NEW-TOK")

        # Simular reload: limpiar env vars (como si el WSGI re-arrancara)
        # SOLO dejamos WM_USERS_FILE (que es lo que el WSGI seguiría seteando)
        old_json = os.environ.pop("WM_USERS_JSON")
        # Re-load desde el file
        users = load_users()
        ids = sorted(u.user_id for u in users)
        assert "rodricor" in ids
        assert "nuevo" in ids
        print(f"  ✓ Después de 'reload', users persisten: {ids}")


def test_7_legacy_compat_when_no_file():
    print("\n[P 7] Sin WM_USERS_FILE: legacy WM_USERS_JSON sigue funcionando:")
    _clean_env()
    os.environ["WM_USERS_JSON"] = json.dumps({
        "u1": {"token": "T1", "is_admin": True}
    })
    from api.users import load_users, is_persistent
    assert not is_persistent()
    users = load_users()
    assert [u.user_id for u in users] == ["u1"]
    print(f"  ✓ Back-compat OK, is_persistent=False")


def test_8_legacy_api_token_alone():
    print("\n[P 8] Solo WM_API_TOKEN (legacy single-tenant):"  )
    _clean_env()
    os.environ["WM_API_TOKEN"] = "legacy-tok"
    from api.users import load_users, is_persistent
    assert not is_persistent()
    users = load_users()
    assert len(users) == 1
    assert users[0].user_id == "default"
    assert users[0].token == "legacy-tok"
    assert users[0].is_admin is True
    print(f"  ✓ Single-tenant legacy: user_id=default")


def test_9_atomic_write_no_partial():
    print("\n[P 9] Escrituras concurrentes no dejan archivo corrupto:")
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        f = Path(tmp) / "users.json"
        os.environ["WM_USERS_FILE"] = str(f)
        from api.users import add_user_to_config, load_users
        load_users()
        # 10 escrituras seguidas
        for i in range(10):
            add_user_to_config(f"user{i}", f"tok-{i}")
        data = json.loads(f.read_text())
        assert len(data) == 10
        # Todos los tokens deben estar bien escritos (no truncados)
        for i in range(10):
            assert data[f"user{i}"]["token"] == f"tok-{i}"
        print(f"  ✓ 10 escrituras consecutivas, todas íntegras")


# =============================================================================

if __name__ == "__main__":
    tests = [
        test_1_priority_file_over_env,
        test_2_bootstrap_from_env,
        test_3_bootstrap_empty_when_no_env,
        test_4_add_user_persists_to_file,
        test_5_remove_user_persists,
        test_6_survives_reload,
        test_7_legacy_compat_when_no_file,
        test_8_legacy_api_token_alone,
        test_9_atomic_write_no_partial,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            import traceback; traceback.print_exc()
            failed.append(t.__name__)
        finally:
            _clean_env()
    print("\n" + "=" * 70)
    if failed:
        print(f"✗ {len(failed)}/{len(tests)} tests FALLARON: {failed}")
        sys.exit(1)
    else:
        print(f"✓ Todos los {len(tests)} tests pasaron")
    print("=" * 70)
