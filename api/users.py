# -*- coding: utf-8 -*-
"""
api/users.py

Resolución de usuarios y permisos para la app multi-tenant.

Modelo:
- Cada user tiene: user_id (handle), token (bearer), display_name, is_admin
- Config se lee de WM_USERS_JSON (env var) en formato:
    {"rodricor": {"token": "abc...", "display_name": "Rodrigo", "is_admin": true},
     "amigo":    {"token": "xyz...", "display_name": "Amigo"}}

Backward compat: si WM_USERS_JSON no está pero WM_API_TOKEN sí, asumimos
single-tenant con user_id="default" y is_admin=true. Esto deja andar el setup
viejo sin tocar nada.

Switch user (admin only):
- Un admin puede pedir POST /api/admin/switch con un target_user_id.
- Mientras esté switched, sus GETs (read-only) ven datos del target.
- Las mutations las bloqueamos: el admin NO puede modificar datos de otro user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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


def _parse_users_json() -> list[UserConfig]:
    """Parsea WM_USERS_JSON en una lista de UserConfig."""
    raw = os.environ.get("WM_USERS_JSON")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[users] WM_USERS_JSON inválido: {e}")
        return []

    users = []
    admin_user = os.environ.get("WM_ADMIN_USER", "").strip()

    if isinstance(data, dict):
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

    Multi-tenant: lee WM_USERS_JSON.
    Backward compat: si no está, usa WM_API_TOKEN como user="default".
    """
    users = _parse_users_json()
    if users:
        return users
    # Fallback single-tenant
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
    # Validar que target exista
    target = next((u for u in load_users() if u.user_id == target_user_id), None)
    if target is None:
        raise ValueError(f"User '{target_user_id}' no existe")
    _switched[admin_token] = target_user_id


def admin_switch_clear(admin_token: str) -> None:
    """Vuelve a ver tus propios datos."""
    _switched.pop(admin_token, None)


def is_switched(token: str) -> bool:
    return token in _switched


def add_user_to_config(user_id: str, token: str, display_name: str = "",
                        is_admin: bool = False) -> None:
    """Agrega un user al WM_USERS_JSON in-memory (NO persiste a disk).

    Para persistir, el admin debe editar el WSGI config en PythonAnywhere
    y agregar el user al JSON manualmente. Esta función actualiza el env
    var en runtime para que el user sea reconocido inmediatamente por las
    siguientes requests (hasta el próximo reload).
    """
    raw = os.environ.get("WM_USERS_JSON", "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[user_id] = {
        "token": token,
        "display_name": display_name or user_id,
        "is_admin": bool(is_admin),
    }
    os.environ["WM_USERS_JSON"] = json.dumps(data)


def remove_user_from_config(user_id: str) -> None:
    """Remueve un user del WM_USERS_JSON in-memory."""
    raw = os.environ.get("WM_USERS_JSON", "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return
    if isinstance(data, dict) and user_id in data:
        del data[user_id]
        os.environ["WM_USERS_JSON"] = json.dumps(data)


def export_users_json() -> str:
    """Devuelve el JSON actual de users (sin tokens, para mostrar al admin)."""
    return json.dumps({u.user_id: {
        "display_name": u.display_name,
        "is_admin": u.is_admin,
        "token_preview": u.token[:8] + "..." if len(u.token) > 8 else u.token,
    } for u in load_users()}, indent=2)
