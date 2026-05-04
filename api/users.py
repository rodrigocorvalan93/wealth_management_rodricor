# -*- coding: utf-8 -*-
"""
api/users.py

Resolución de usuarios y permisos para la app multi-tenant.

Modelo:
- Cada user tiene: user_id (handle), token (bearer), display_name, is_admin

Persistencia (3 niveles, en orden de prioridad):

  1. WM_USERS_FILE: path a un JSON file en disk. RECOMENDADO. Las escrituras
     desde admin endpoints (crear/borrar user) se persisten automáticamente
     y la próxima request las ve. NO requiere reload del WSGI.

  2. WM_USERS_JSON: env var con JSON inline. Compat / bootstrap. Si está
     pero el FILE no existe, se "promociona" al file en el primer
     add_user_to_config (transparente).

  3. WM_API_TOKEN: legacy single-tenant. Si solo está esto, asume
     user_id="default" y is_admin=true.

Switch user (admin only):
- Un admin puede pedir POST /api/admin/switch con un target_user_id.
- Mientras esté switched, sus GETs (read-only) ven datos del target.
- Las mutations las bloqueamos: el admin NO puede modificar datos de otro user.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class UserConfig:
    user_id: str
    token: str
    display_name: str = ""
    is_admin: bool = False

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.user_id


# Cache en memoria del switch state {admin_token: target_user_id}
# Se pierde al reload del web app, lo cual es deseable (es vista temporal).
_switched: dict[str, str] = {}


# =============================================================================
# Persistencia: WM_USERS_FILE (JSON en disk)
# =============================================================================

def _users_file_path() -> Optional[Path]:
    """Devuelve el path al users.json si WM_USERS_FILE está seteado."""
    p = os.environ.get("WM_USERS_FILE", "").strip()
    return Path(p) if p else None


def _read_users_file() -> Optional[dict]:
    """Lee users.json. Devuelve None si no está seteado o no existe.
    Devuelve {} si está seteado pero corrupto/vacío."""
    p = _users_file_path()
    if p is None:
        return None
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[users] WARN no pude leer {p}: {e}")
        return {}


def _write_users_file(data: dict) -> bool:
    """Escribe users.json atómicamente (temp file + rename).
    Devuelve True si OK, False si WM_USERS_FILE no está seteado."""
    p = _users_file_path()
    if p is None:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tempfile en el mismo dir + rename
    fd, tmp_path = tempfile.mkstemp(prefix=".users-", suffix=".json.tmp",
                                     dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, p)  # atómico en POSIX
        try:
            os.chmod(p, 0o600)  # solo el owner puede leer (tokens son secretos)
        except OSError:
            pass
        return True
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise


def _bootstrap_file_from_env() -> Optional[dict]:
    """Si WM_USERS_FILE está seteado pero no existe, lo crea desde
    WM_USERS_JSON. Devuelve el dict bootstrappeado, o None si no aplica."""
    p = _users_file_path()
    if p is None or p.is_file():
        return None
    raw = os.environ.get("WM_USERS_JSON", "")
    if not raw:
        # No hay nada que bootstrappear; crear vacío.
        _write_users_file({})
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except json.JSONDecodeError:
        data = {}
    _write_users_file(data)
    print(f"[users] Bootstrapped {p} desde WM_USERS_JSON ({len(data)} users)")
    return data


# =============================================================================
# Loaders
# =============================================================================

def _build_user_configs(data: dict) -> list[UserConfig]:
    """Convierte un dict {user_id: token|spec} a list[UserConfig]."""
    users = []
    admin_user = os.environ.get("WM_ADMIN_USER", "").strip()
    if not isinstance(data, dict):
        return users
    for user_id, value in data.items():
        user_id = str(user_id).strip()
        if not user_id:
            continue
        # Acepta dos formatos:
        #   {"rodricor": "token..."}                                  (corto)
        #   {"rodricor": {"token":"...", "is_admin":true, ...}}       (completo)
        if isinstance(value, str):
            users.append(UserConfig(
                user_id=user_id, token=value,
                is_admin=(user_id == admin_user) if admin_user else False,
            ))
        elif isinstance(value, dict):
            token = value.get("token", "")
            if not token:
                continue
            users.append(UserConfig(
                user_id=user_id,
                token=token,
                display_name=value.get("display_name") or user_id,
                is_admin=bool(value.get("is_admin", False))
                          or (user_id == admin_user if admin_user else False),
            ))
    return users


def load_users() -> list[UserConfig]:
    """Devuelve la lista actual de users.

    Prioridad:
      1. WM_USERS_FILE (disk JSON) — fuente de verdad si existe
      2. WM_USERS_JSON (env var) — fallback / bootstrap
      3. WM_API_TOKEN — legacy single-tenant
    """
    # 1. File first
    file_data = _read_users_file()
    if file_data is None and _users_file_path() is not None:
        # WM_USERS_FILE seteado pero file no existe: bootstrap from env
        file_data = _bootstrap_file_from_env()
    if file_data:
        users = _build_user_configs(file_data)
        if users:
            return users

    # 2. WM_USERS_JSON env var
    raw = os.environ.get("WM_USERS_JSON")
    if raw:
        try:
            users = _build_user_configs(json.loads(raw))
            if users:
                return users
        except json.JSONDecodeError as e:
            print(f"[users] WM_USERS_JSON inválido: {e}")

    # 3. Legacy single-tenant
    legacy_token = os.environ.get("WM_API_TOKEN", "").strip()
    if legacy_token:
        return [UserConfig(
            user_id="default",
            token=legacy_token,
            display_name="Default user",
            is_admin=True,
        )]
    return []


def resolve_user_by_token(token: str) -> Optional[UserConfig]:
    """Devuelve UserConfig si algún user tiene ese token, None si no."""
    if not token:
        return None
    for u in load_users():
        if u.token == token:
            return u
    return None


def get_active_user(token: str) -> Optional[tuple[UserConfig, bool]]:
    """Resuelve el user "activo" para un request.

    Si el token es de un admin que hizo switch a otro user, devuelve
    (target_user, is_switched=True). Si no, (auth_user, False).

    Devuelve None si el token no matchea ningún user.
    """
    auth_user = resolve_user_by_token(token)
    if auth_user is None:
        return None
    if auth_user.is_admin and token in _switched:
        target_id = _switched[token]
        for u in load_users():
            if u.user_id == target_id:
                return (u, True)
    return (auth_user, False)


def admin_switch_to(admin_token: str, target_user_id: str) -> None:
    """Switch del admin a ver datos de target_user. Lanza si no es admin."""
    user = resolve_user_by_token(admin_token)
    if user is None:
        raise PermissionError("Token inválido")
    if not user.is_admin:
        raise PermissionError("No sos admin")
    target = next((u for u in load_users() if u.user_id == target_user_id), None)
    if target is None:
        raise ValueError(f"User '{target_user_id}' no existe")
    _switched[admin_token] = target_user_id


def admin_switch_clear(admin_token: str) -> None:
    """Vuelve a ver tus propios datos."""
    _switched.pop(admin_token, None)


def is_switched(token: str) -> bool:
    return token in _switched


# =============================================================================
# Mutaciones (escriben a disk si WM_USERS_FILE está seteado)
# =============================================================================

def _load_current_dict() -> dict:
    """Devuelve el dict actual de users (file primero, env var como fallback).
    A diferencia de load_users(), preserva el formato dict crudo."""
    file_data = _read_users_file()
    if file_data is not None:
        return dict(file_data)
    raw = os.environ.get("WM_USERS_JSON", "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def add_user_to_config(user_id: str, token: str, display_name: str = "",
                        is_admin: bool = False) -> None:
    """Agrega/actualiza un user. Persiste a WM_USERS_FILE si está seteado;
    si no, mutate WM_USERS_JSON env var (back-compat). En modo file la
    persistencia sobrevive reloads sin tocar el WSGI."""
    data = _load_current_dict()
    data[user_id] = {
        "token": token,
        "display_name": display_name or user_id,
        "is_admin": bool(is_admin),
    }
    if _users_file_path() is not None:
        _write_users_file(data)
    # También mantener env var en sync (por si load_users hace fallback)
    os.environ["WM_USERS_JSON"] = json.dumps(data)


def remove_user_from_config(user_id: str) -> None:
    """Remueve un user. Persiste a WM_USERS_FILE si está seteado."""
    data = _load_current_dict()
    if user_id in data:
        del data[user_id]
        if _users_file_path() is not None:
            _write_users_file(data)
        os.environ["WM_USERS_JSON"] = json.dumps(data)


def export_users_json() -> str:
    """Devuelve el JSON actual de users (sin tokens, para mostrar al admin)."""
    return json.dumps({u.user_id: {
        "display_name": u.display_name,
        "is_admin": u.is_admin,
        "token_preview": u.token[:8] + "..." if len(u.token) > 8 else u.token,
    } for u in load_users()}, indent=2)


def is_persistent() -> bool:
    """True si las mutaciones se persisten automáticamente (WM_USERS_FILE
    seteado). False si solo viven en memoria del worker."""
    return _users_file_path() is not None

