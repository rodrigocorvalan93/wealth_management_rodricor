# -*- coding: utf-8 -*-
"""
api/email.py

Wrapper minimalista de envío de email.

Modos:
  - SMTP real: si están seteadas WM_SMTP_HOST + WM_SMTP_USER + WM_SMTP_PASS,
    manda via smtplib (TLS o SSL según el puerto).
  - Outbox file: si falta config, escribe el .eml a `data/_outbox/` para
    que el admin lo mande manualmente o un agente lo procese. Sirve para
    desarrollo y para deploys donde aún no está config'd el SMTP.
  - Disabled: WM_SMTP_DISABLED=1 fuerza outbox sin warnings.

Env vars:
  WM_SMTP_HOST     ej smtp.gmail.com
  WM_SMTP_PORT     ej 587 (TLS) o 465 (SSL). Default 587.
  WM_SMTP_USER     usuario
  WM_SMTP_PASS     password (o app password)
  WM_SMTP_FROM     "Wealth Management <noreply@dominio>" — default = WM_SMTP_USER
  WM_SMTP_TLS      'starttls' | 'ssl' | 'plain'  (default 'starttls' si port=587, 'ssl' si 465)
  WM_APP_URL       URL pública del frontend (para links de reset/verify).
                    Ej: https://wm.example.com   (default: http://localhost:5000)
"""

from __future__ import annotations

import email.utils
import os
import smtplib
import ssl
import uuid
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


def _smtp_config() -> Optional[dict]:
    """Devuelve dict con config de SMTP o None si no está completo."""
    if os.environ.get("WM_SMTP_DISABLED") == "1":
        return None
    host = os.environ.get("WM_SMTP_HOST", "").strip()
    user = os.environ.get("WM_SMTP_USER", "").strip()
    pwd = os.environ.get("WM_SMTP_PASS", "").strip()
    if not all([host, user, pwd]):
        return None
    port = int(os.environ.get("WM_SMTP_PORT", "587") or "587")
    tls = os.environ.get("WM_SMTP_TLS", "").lower()
    if not tls:
        tls = "ssl" if port == 465 else "starttls"
    return {
        "host": host, "port": port, "user": user, "password": pwd,
        "tls": tls,
        "from_addr": os.environ.get("WM_SMTP_FROM", "").strip() or user,
    }


def app_url() -> str:
    return (os.environ.get("WM_APP_URL", "").strip().rstrip("/")
            or "http://localhost:5000")


def _outbox_dir() -> Path:
    base = Path(os.environ.get("WM_BASE_DIR", ".")).resolve()
    p = base / "data" / "_outbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _build_message(to: str, subject: str, body_text: str,
                    body_html: Optional[str] = None,
                    from_addr: Optional[str] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr or "wm@example.com"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-Id"] = email.utils.make_msgid(domain="wm")
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


def send_email(to: str, subject: str, body_text: str,
               body_html: Optional[str] = None) -> dict:
    """Manda un email. Devuelve {"sent": bool, "path": str|None, "via": str}.

    Si SMTP está configurado, lo manda. Si no, lo escribe a outbox.
    Errores de SMTP caen automáticamente al outbox para no perder mails.
    """
    cfg = _smtp_config()
    msg = _build_message(
        to=to, subject=subject, body_text=body_text, body_html=body_html,
        from_addr=cfg["from_addr"] if cfg else None,
    )

    if cfg:
        try:
            if cfg["tls"] == "ssl":
                with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20,
                                       context=ssl.create_default_context()) as s:
                    s.login(cfg["user"], cfg["password"])
                    s.send_message(msg)
            else:
                with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
                    s.ehlo()
                    if cfg["tls"] == "starttls":
                        s.starttls(context=ssl.create_default_context())
                        s.ehlo()
                    s.login(cfg["user"], cfg["password"])
                    s.send_message(msg)
            return {"sent": True, "path": None, "via": "smtp"}
        except (smtplib.SMTPException, OSError) as e:
            print(f"[email] SMTP falló ({type(e).__name__}: {e}) — caiéndome al outbox")
            # fallthrough al outbox
            via_fallback = "outbox-fallback"
        else:
            via_fallback = "smtp"
    else:
        via_fallback = "outbox"

    # Outbox
    fname = (f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-"
             f"{uuid.uuid4().hex[:8]}.eml")
    path = _outbox_dir() / fname
    path.write_bytes(bytes(msg))
    return {"sent": True, "path": str(path), "via": via_fallback}


# =============================================================================
# Templates específicos del producto
# =============================================================================

def send_verify_email(email_addr: str, token: str, user_id: str) -> dict:
    """Email de verificación post-signup."""
    link = f"{app_url()}/#/verify-email?token={token}"
    subject = "Verificá tu email — Wealth Management"
    text = (
        f"¡Hola!\n\n"
        f"Confirmá que este email es tuyo abriendo este link "
        f"(expira en 48 horas):\n\n  {link}\n\n"
        f"Tu user_id es: {user_id}\n\n"
        f"Si no te registraste, ignorá este mensaje.\n\n"
        f"— Wealth Management\n"
    )
    html = (
        f"<p>¡Hola!</p>"
        f"<p>Confirmá que este email es tuyo apretando el botón "
        f"(expira en 48 horas):</p>"
        f'<p><a href="{link}" style="display:inline-block;padding:12px 24px;'
        f'background:#1F3864;color:#fff;text-decoration:none;border-radius:8px;">'
        f"Verificar email</a></p>"
        f'<p style="color:#666;font-size:12px;">O copiá este link: <code>{link}</code></p>'
        f"<p>Tu user_id es: <b>{user_id}</b></p>"
        f'<hr style="border:0;border-top:1px solid #eee">'
        f'<p style="color:#666;font-size:12px;">'
        f"Si no te registraste, ignorá este mensaje.</p>"
    )
    return send_email(email_addr, subject, text, html)


def send_reset_email(email_addr: str, token: str) -> dict:
    """Email de password reset."""
    link = f"{app_url()}/#/reset-password?token={token}"
    subject = "Recuperar contraseña — Wealth Management"
    text = (
        f"Recibimos un pedido de recuperación de contraseña para tu cuenta.\n\n"
        f"Si fuiste vos, abrí este link (expira en 1 hora):\n\n  {link}\n\n"
        f"Si no fuiste vos, ignorá este mensaje. Tu contraseña no cambia.\n\n"
        f"— Wealth Management\n"
    )
    html = (
        f"<p>Recibimos un pedido de recuperación de contraseña.</p>"
        f"<p>Si fuiste vos, hacé click acá (expira en 1 hora):</p>"
        f'<p><a href="{link}" style="display:inline-block;padding:12px 24px;'
        f'background:#1F3864;color:#fff;text-decoration:none;border-radius:8px;">'
        f"Cambiar contraseña</a></p>"
        f'<p style="color:#666;font-size:12px;">O copiá este link: <code>{link}</code></p>'
        f'<hr style="border:0;border-top:1px solid #eee">'
        f'<p style="color:#666;font-size:12px;">'
        f"Si no fuiste vos, ignorá este mensaje. Tu contraseña no cambia.</p>"
    )
    return send_email(email_addr, subject, text, html)


def send_welcome_email(email_addr: str, user_id: str, is_superadmin: bool = False) -> dict:
    """Email de bienvenida tras verificar."""
    subject = "Bienvenido a Wealth Management"
    extra = ("\n\nTu cuenta tiene permisos de SUPERADMIN — podés ver y "
             "gestionar todos los users.\n") if is_superadmin else ""
    text = (
        f"¡Bienvenido!\n\n"
        f"Tu user_id es: {user_id}\n"
        f"Email verificado ✓\n"
        f"{extra}\n"
        f"Próximos pasos:\n"
        f"  1. Cargá tus cuentas (banco, broker, wallet)\n"
        f"  2. Configurá credenciales del broker (opcional)\n"
        f"  3. Cargá tu primer trade o saldo inicial\n\n"
        f"Entrá: {app_url()}\n\n"
        f"— Wealth Management\n"
    )
    html = (
        f"<h2>¡Bienvenido!</h2>"
        f"<p>Tu user_id es <b>{user_id}</b>. Email verificado ✓.</p>"
        + (f'<p style="background:#FEF3C7;padding:8px;border-radius:6px;">'
           f"⭐ Tu cuenta tiene permisos de <b>superadmin</b>.</p>"
           if is_superadmin else "")
        + f"<ol><li>Cargá tus cuentas (banco, broker, wallet)</li>"
          f"<li>Configurá credenciales del broker (opcional)</li>"
          f"<li>Cargá tu primer trade o saldo inicial</li></ol>"
        + f'<p><a href="{app_url()}">Entrar a la app</a></p>'
    )
    return send_email(email_addr, subject, text, html)
