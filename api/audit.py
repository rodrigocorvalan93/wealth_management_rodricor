# -*- coding: utf-8 -*-
"""
api/audit.py

Audit log per-user. Registra cada mutation (POST/PUT/DELETE) en
<user_data_dir>/audit.log como JSONL append-only.

Esto cumple dos objetivos:
  1. Trazabilidad: si algo se rompe, podés revisar qué requests
     mutaron data y reproducir el estado.
  2. Compliance: requisito básico para Argentina (Ley 25.326) y para
     que el user pueda ver "quién tocó mi cuenta y cuándo".

Cada entry es una línea JSON con:
  ts, ip, user_id, auth_user_id, method, path, status,
  body_hash (SHA256 truncado a 12 hex), is_admin_switch
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def _audit_path(user_id: str) -> Path:
    from .state import get_user_settings
    s = get_user_settings(user_id)
    s.user_data_dir.mkdir(parents=True, exist_ok=True)
    return s.user_data_dir / "audit.log"


def log(user_id: str, entry: dict) -> None:
    """Append entry as JSONL. Best-effort: errores no rompen request."""
    try:
        path = _audit_path(user_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # rotate after 5 MB para no llenar el disk
        try:
            if path.stat().st_size > 5 * 1024 * 1024:
                rotated = path.with_suffix(
                    f".log.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                )
                path.rename(rotated)
        except OSError:
            pass
    except OSError as e:
        print(f"[audit] WARN no pude escribir log: {e}")


def hash_body(body) -> Optional[str]:
    """SHA256 truncado del body JSON (para no almacenar PII en logs)."""
    if not body:
        return None
    try:
        s = json.dumps(body, sort_keys=True, default=str)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]
    except (TypeError, ValueError):
        return None


def tail(user_id: str, n: int = 100) -> list[dict]:
    """Lee las últimas N entradas del audit log del user."""
    path = _audit_path(user_id)
    if not path.is_file():
        return []
    try:
        # Lee el archivo entero — para audit logs típicos (<5MB) es OK.
        # Si crece, paginamos en disk con tail real.
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []
