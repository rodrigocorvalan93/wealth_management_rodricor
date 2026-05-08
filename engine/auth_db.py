# -*- coding: utf-8 -*-
"""
engine/auth_db.py

Schema y helpers de la DB de autenticación (`data/auth.db`).

Esta DB es COMPARTIDA entre todos los users (a diferencia de `wealth.db`
que es per-user). Acá viven:

  - auth_users:               registración email/password
  - auth_sessions:            tokens de sesión emitidos al login
  - password_reset_tokens:    para forgot-password flow
  - email_verification_tokens

Los password se hashean con scrypt (built-in en hashlib, sin deps extra).
Los tokens son tokens criptográficamente seguros (secrets.token_urlsafe).

Convención: cada token también guarda un `prefix` (los primeros 8 chars
sin hashear) para poder mostrar al user "tu sesión empieza con xyz123..."
sin exponer el token completo.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# scrypt params: n=2**14, r=8, p=1 → ~30ms en hardware típico.
# Suficiente para resistir bruteforce offline. n=2**15 sería más fuerte
# pero excede el memory limit default de OpenSSL en algunos setups
# (el cómputo de memoria es 128 * N * r ≈ 32 MB con N=2**15).
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
# maxmem=64 MB — generoso para que la verificación nunca falle por límite
SCRYPT_MAXMEM = 64 * 1024 * 1024

SESSION_TTL_DAYS = 30
RESET_TTL_HOURS = 1
EMAIL_VERIFY_TTL_HOURS = 48


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS auth_users (
    user_id            TEXT PRIMARY KEY,           -- handle (slug del email + random)
    email              TEXT NOT NULL UNIQUE,        -- normalizado a lowercase
    password_hash      TEXT NOT NULL,               -- scrypt hex
    password_salt      TEXT NOT NULL,               -- hex
    display_name       TEXT,
    is_admin           INTEGER NOT NULL DEFAULT 0,  -- admin (puede gestionar otros users)
    is_superadmin      INTEGER NOT NULL DEFAULT 0,  -- superadmin (vos)
    email_verified     INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at      TEXT,
    locked_until       TEXT,                         -- bloqueo temporal por failed logins
    failed_attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_auth_users_email ON auth_users(email);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash         TEXT PRIMARY KEY,           -- SHA256(token) — token NUNCA en disk en claro
    token_prefix       TEXT NOT NULL,              -- primeros 8 chars del token (visible)
    user_id            TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at       TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at         TEXT NOT NULL,
    user_agent         TEXT,
    ip                 TEXT,
    FOREIGN KEY (user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at    TEXT NOT NULL,
    used_at       TEXT,
    ip            TEXT,
    FOREIGN KEY (user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    token_hash    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    new_email     TEXT,                           -- si es change-email, el target
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at    TEXT NOT NULL,
    used_at       TEXT,
    FOREIGN KEY (user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
);

-- Audit de auth events (logins, signups, resets) — separado del audit
-- per-user porque acá entran cosas pre-login (intento fallido).
CREATE TABLE IF NOT EXISTS auth_audit (
    audit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL DEFAULT (datetime('now')),
    event         TEXT NOT NULL,                  -- 'login_ok','login_fail','signup','reset_request','reset_use','verify','logout','password_change'
    email         TEXT,
    user_id       TEXT,
    ip            TEXT,
    user_agent    TEXT,
    detail        TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_audit_ts ON auth_audit(ts);
CREATE INDEX IF NOT EXISTS idx_auth_audit_email ON auth_audit(email);
"""


def auth_db_path() -> Path:
    """Path a la DB de auth (compartida)."""
    base = Path(os.environ.get("WM_BASE_DIR", ".")).resolve()
    return base / "data" / "auth.db"


def open_db() -> sqlite3.Connection:
    """Abre conexión y crea schema si falta."""
    path = auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_DDL)
    conn.commit()
    return conn


# =============================================================================
# Password hashing (scrypt, built-in)
# =============================================================================

def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Devuelve (hash_hex, salt_hex). Salt nuevo si no se pasa."""
    if salt is None:
        salt = secrets.token_bytes(16)
    h = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return (h.hex(), salt.hex())


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    """Verifica password contra hash+salt almacenado."""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    new_hash, _ = hash_password(password, salt=salt)
    # Constant-time comparison
    return secrets.compare_digest(new_hash, hash_hex)


# =============================================================================
# Token helpers (sessions, reset, verify)
# =============================================================================

def generate_token() -> str:
    """Genera un token criptográfico ~43 chars b64."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA256 hex del token. Lo que guardamos en disk."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """Primeros 8 chars del token — visibles en UI."""
    return token[:8]


# =============================================================================
# Email validation + user_id slugs
# =============================================================================

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def slugify_email(email: str) -> str:
    """Convierte email → slug filesystem-safe, no único.

    'Rodrigo.Corvalan+wm@gmail.com' → 'rodrigo-corvalan-wm'
    """
    local = (email.split("@")[0] if "@" in email else email).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local).strip("-")
    return slug or "user"


def make_user_id(conn: sqlite3.Connection, email: str) -> str:
    """Genera un user_id único filesystem-safe a partir del email."""
    base = slugify_email(email)[:24]
    candidate = base
    n = 0
    while True:
        cur = conn.execute(
            "SELECT 1 FROM auth_users WHERE user_id=? LIMIT 1", (candidate,)
        )
        if cur.fetchone() is None:
            return candidate
        n += 1
        suffix = secrets.token_hex(3)  # 6 hex chars
        candidate = f"{base}-{suffix}"
        if n > 5:
            # Highly unlikely; fall back to fully random
            return f"user-{secrets.token_hex(6)}"


# =============================================================================
# Audit
# =============================================================================

def audit(conn: sqlite3.Connection, event: str, email: Optional[str] = None,
          user_id: Optional[str] = None, ip: Optional[str] = None,
          user_agent: Optional[str] = None, detail: Optional[str] = None):
    """Registra un evento de auth en auth_audit."""
    conn.execute(
        """INSERT INTO auth_audit (event, email, user_id, ip, user_agent, detail)
           VALUES (?,?,?,?,?,?)""",
        (event, normalize_email(email) if email else None,
         user_id, ip, user_agent, detail),
    )
    conn.commit()


# =============================================================================
# Cleanup
# =============================================================================

def purge_expired(conn: sqlite3.Connection) -> dict:
    """Borra sessions y reset tokens expirados. Best-effort."""
    now = datetime.now().isoformat()
    out = {}
    for table in ("auth_sessions", "password_reset_tokens",
                  "email_verification_tokens"):
        cur = conn.execute(
            f"DELETE FROM {table} WHERE expires_at < ?", (now,)
        )
        out[table] = cur.rowcount
    conn.commit()
    return out


# =============================================================================
# Datetime helpers
# =============================================================================

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def expiry_iso(seconds: int = 0, hours: int = 0, days: int = 0) -> str:
    delta = timedelta(seconds=seconds, hours=hours, days=days)
    return (datetime.now() + delta).isoformat(timespec="seconds")
