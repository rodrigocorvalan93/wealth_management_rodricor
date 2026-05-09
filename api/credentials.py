# -*- coding: utf-8 -*-
"""
api/credentials.py

Storage seguro de credenciales por user. Reemplaza la convención
secrets.txt single-tenant por un store encriptado per-user.

Layout:
  <user_data_dir>/credentials.enc   ← JSON encriptado con Fernet

Encryption:
  - Master key viene de la env var WM_ENCRYPTION_KEY (Fernet base64).
  - Si no está seteada, se genera una y se guarda en
    <BASE_DIR>/data/.encryption_key (solo permisos 0600). Es persistente
    pero NO debería commitearse — agrega a .gitignore.
  - El user_id se mezcla con el master key para derivar una key per-user
    (HKDF), así un dump de credentials.enc de user A no se descifra con
    la master key sola sin saber a quién pertenece.

Credenciales soportadas:
  - byma_user / byma_pass   → para byma_loader.run()
  - byma_api_url            → opcional override (default cocos)
  - cafci_token             → para cafci_loader

Funciones:
  get_credentials(user_id) -> dict
  set_credentials(user_id, data: dict)
  delete_credentials(user_id)
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


CRED_FIELDS = (
    "byma_user", "byma_pass", "byma_api_url",
    "cafci_token",
    "binance_api_key", "binance_api_secret",
    "ibkr_flex_token", "ibkr_flex_query_id",
)


def _load_master_key() -> bytes:
    """Devuelve el master Fernet key como bytes. Lee de WM_ENCRYPTION_KEY
    o genera+guarda en data/.encryption_key.
    """
    env_key = os.environ.get("WM_ENCRYPTION_KEY", "").strip()
    if env_key:
        # Fernet keys son 44 bytes b64; aceptamos también raw 32 bytes b64
        try:
            Fernet(env_key.encode() if isinstance(env_key, str) else env_key)
            return env_key.encode() if isinstance(env_key, str) else env_key
        except Exception:
            pass

    base = Path(os.environ.get("WM_BASE_DIR", ".")).resolve()
    key_file = base / "data" / ".encryption_key"
    if key_file.is_file():
        try:
            data = key_file.read_bytes().strip()
            if data:
                Fernet(data)  # validar
                return data
        except Exception:
            pass

    # Generar nueva master key
    new_key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(new_key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return new_key


def _user_key(user_id: str) -> bytes:
    """Deriva una Fernet key per-user a partir de la master key + user_id."""
    master = _load_master_key()
    # Decodear master (Fernet key es base64) para obtener el material crudo,
    # después HKDF-SHA256 con el user_id como info para mezclar.
    try:
        material = base64.urlsafe_b64decode(master)
    except Exception:
        material = master
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"wm-credentials-v1",
        info=user_id.encode("utf-8"),
    ).derive(material)
    return base64.urlsafe_b64encode(derived)


def _cred_path(user_id: str) -> Path:
    """Devuelve el path al archivo de credenciales del user."""
    from .state import get_user_settings
    s = get_user_settings(user_id)
    s.user_data_dir.mkdir(parents=True, exist_ok=True)
    return s.user_data_dir / "credentials.enc"


def get_credentials(user_id: str) -> dict:
    """Lee credenciales del user. Devuelve dict (vacío si no hay).

    Las claves nunca se devuelven con valores vacíos — solo las que el
    user haya seteado.
    """
    path = _cred_path(user_id)
    if not path.is_file():
        return {}
    try:
        f = Fernet(_user_key(user_id))
        plain = f.decrypt(path.read_bytes())
        data = json.loads(plain.decode("utf-8"))
        return {k: v for k, v in data.items() if v}
    except (InvalidToken, json.JSONDecodeError, ValueError):
        # Archivo corrupto o key cambió — no romper la app, solo loguear
        print(f"[credentials] WARN no pude desencriptar {path} — ignorando")
        return {}


def set_credentials(user_id: str, data: dict) -> dict:
    """Hace MERGE de las credenciales existentes con `data`.

    - Solo se persisten campos en CRED_FIELDS.
    - Pasar value=None o "" BORRA esa credencial.
    - Trimea whitespace y CRLF para evitar errores de copy-paste.
    - Devuelve el dict final (con keys, sin valores) para confirmar.
    """
    current = get_credentials(user_id)
    for k, v in data.items():
        if k not in CRED_FIELDS:
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            current.pop(k, None)
        else:
            # Trim whitespace + newlines (los password managers a veces
            # los agregan al copiar) + saltos de linea internos en tokens
            cleaned = str(v).strip().replace("\r", "").replace("\n", "")
            current[k] = cleaned

    path = _cred_path(user_id)
    if current:
        f = Fernet(_user_key(user_id))
        token = f.encrypt(json.dumps(current).encode("utf-8"))
        path.write_bytes(token)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    elif path.is_file():
        # No queda nada → borrar el archivo
        try:
            path.unlink()
        except OSError:
            pass

    return {k: True for k in current.keys()}


def delete_credentials(user_id: str) -> bool:
    """Borra todas las credenciales del user."""
    path = _cred_path(user_id)
    if path.is_file():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


SUPERADMIN_ONLY_FIELDS = {"cafci_token"}


def list_supported_fields(is_superadmin: bool = False) -> list[dict]:
    """Devuelve metadata de las credenciales soportadas para que la UI
    arme el form.

    Si `is_superadmin` es False, oculta las credenciales globales que
    sólo el superadmin puede configurar (ej. token de CAFCI — los datos
    de CAFCI son compartidos entre todos los users, así que la baja la
    dispara únicamente el superadmin).
    """
    fields = [
        {
            "key": "byma_user",
            "label": "BYMA / OMS user",
            "type": "text",
            "secret": False,
            "help": "Usuario del OMS (Cocos / LatinSecurities). Lo usa el loader de precios BYMA.",
        },
        {
            "key": "byma_pass",
            "label": "BYMA / OMS password",
            "type": "password",
            "secret": True,
            "help": "Contraseña del OMS. Se guarda encriptada y nunca se devuelve por la API.",
        },
        {
            "key": "byma_api_url",
            "label": "BYMA API URL (opcional)",
            "type": "text",
            "secret": False,
            "help": "Default: https://api.cocos.xoms.com.ar/. Cambialo si usás otro broker (Latin: https://api.latinsecurities.matrizoms.com.ar/).",
        },
        {
            "key": "cafci_token",
            "label": "CAFCI Bearer token (solo superadmin)",
            "type": "password",
            "secret": True,
            "superadmin_only": True,
            "help": "Token del API de CAFCI para precios de FCIs. Incluí 'Bearer ' al principio. Los precios bajados se comparten entre todos los users.",
        },
        {
            "key": "binance_api_key",
            "label": "Binance API key",
            "type": "text",
            "secret": False,
            "help": "API key de Binance (read-only). Crealo en Account → API Management con permiso 'Enable Reading' SOLAMENTE — sin trading ni withdrawals.",
        },
        {
            "key": "binance_api_secret",
            "label": "Binance API secret",
            "type": "password",
            "secret": True,
            "help": "Secret de Binance. Encriptado en disk.",
        },
        {
            "key": "ibkr_flex_token",
            "label": "IBKR Flex token",
            "type": "password",
            "secret": True,
            "help": "Token de Flex Web Service de IBKR. Generalo en Reports → Settings → Flex Web Service. Es un read-only token (no permite trading).",
        },
        {
            "key": "ibkr_flex_query_id",
            "label": "IBKR Flex query ID",
            "type": "text",
            "secret": False,
            "help": "ID numérico de tu Flex Query. Configurá una query con secciones 'Open Positions' y 'Trades' en IBKR → Reports → Flex Queries.",
        },
    ]
    if not is_superadmin:
        fields = [f for f in fields if not f.get("superadmin_only")]
    return fields
