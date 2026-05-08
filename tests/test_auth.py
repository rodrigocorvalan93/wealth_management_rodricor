# -*- coding: utf-8 -*-
"""
tests/test_auth.py

Tests del flow de auth con email + password (api/auth.py + endpoints).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import pytest


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Cliente Flask con BASE_DIR aislado y SMTP deshabilitado."""
    monkeypatch.setenv("WM_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("WM_SMTP_DISABLED", "1")
    monkeypatch.setenv("WM_DISABLE_RATELIMIT", "1")
    monkeypatch.setenv("WM_BOOTSTRAP_SUPERADMIN_EMAIL", "rodri@test.com")
    monkeypatch.setenv("WM_AUTO_VERIFY_FIRST_SUPERADMIN", "1")
    # Sin WM_API_TOKEN ni WM_USERS_JSON — solo auth_db
    monkeypatch.delenv("WM_API_TOKEN", raising=False)
    monkeypatch.delenv("WM_USERS_JSON", raising=False)
    monkeypatch.delenv("WM_USERS_FILE", raising=False)

    # Reset state caches
    from api import state as s
    s.reset_settings()

    from api.app import create_app
    app = create_app()
    return app.test_client()


def _extract_token_from_outbox(kind: str) -> str | None:
    """Lee los .eml del outbox y devuelve el último token de tipo `kind`
    (e.g. 'verify-email', 'reset-password'). Decodifica QP correctamente."""
    import email as _email
    import re
    base = os.environ.get("WM_BASE_DIR", ".")
    outbox = os.path.join(base, "data", "_outbox")
    if not os.path.isdir(outbox):
        return None
    pattern = re.compile(rf"{re.escape(kind)}\?token=([A-Za-z0-9_-]+)")
    # Iterar de más nuevo a más viejo
    for fname in sorted(os.listdir(outbox), reverse=True):
        with open(os.path.join(outbox, fname), "rb") as f:
            msg = _email.message_from_binary_file(f)
        # Recorrer todas las parts y decodear correctamente
        for part in msg.walk():
            if part.is_multipart():
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            try:
                text = payload.decode(part.get_content_charset() or "utf-8",
                                       errors="replace")
            except Exception:
                continue
            m = pattern.search(text)
            if m:
                return m.group(1)
    return None


def _signup(client, email, password, **kw):
    return client.post("/api/auth/signup",
                        json={"email": email, "password": password, **kw})


def _login(client, email, password):
    return client.post("/api/auth/login",
                        json={"email": email, "password": password})


def test_signup_then_login(app_client):
    r = _signup(app_client, "alice@test.com", "pass1234")
    assert r.status_code == 201, r.json
    assert r.json["email"] == "alice@test.com"
    assert r.json["is_superadmin"] is False  # no es bootstrap email

    r = _login(app_client, "alice@test.com", "pass1234")
    assert r.status_code == 200
    assert "session_token" in r.json
    assert r.json["user"]["email"] == "alice@test.com"


def test_signup_invalid_email(app_client):
    r = _signup(app_client, "no-arroba", "pass1234")
    assert r.status_code == 400
    assert r.json["code"] == "INVALID_EMAIL"


def test_signup_weak_password(app_client):
    r = _signup(app_client, "x@y.com", "abcde")
    assert r.status_code == 400
    assert r.json["code"] == "WEAK_PASSWORD"


def test_signup_duplicate_email(app_client):
    _signup(app_client, "x@y.com", "pass1234")
    r = _signup(app_client, "x@y.com", "pass5678")
    assert r.status_code == 409
    assert r.json["code"] == "EMAIL_TAKEN"


def test_login_wrong_password(app_client):
    _signup(app_client, "x@y.com", "pass1234")
    r = _login(app_client, "x@y.com", "wrongpass")
    assert r.status_code == 401
    assert r.json["code"] == "INVALID_CREDS"


def test_login_unknown_email(app_client):
    r = _login(app_client, "nadie@example.com", "pass1234")
    # Mismo error que password mala — no revelar enumeration
    assert r.status_code == 401
    assert r.json["code"] == "INVALID_CREDS"


def test_lockout_after_5_failed(app_client):
    _signup(app_client, "x@y.com", "pass1234")
    for _ in range(5):
        r = _login(app_client, "x@y.com", "wrongpass")
        assert r.status_code == 401
    # 6th attempt — locked
    r = _login(app_client, "x@y.com", "wrongpass")
    assert r.status_code == 423
    assert r.json["code"] == "LOCKED"


def test_session_resolves_for_protected_endpoints(app_client):
    _signup(app_client, "alice@test.com", "pass1234")
    login = _login(app_client, "alice@test.com", "pass1234")
    token = login.json["session_token"]

    # Endpoint protegido (auth/me) debería responder 200
    r = app_client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json["user"]["email"] == "alice@test.com"
    assert r.json["auth_via"] == "session"


def test_logout_invalidates_session(app_client):
    _signup(app_client, "alice@test.com", "pass1234")
    login = _login(app_client, "alice@test.com", "pass1234")
    token = login.json["session_token"]

    r = app_client.post("/api/auth/logout",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json["ok"] is True

    # Token ya no vale
    r = app_client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_password_reset_flow(app_client):
    _signup(app_client, "alice@test.com", "pass1234")

    # Pedir reset
    r = app_client.post("/api/auth/forgot-password",
                         json={"email": "alice@test.com"})
    assert r.status_code == 200

    reset_token = _extract_token_from_outbox("reset-password")
    assert reset_token, "No encontré reset token en el outbox"

    # Reset con token
    r = app_client.post("/api/auth/reset-password",
                         json={"token": reset_token,
                                "new_password": "newpass99"})
    assert r.status_code == 200, r.json

    # Login con password nueva
    r = _login(app_client, "alice@test.com", "newpass99")
    assert r.status_code == 200
    # Login con la vieja falla
    r = _login(app_client, "alice@test.com", "pass1234")
    assert r.status_code == 401


def test_forgot_unknown_email_returns_ok(app_client):
    """Anti-enumeration: forgot-password con email inexistente devuelve 200."""
    r = app_client.post("/api/auth/forgot-password",
                         json={"email": "nadie@ejemplo.com"})
    assert r.status_code == 200
    assert r.json["ok"] is True


def test_bootstrap_superadmin(app_client):
    # rodri@test.com es WM_BOOTSTRAP_SUPERADMIN_EMAIL en el fixture
    r = _signup(app_client, "rodri@test.com", "pass1234")
    assert r.status_code == 201
    assert r.json["is_superadmin"] is True
    assert r.json["is_admin"] is True
    # Auto-verified porque WM_AUTO_VERIFY_FIRST_SUPERADMIN=1
    assert r.json["needs_verification"] is False

    # Segundo signup NO debería ser super
    r = _signup(app_client, "amigo@test.com", "pass1234")
    assert r.status_code == 201
    assert r.json["is_superadmin"] is False


def test_superadmin_can_list_users(app_client):
    _signup(app_client, "rodri@test.com", "pass1234")
    _signup(app_client, "amigo@test.com", "pass1234")
    login = _login(app_client, "rodri@test.com", "pass1234")
    token = login.json["session_token"]

    r = app_client.get("/api/superadmin/users",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert len(r.json["users"]) == 2


def test_non_superadmin_cannot_list_users(app_client):
    _signup(app_client, "rodri@test.com", "pass1234")  # super
    _signup(app_client, "amigo@test.com", "pass1234")  # plain
    login = _login(app_client, "amigo@test.com", "pass1234")
    token = login.json["session_token"]
    r = app_client.get("/api/superadmin/users",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_change_password_invalidates_other_sessions(app_client):
    _signup(app_client, "alice@test.com", "pass1234")
    # Dos logins → dos sesiones
    s1 = _login(app_client, "alice@test.com", "pass1234").json["session_token"]
    s2 = _login(app_client, "alice@test.com", "pass1234").json["session_token"]

    # Cambiar password con s1; s2 debe invalidarse
    r = app_client.post("/api/auth/change-password",
                         json={"current_password": "pass1234",
                                "new_password": "newpass99"},
                         headers={"Authorization": f"Bearer {s1}"})
    assert r.status_code == 200

    # s1 sigue valiendo
    r = app_client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {s1}"})
    assert r.status_code == 200
    # s2 ya no
    r = app_client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {s2}"})
    assert r.status_code == 401


def test_email_verify_flow(app_client, monkeypatch):
    # Sin auto-verify para forzar el flujo de verify
    monkeypatch.setenv("WM_AUTO_VERIFY_FIRST_SUPERADMIN", "0")
    monkeypatch.setenv("WM_BOOTSTRAP_SUPERADMIN_EMAIL", "")

    r = _signup(app_client, "alice@test.com", "pass1234")
    assert r.status_code == 201
    assert r.json.get("verify_token_dev") is None  # solo super-bootstrap lo expone

    verify_token = _extract_token_from_outbox("verify-email")
    assert verify_token, "No encontré verify token"

    # Verificar
    r = app_client.post("/api/auth/verify-email",
                         json={"token": verify_token})
    assert r.status_code == 200, r.json

    # Login muestra email_verified=True
    login = _login(app_client, "alice@test.com", "pass1234")
    assert login.json["user"]["email_verified"] is True
