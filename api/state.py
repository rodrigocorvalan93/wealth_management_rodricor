# -*- coding: utf-8 -*-
"""
api/state.py

Estado global y configuración del backend.

Variables de entorno (las podés setear en PythonAnywhere → Web → Environment):

  WM_API_TOKEN     — bearer token de auth (REQUERIDO)
  WM_BASE_DIR      — directorio raíz (default: cwd)
  WM_XLSX_PATH     — path al Excel master
                     (default: $WM_BASE_DIR/inputs/wealth_management_rodricor.xlsx)
  WM_DB_PATH       — path a la DB sqlite
                     (default: $WM_BASE_DIR/data/wealth.db)
  WM_DATA_DIR      — directorio con CSVs de precios y backups
                     (default: $WM_BASE_DIR/data)
  WM_BACKUPS_DIR   — directorio para backups del Excel antes de cada write
                     (default: $WM_DATA_DIR/excel_backups)
  WM_ANCHOR        — moneda ancla default (default: USD)
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False  # Windows


# =============================================================================
# Config
# =============================================================================

class Settings:
    def __init__(self):
        self.base_dir = Path(os.environ.get("WM_BASE_DIR", ".")).resolve()
        self.xlsx_path = Path(
            os.environ.get("WM_XLSX_PATH",
                            self.base_dir / "inputs" / "wealth_management_rodricor.xlsx")
        )
        self.db_path = Path(
            os.environ.get("WM_DB_PATH", self.base_dir / "data" / "wealth.db")
        )
        self.data_dir = Path(
            os.environ.get("WM_DATA_DIR", self.base_dir / "data")
        )
        self.backups_dir = Path(
            os.environ.get("WM_BACKUPS_DIR", self.data_dir / "excel_backups")
        )
        self.anchor = os.environ.get("WM_ANCHOR", "USD").upper()
        self.api_token = os.environ.get("WM_API_TOKEN", "")
        self.lock_path = self.data_dir / ".excel_write.lock"
        # Asegurar dirs
        for d in (self.data_dir, self.backups_dir,
                  self.xlsx_path.parent, self.db_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def __repr__(self):
        return (f"Settings(base={self.base_dir}, xlsx={self.xlsx_path}, "
                f"db={self.db_path}, anchor={self.anchor})")


_settings: Settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings():
    """Útil para tests."""
    global _settings
    _settings = None


# =============================================================================
# Excel write lock (single-writer)
# =============================================================================

@contextmanager
def excel_write_lock():
    """Lock exclusivo sobre el Excel master para evitar writes concurrentes.

    En POSIX usa fcntl.flock; en Windows degrada a no-op (PythonAnywhere = Linux,
    así que en producción siempre tiene lock real).
    """
    s = get_settings()
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

def backup_excel():
    """Hace una copia del Excel master con timestamp. Devuelve el path."""
    import shutil
    s = get_settings()
    if not s.xlsx_path.is_file():
        return None
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    backup = s.backups_dir / f"{s.xlsx_path.stem}.backup-{ts}{s.xlsx_path.suffix}"
    shutil.copy2(s.xlsx_path, backup)
    return backup


def list_backups(limit: int = 20):
    """Devuelve los últimos N backups (más recientes primero)."""
    s = get_settings()
    if not s.backups_dir.is_dir():
        return []
    backups = sorted(
        (p for p in s.backups_dir.iterdir() if p.is_file() and p.suffix == ".xlsx"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return backups[:limit]


def prune_backups(keep_last: int = 30):
    """Borra backups viejos, deja los últimos N."""
    backups = list_backups(limit=10000)
    for b in backups[keep_last:]:
        try:
            b.unlink()
        except OSError:
            pass


# =============================================================================
# DB helpers
# =============================================================================

def db_conn() -> sqlite3.Connection:
    """Abre conexión a la DB del engine."""
    s = get_settings()
    conn = sqlite3.connect(str(s.db_path))
    conn.row_factory = sqlite3.Row
    return conn


def reimport_excel(fecha_corte=None):
    """Re-importa el Excel master en la DB (drop + recreate).

    Devuelve stats. Llamarlo después de cada mutación al Excel.
    """
    from datetime import date as _date
    from engine.importer import import_all
    s = get_settings()
    if fecha_corte is None:
        fecha_corte = _date.today()
    return import_all(str(s.db_path), str(s.xlsx_path), fecha_corte=fecha_corte)
