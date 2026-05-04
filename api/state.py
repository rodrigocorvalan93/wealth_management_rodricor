# -*- coding: utf-8 -*-
"""
api/state.py

Estado y configuración del backend, MULTI-TENANT.

Variables de entorno:

  WM_BASE_DIR       directorio raíz (default: cwd)
  WM_USERS_JSON     JSON con users + tokens (multi-tenant). Ver api/users.py.
  WM_API_TOKEN      legacy single-tenant (back-compat). Si se usa,
                    user_id="default".
  WM_ADMIN_USER     id del user admin (multi-tenant). Default: el primer user.
  WM_ANCHOR         moneda ancla default (default: USD)

  WM_XLSX_PATH      override del path al Excel master (raro, solo single-tenant)
  WM_DB_PATH        override del path a la DB sqlite
  WM_DATA_DIR       override del directorio de CSVs y backups

Layout en filesystem (multi-tenant):
  $WM_BASE_DIR/
    inputs/
      <user_id>/wealth_management.xlsx
    data/
      fx_historico.csv               # SHARED — un solo set de precios
      precios_historico.csv          #          para todos los users
      precios_cafci.csv
      precios_cripto.csv
      precios_us.csv
      tickers_union.txt              # auto-generado por sync.py
      <user_id>/
        wealth.db                    # DB por user
        excel_backups/               # backups del master del user
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False  # Windows


# =============================================================================
# Config per-user
# =============================================================================

DEFAULT_USER_ID = "default"


class Settings:
    """Settings asociados a un user_id particular.

    Cada user tiene paths separados:
      inputs/<user_id>/wealth_management.xlsx
      data/<user_id>/wealth.db
      data/<user_id>/excel_backups/

    Los CSVs de precios y FX viven en data/ (compartidos).
    """

    def __init__(self, user_id: str = DEFAULT_USER_ID):
        self.user_id = user_id
        self.base_dir = Path(os.environ.get("WM_BASE_DIR", ".")).resolve()

        # Layout multi-tenant
        self.inputs_dir = self.base_dir / "inputs" / user_id
        self.user_data_dir = self.base_dir / "data" / user_id
        self.shared_data_dir = self.base_dir / "data"

        # Paths del user
        self.xlsx_path = self.inputs_dir / "wealth_management.xlsx"
        self.db_path = self.user_data_dir / "wealth.db"
        self.backups_dir = self.user_data_dir / "excel_backups"
        self.lock_path = self.user_data_dir / ".excel_write.lock"

        # Para data dir, el motor lee CSVs de aquí (compartido)
        self.data_dir = self.shared_data_dir

        # === Back-compat single-tenant ===
        # Si user_id == "default" y hay overrides legacy, usarlos.
        if user_id == DEFAULT_USER_ID:
            legacy_xlsx = os.environ.get("WM_XLSX_PATH")
            legacy_db = os.environ.get("WM_DB_PATH")
            legacy_data = os.environ.get("WM_DATA_DIR")
            legacy_backups = os.environ.get("WM_BACKUPS_DIR")
            if legacy_xlsx:
                self.xlsx_path = Path(legacy_xlsx)
                self.inputs_dir = self.xlsx_path.parent
            if legacy_db:
                self.db_path = Path(legacy_db)
                self.user_data_dir = self.db_path.parent
            if legacy_data:
                self.data_dir = Path(legacy_data)
                self.shared_data_dir = self.data_dir
            if legacy_backups:
                self.backups_dir = Path(legacy_backups)
            else:
                self.backups_dir = self.user_data_dir / "excel_backups"
            self.lock_path = self.user_data_dir / ".excel_write.lock"

        self.anchor = os.environ.get("WM_ANCHOR", "USD").upper()
        self.api_token = os.environ.get("WM_API_TOKEN", "")  # back-compat

        # Asegurar dirs
        for d in (self.inputs_dir, self.user_data_dir, self.shared_data_dir,
                  self.backups_dir, self.xlsx_path.parent, self.db_path.parent):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def __repr__(self):
        return (f"Settings(user={self.user_id}, base={self.base_dir}, "
                f"xlsx={self.xlsx_path}, db={self.db_path})")


# Cache de Settings por user_id
_user_settings: dict[str, Settings] = {}


def get_user_settings(user_id: str) -> Settings:
    """Devuelve Settings para el user_id dado, cacheando."""
    if user_id not in _user_settings:
        _user_settings[user_id] = Settings(user_id=user_id)
    return _user_settings[user_id]


def get_settings() -> Settings:
    """Devuelve Settings para el user activo del request actual.

    Si está en un Flask request context, lee `g.active_user_id`.
    Fuera de request (CLI, tests, scripts), usa DEFAULT_USER_ID.
    """
    user_id = DEFAULT_USER_ID
    try:
        from flask import g, has_request_context
        if has_request_context():
            user_id = getattr(g, "active_user_id", None) or DEFAULT_USER_ID
    except (ImportError, RuntimeError):
        pass
    return get_user_settings(user_id)


def reset_settings():
    """Útil para tests — limpia el cache de settings."""
    global _user_settings
    _user_settings = {}


def list_user_ids() -> list[str]:
    """Lista user_ids con folder existente en disk (no solo en config)."""
    base = Path(os.environ.get("WM_BASE_DIR", ".")).resolve()
    inputs = base / "inputs"
    if not inputs.is_dir():
        return []
    return sorted(
        d.name for d in inputs.iterdir()
        if d.is_dir() and (d / "wealth_management.xlsx").is_file()
    )


# =============================================================================
# Excel write lock (single-writer per user)
# =============================================================================

@contextmanager
def excel_write_lock(settings: Optional[Settings] = None):
    """Lock exclusivo sobre el Excel master del user activo.

    Cada user tiene su propio lockfile, así que dos users pueden escribir
    en paralelo sin bloquearse mutuamente.
    """
    s = settings or get_settings()
    s.lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not HAS_FCNTL:
        yield
        return
    f = open(s.lock_path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


# =============================================================================
# Excel backups (antes de cada write)
# =============================================================================

def backup_excel(settings: Optional[Settings] = None):
    """Hace una copia del Excel master del user activo con timestamp."""
    import shutil
    s = settings or get_settings()
    if not s.xlsx_path.is_file():
        return None
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    backup = s.backups_dir / f"{s.xlsx_path.stem}.backup-{ts}{s.xlsx_path.suffix}"
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s.xlsx_path, backup)
    return backup


def list_backups(limit: int = 20, settings: Optional[Settings] = None):
    """Devuelve los últimos N backups del user activo (recientes primero)."""
    s = settings or get_settings()
    if not s.backups_dir.is_dir():
        return []
    backups = sorted(
        (p for p in s.backups_dir.iterdir() if p.is_file() and p.suffix == ".xlsx"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return backups[:limit]


def prune_backups(keep_last: int = 30, settings: Optional[Settings] = None):
    """Borra backups viejos del user activo, deja los últimos N."""
    backups = list_backups(limit=10000, settings=settings)
    for b in backups[keep_last:]:
        try:
            b.unlink()
        except OSError:
            pass


# =============================================================================
# DB helpers
# =============================================================================

def db_conn(settings: Optional[Settings] = None) -> sqlite3.Connection:
    """Abre conexión a la DB del user activo."""
    s = settings or get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(s.db_path))
    conn.row_factory = sqlite3.Row
    return conn


def reimport_excel(fecha_corte=None, settings: Optional[Settings] = None):
    """Re-importa el Excel master del user activo en su DB."""
    from datetime import date as _date
    from engine.importer import import_all
    s = settings or get_settings()
    if fecha_corte is None:
        fecha_corte = _date.today()
    return import_all(str(s.db_path), str(s.xlsx_path),
                      fecha_corte=fecha_corte,
                      data_dir=str(s.data_dir))
