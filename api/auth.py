# -*- coding: utf-8 -*-
"""
api/auth.py

Lógica de autenticación con email + password.

Endpoints (en api/app.py):
  POST /api/auth/signup            → crea user, manda verify email
  POST /api/auth/login             → valida creds, emite session token
  POST /api/auth/logout            → invalida session token actual
  POST /api/auth/forgot-password   → manda reset email (no revela si el email existe)
  POST /api/auth/reset-password    → cambia password con reset token
  POST /api/auth/verify-email      → marca email_verified=1 con verify token
  POST /api/auth/change-password   → cambia password con la actual + nueva
  GET  /api/auth/me                → info del user logueado
  POST /api/auth/resend-verify     → re-manda email de verificación

Convenciones:
  - Las funciones de este módulo NO tocan flask request/g; reciben todo
    como argumentos. Eso facilita testear sin server.
  - Cualquier función que pueda fallar lanza AuthError con un código
    que la API mapea a HTTP status:

      INVALID_EMAIL    → 400
      WEAK_PASSWORD    → 400
      EMAIL_TAKEN      → 409
      INVALID_CREDS    → 401
      LOCKED           → 423 (rate-limited después de N failed attempts)
      TOKEN_INVALID    → 400
      TOKEN_EXPIRED    → 410
      TOKEN_USED       → 410
      EMAIL_NOT_VERIFIED → 403 (cuando se requiere)
      USER_NOT_FOUND   → 404

Lockout policy:
  - 5 logins fallidos seguidos → lock por 15 minutos.
  - Reset al hacer un login OK.

Bootstrap superadmin:
  - WM_BOOTSTRAP_SUPERADMIN_EMAIL: si está seteado y NO existe ningún
    superadmin todavía, el signup con ese email se promueve automáticamente.
  - WM_AUTO_VERIFY_FIRST_SUPERADMIN=1: además, marca email_verified=1
    para que pueda loguearse sin email funcional.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from engine.auth_db import (
    open_db, hash_password, verify_password,
    generate_token, hash_token, token_prefix,
    normalize_email, is_valid_email, make_user_id,
    audit, expiry_iso, now_iso,
    SESSION_TTL_DAYS, RESET_TTL_HOURS, EMAIL_VERIFY_TTL_HOURS,
)


# =============================================================================
# Errors
# =============================================================================

class AuthError(Exception):
    """Errores controlados de auth — el código mapea a HTTP status."""
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _bootstrap_email() -> str:
    return normalize_email(
        os.environ.get("WM_BOOTSTRAP_SUPERADMIN_EMAIL", "")
    )


def _auto_verify_first() -> bool:
    return os.environ.get("WM_AUTO_VERIFY_FIRST_SUPERADMIN") == "1"


# =============================================================================
# Password policy
# =============================================================================

MIN_PASSWORD_LEN = 8


def validate_password(password: str) -> None:
    """Lanza AuthError si la password es muy débil."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise AuthError(
            "WEAK_PASSWORD",
            f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres",
            http_status=400,
        )
    # Algún criterio mínimo: que tenga al menos 2 tipos de chars
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_other = any(not c.isalnum() for c in password)
    score = sum([has_letter, has_digit, has_other])
    if score < 2:
        raise AuthError(
            "WEAK_PASSWORD",
            "Mezclá letras + (números o símbolos)",
            http_status=400,
        )


# =============================================================================
# Signup
# =============================================================================

@dataclass
class SignupResult:
    user_id: str
    email: str
    is_superadmin: bool
    is_admin: bool
    verify_token_plain: Optional[str]   # solo en modo dev / si SMTP falla
    verify_email_via: str               # 'smtp', 'outbox', 'outbox-fallback'


def signup(email: str, password: str, display_name: str = "",
           ip: Optional[str] = None, user_agent: Optional[str] = None
           ) -> SignupResult:
    """Crea un user nuevo. Manda email de verificación.

    Si el email matchea WM_BOOTSTRAP_SUPERADMIN_EMAIL y no hay superadmin
    todavía, el user se crea con is_superadmin=1.
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise AuthError("INVALID_EMAIL", "Email inválido")
    validate_password(password)

    conn = open_db()
    try:
        # Email único
        cur = conn.execute(
            "SELECT 1 FROM auth_users WHERE email=? LIMIT 1", (email,)
        )
        if cur.fetchone() is not None:
            raise AuthError("EMAIL_TAKEN",
                             "Ya hay una cuenta con ese email",
                             http_status=409)

        # ¿Promote a superadmin?
        bootstrap = _bootstrap_email()
        is_super = False
        if bootstrap and email == bootstrap:
            cur = conn.execute(
                "SELECT 1 FROM auth_users WHERE is_superadmin=1 LIMIT 1"
            )
            if cur.fetchone() is None:
                is_super = True

        password_hash, salt = hash_password(password)
        user_id = make_user_id(conn, email)
        verified = 1 if (is_super and _auto_verify_first()) else 0

        conn.execute(
            """INSERT INTO auth_users
               (user_id, email, password_hash, password_salt, display_name,
                is_admin, is_superadmin, email_verified)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, email, password_hash, salt,
             display_name or email.split("@")[0],
             1 if is_super else 0,
             1 if is_super else 0,
             verified),
        )

        # Verify token
        verify_token = generate_token()
        conn.execute(
            """INSERT INTO email_verification_tokens
               (token_hash, user_id, expires_at)
               VALUES (?,?,?)""",
            (hash_token(verify_token), user_id,
             expiry_iso(hours=EMAIL_VERIFY_TTL_HOURS)),
        )
        conn.commit()
        audit(conn, "signup", email=email, user_id=user_id, ip=ip,
              user_agent=user_agent,
              detail=("superadmin" if is_super else None))
    finally:
        conn.close()

    # Mandar verify email (fuera del lock de DB)
    via = "skipped"
    if not verified:
        from .email import send_verify_email
        result = send_verify_email(email, verify_token, user_id)
        via = result.get("via", "outbox")

    return SignupResult(
        user_id=user_id, email=email,
        is_superadmin=is_super, is_admin=is_super,
        verify_token_plain=(verify_token if not verified else None),
        verify_email_via=via,
    )


# =============================================================================
# Login / sessions
# =============================================================================

@dataclass
class LoginResult:
    user_id: str
    email: str
    display_name: str
    is_admin: bool
    is_superadmin: bool
    email_verified: bool
    session_token: str          # plain — devolver al cliente
    session_token_prefix: str
    expires_at: str


MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def login(email: str, password: str, ip: Optional[str] = None,
          user_agent: Optional[str] = None) -> LoginResult:
    email = normalize_email(email)
    if not email or not password:
        raise AuthError("INVALID_CREDS", "Email y password requeridos",
                         http_status=400)
    conn = open_db()
    try:
        cur = conn.execute(
            "SELECT * FROM auth_users WHERE email=?", (email,)
        )
        user = cur.fetchone()
        if user is None:
            audit(conn, "login_fail", email=email, ip=ip,
                  user_agent=user_agent, detail="user_not_found")
            # Mensaje genérico para no revelar existencia
            raise AuthError("INVALID_CREDS", "Email o contraseña incorrectos",
                             http_status=401)

        # Lockout
        if user["locked_until"]:
            try:
                until = datetime.fromisoformat(user["locked_until"])
                if datetime.now() < until:
                    audit(conn, "login_fail", email=email,
                          user_id=user["user_id"], ip=ip,
                          user_agent=user_agent, detail="locked")
                    raise AuthError(
                        "LOCKED",
                        f"Cuenta bloqueada hasta {until.isoformat(timespec='minutes')}. "
                        f"Demasiados intentos fallidos.",
                        http_status=423,
                    )
            except ValueError:
                pass

        # Password
        if not verify_password(password, user["password_hash"], user["password_salt"]):
            attempts = (user["failed_attempts"] or 0) + 1
            locked_until = None
            if attempts >= MAX_FAILED_ATTEMPTS:
                locked_until = (datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
                attempts = 0  # reset counter; lock está activo
            conn.execute(
                "UPDATE auth_users SET failed_attempts=?, locked_until=? "
                "WHERE user_id=?",
                (attempts, locked_until, user["user_id"]),
            )
            conn.commit()
            audit(conn, "login_fail", email=email, user_id=user["user_id"],
                  ip=ip, user_agent=user_agent, detail=f"attempt_{attempts}")
            raise AuthError("INVALID_CREDS", "Email o contraseña incorrectos",
                             http_status=401)

        # OK — emitir session
        token = generate_token()
        th = hash_token(token)
        expires = expiry_iso(days=SESSION_TTL_DAYS)
        conn.execute(
            """INSERT INTO auth_sessions
               (token_hash, token_prefix, user_id, expires_at, user_agent, ip)
               VALUES (?,?,?,?,?,?)""",
            (th, token_prefix(token), user["user_id"], expires,
             user_agent, ip),
        )
        conn.execute(
            "UPDATE auth_users SET last_login_at=?, failed_attempts=0, "
            "locked_until=NULL WHERE user_id=?",
            (now_iso(), user["user_id"]),
        )
        conn.commit()
        audit(conn, "login_ok", email=email, user_id=user["user_id"],
              ip=ip, user_agent=user_agent)

        return LoginResult(
            user_id=user["user_id"], email=email,
            display_name=user["display_name"] or email,
            is_admin=bool(user["is_admin"]),
            is_superadmin=bool(user["is_superadmin"]),
            email_verified=bool(user["email_verified"]),
            session_token=token,
            session_token_prefix=token_prefix(token),
            expires_at=expires,
        )
    finally:
        conn.close()


def logout(session_token: str) -> bool:
    """Invalida el session token. Devuelve True si existía."""
    if not session_token:
        return False
    conn = open_db()
    try:
        th = hash_token(session_token)
        cur = conn.execute(
            "SELECT user_id FROM auth_sessions WHERE token_hash=?", (th,)
        )
        row = cur.fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (th,))
        conn.commit()
        audit(conn, "logout", user_id=row["user_id"])
        return True
    finally:
        conn.close()


def resolve_session(session_token: str) -> Optional[dict]:
    """Devuelve dict con info del user si el session_token es válido y
    no expiró. None si no.
    """
    if not session_token:
        return None
    th = hash_token(session_token)
    conn = open_db()
    try:
        cur = conn.execute(
            """SELECT s.token_hash, s.user_id, s.expires_at,
                      u.email, u.display_name, u.is_admin, u.is_superadmin,
                      u.email_verified
               FROM auth_sessions s
               JOIN auth_users u ON u.user_id = s.user_id
               WHERE s.token_hash=?""",
            (th,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now():
                conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (th,))
                conn.commit()
                return None
        except (ValueError, TypeError):
            return None
        # Touch last_used_at (sin commit cada request — OK, write barato)
        conn.execute(
            "UPDATE auth_sessions SET last_used_at=? WHERE token_hash=?",
            (now_iso(), th),
        )
        conn.commit()
        return {
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "is_admin": bool(row["is_admin"]),
            "is_superadmin": bool(row["is_superadmin"]),
            "email_verified": bool(row["email_verified"]),
            "expires_at": row["expires_at"],
        }
    finally:
        conn.close()


def list_user_sessions(user_id: str) -> list[dict]:
    """Lista sessions activas del user (sin tokens, solo prefixes)."""
    conn = open_db()
    try:
        cur = conn.execute(
            """SELECT token_prefix, created_at, last_used_at, expires_at,
                      user_agent, ip
               FROM auth_sessions WHERE user_id=?
               ORDER BY last_used_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def revoke_session(user_id: str, token_prefix_match: str) -> bool:
    """Borra una session por prefix (mostrado en UI). True si encontró una."""
    conn = open_db()
    try:
        cur = conn.execute(
            "DELETE FROM auth_sessions WHERE user_id=? AND token_prefix=?",
            (user_id, token_prefix_match),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def revoke_all_sessions(user_id: str, except_token: Optional[str] = None) -> int:
    """Borra todas las sesiones del user, opcionalmente preservando una."""
    conn = open_db()
    try:
        if except_token:
            cur = conn.execute(
                "DELETE FROM auth_sessions WHERE user_id=? AND token_hash<>?",
                (user_id, hash_token(except_token)),
            )
        else:
            cur = conn.execute(
                "DELETE FROM auth_sessions WHERE user_id=?", (user_id,)
            )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# =============================================================================
# Password reset
# =============================================================================

def request_password_reset(email: str, ip: Optional[str] = None) -> dict:
    """Genera reset token y manda email. SIEMPRE devuelve 'ok' aunque el
    email no exista (no revelar enumeration)."""
    email = normalize_email(email)
    conn = open_db()
    try:
        cur = conn.execute(
            "SELECT user_id FROM auth_users WHERE email=?", (email,)
        )
        row = cur.fetchone()
        if row is None:
            audit(conn, "reset_request", email=email, ip=ip,
                  detail="user_not_found")
            return {"ok": True, "via": "noop"}
        user_id = row["user_id"]

        # Invalidar reset tokens previos no usados (best-effort)
        conn.execute(
            "DELETE FROM password_reset_tokens "
            "WHERE user_id=? AND used_at IS NULL",
            (user_id,),
        )
        token = generate_token()
        conn.execute(
            """INSERT INTO password_reset_tokens
               (token_hash, user_id, expires_at, ip)
               VALUES (?,?,?,?)""",
            (hash_token(token), user_id,
             expiry_iso(hours=RESET_TTL_HOURS), ip),
        )
        conn.commit()
        audit(conn, "reset_request", email=email, user_id=user_id, ip=ip)
    finally:
        conn.close()

    from .email import send_reset_email
    result = send_reset_email(email, token)
    return {"ok": True, "via": result.get("via", "outbox")}


def reset_password_with_token(token: str, new_password: str,
                               ip: Optional[str] = None) -> dict:
    """Cambia password usando un reset token. Invalida todas las sesiones
    del user después de cambiar."""
    if not token:
        raise AuthError("TOKEN_INVALID", "Token requerido")
    validate_password(new_password)

    conn = open_db()
    try:
        th = hash_token(token)
        cur = conn.execute(
            """SELECT user_id, expires_at, used_at
               FROM password_reset_tokens WHERE token_hash=?""",
            (th,),
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("TOKEN_INVALID", "Link inválido")
        if row["used_at"]:
            raise AuthError("TOKEN_USED", "Este link ya se usó",
                             http_status=410)
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now():
                raise AuthError("TOKEN_EXPIRED", "El link expiró",
                                 http_status=410)
        except ValueError:
            raise AuthError("TOKEN_INVALID", "Token inválido")

        new_hash, new_salt = hash_password(new_password)
        conn.execute(
            "UPDATE auth_users SET password_hash=?, password_salt=?, "
            "failed_attempts=0, locked_until=NULL WHERE user_id=?",
            (new_hash, new_salt, row["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at=? WHERE token_hash=?",
            (now_iso(), th),
        )
        # Invalidar todas las sesiones (forzar re-login)
        conn.execute(
            "DELETE FROM auth_sessions WHERE user_id=?", (row["user_id"],)
        )
        conn.commit()
        audit(conn, "reset_use", user_id=row["user_id"], ip=ip)
        return {"ok": True, "user_id": row["user_id"]}
    finally:
        conn.close()


# =============================================================================
# Email verification
# =============================================================================

def verify_email_with_token(token: str) -> dict:
    if not token:
        raise AuthError("TOKEN_INVALID", "Token requerido")
    conn = open_db()
    try:
        th = hash_token(token)
        cur = conn.execute(
            """SELECT user_id, expires_at, used_at, new_email
               FROM email_verification_tokens WHERE token_hash=?""",
            (th,),
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("TOKEN_INVALID", "Link inválido")
        if row["used_at"]:
            raise AuthError("TOKEN_USED", "Este link ya se usó",
                             http_status=410)
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now():
                raise AuthError("TOKEN_EXPIRED", "El link expiró",
                                 http_status=410)
        except ValueError:
            raise AuthError("TOKEN_INVALID", "Token inválido")

        # Si había new_email (change-email flow), aplicarlo. Si no, solo
        # marcar email_verified=1.
        if row["new_email"]:
            conn.execute(
                "UPDATE auth_users SET email=?, email_verified=1 WHERE user_id=?",
                (row["new_email"], row["user_id"]),
            )
        else:
            conn.execute(
                "UPDATE auth_users SET email_verified=1 WHERE user_id=?",
                (row["user_id"],),
            )
        conn.execute(
            "UPDATE email_verification_tokens SET used_at=? WHERE token_hash=?",
            (now_iso(), th),
        )
        conn.commit()

        # Email de bienvenida (best-effort)
        cur = conn.execute(
            "SELECT email, is_superadmin FROM auth_users WHERE user_id=?",
            (row["user_id"],),
        )
        urow = cur.fetchone()
        audit(conn, "verify", user_id=row["user_id"],
              email=urow["email"] if urow else None)
        if urow:
            try:
                from .email import send_welcome_email
                send_welcome_email(urow["email"], row["user_id"],
                                    bool(urow["is_superadmin"]))
            except Exception as e:
                print(f"[auth] welcome email falló: {e}")
        return {"ok": True, "user_id": row["user_id"]}
    finally:
        conn.close()


def resend_verification(user_id: str) -> dict:
    """Re-emite verify token y manda nuevo email."""
    conn = open_db()
    try:
        cur = conn.execute(
            "SELECT email, email_verified FROM auth_users WHERE user_id=?",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("USER_NOT_FOUND", "User no encontrado",
                             http_status=404)
        if row["email_verified"]:
            return {"ok": True, "already_verified": True}
        # Borrar tokens previos no usados
        conn.execute(
            "DELETE FROM email_verification_tokens "
            "WHERE user_id=? AND used_at IS NULL",
            (user_id,),
        )
        token = generate_token()
        conn.execute(
            """INSERT INTO email_verification_tokens
               (token_hash, user_id, expires_at)
               VALUES (?,?,?)""",
            (hash_token(token), user_id,
             expiry_iso(hours=EMAIL_VERIFY_TTL_HOURS)),
        )
        conn.commit()
    finally:
        conn.close()
    from .email import send_verify_email
    res = send_verify_email(row["email"], token, user_id)
    return {"ok": True, "via": res.get("via", "outbox")}


# =============================================================================
# Change password (logueado)
# =============================================================================

def change_password(user_id: str, current_password: str,
                     new_password: str, current_session_token: Optional[str] = None
                     ) -> dict:
    validate_password(new_password)
    conn = open_db()
    try:
        cur = conn.execute(
            "SELECT password_hash, password_salt, email FROM auth_users WHERE user_id=?",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("USER_NOT_FOUND", "User no encontrado",
                             http_status=404)
        if not verify_password(current_password, row["password_hash"], row["password_salt"]):
            raise AuthError("INVALID_CREDS",
                             "La contraseña actual es incorrecta",
                             http_status=401)
        new_hash, new_salt = hash_password(new_password)
        conn.execute(
            "UPDATE auth_users SET password_hash=?, password_salt=? WHERE user_id=?",
            (new_hash, new_salt, user_id),
        )
        # Invalidar otras sesiones (mantener la actual)
        if current_session_token:
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id=? AND token_hash<>?",
                (user_id, hash_token(current_session_token)),
            )
        else:
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id=?", (user_id,)
            )
        conn.commit()
        audit(conn, "password_change", user_id=user_id, email=row["email"])
        return {"ok": True}
    finally:
        conn.close()


# =============================================================================
# Profile
# =============================================================================

def get_user_profile(user_id: str) -> Optional[dict]:
    conn = open_db()
    try:
        cur = conn.execute(
            """SELECT user_id, email, display_name, is_admin, is_superadmin,
                      email_verified, created_at, last_login_at
               FROM auth_users WHERE user_id=?""",
            (user_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_display_name(user_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise AuthError("INVALID_NAME", "Nombre vacío")
    if len(name) > 80:
        raise AuthError("INVALID_NAME", "Nombre demasiado largo")
    conn = open_db()
    try:
        conn.execute(
            "UPDATE auth_users SET display_name=? WHERE user_id=?",
            (name, user_id),
        )
        conn.commit()
        return {"ok": True, "display_name": name}
    finally:
        conn.close()


# =============================================================================
# Superadmin operations
# =============================================================================

def list_all_auth_users() -> list[dict]:
    conn = open_db()
    try:
        cur = conn.execute(
            """SELECT user_id, email, display_name, is_admin, is_superadmin,
                      email_verified, created_at, last_login_at
               FROM auth_users ORDER BY created_at DESC"""
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def set_admin_flag(user_id: str, is_admin: bool, set_by_user_id: str) -> dict:
    conn = open_db()
    try:
        conn.execute(
            "UPDATE auth_users SET is_admin=? WHERE user_id=?",
            (1 if is_admin else 0, user_id),
        )
        conn.commit()
        audit(conn, "set_admin", user_id=user_id,
              detail=f"by={set_by_user_id} value={is_admin}")
        return {"ok": True}
    finally:
        conn.close()


def set_superadmin_flag(user_id: str, is_superadmin: bool,
                         set_by_user_id: str) -> dict:
    conn = open_db()
    try:
        conn.execute(
            "UPDATE auth_users SET is_superadmin=?, is_admin=? WHERE user_id=?",
            (1 if is_superadmin else 0,
             1 if is_superadmin else 0,
             user_id),
        )
        conn.commit()
        audit(conn, "set_superadmin", user_id=user_id,
              detail=f"by={set_by_user_id} value={is_superadmin}")
        return {"ok": True}
    finally:
        conn.close()


def delete_auth_user(user_id: str, by_user_id: str) -> dict:
    """Borra un user de auth_users. Las cascades borran sus sesiones
    y tokens. NO borra los datos de wealth (xlsx, db) — eso lo hace
    /api/account."""
    conn = open_db()
    try:
        cur = conn.execute(
            "SELECT email FROM auth_users WHERE user_id=?", (user_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("USER_NOT_FOUND", "User no encontrado",
                             http_status=404)
        conn.execute("DELETE FROM auth_users WHERE user_id=?", (user_id,))
        conn.commit()
        audit(conn, "user_deleted", user_id=user_id, email=row["email"],
              detail=f"by={by_user_id}")
        return {"ok": True}
    finally:
        conn.close()
