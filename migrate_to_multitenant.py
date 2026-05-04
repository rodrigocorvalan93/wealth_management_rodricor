# -*- coding: utf-8 -*-
"""
migrate_to_multitenant.py

Migra el setup single-tenant existente a la estructura multi-tenant.

Cambios que aplica (idempotente: re-ejecutar es seguro):

  Antes (single-tenant):
    inputs/wealth_management_rodricor.xlsx
    data/wealth.db
    data/excel_backups/

  Después (multi-tenant, default user_id="rodricor"):
    inputs/rodricor/wealth_management.xlsx
    data/rodricor/wealth.db
    data/rodricor/excel_backups/
    data/                         # CSVs de precios siguen acá (compartidos)

USO:
    python migrate_to_multitenant.py                # default user_id "rodricor"
    python migrate_to_multitenant.py --user-id me   # custom
    python migrate_to_multitenant.py --dry-run      # solo muestra qué haría

NOTA: el archivo rename del Excel (.xlsx) preserva el nombre como
`wealth_management.xlsx` (sin sufijo _rodricor). Los backups y CSVs no
se renombran.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Posibles nombres del Excel master legacy
LEGACY_XLSX_NAMES = [
    "wealth_management_rodricor.xlsx",
    "wealth_management.xlsx",
]

# Files inside data/ que son SHARED (no se mueven)
SHARED_DATA_FILES = {
    "fx_historico.csv",
    "precios_historico.csv",
    "precios_cafci.csv",
    "precios_cripto.csv",
    "precios_us.csv",
    "tickers_union.txt",
    ".excel_write.lock",
}

# Files/folders inside data/ que SÍ son del user
USER_DATA_ITEMS = {
    "wealth.db",
    "excel_backups",
}


def find_legacy_xlsx() -> Path:
    """Busca el master legacy. None si no existe (ya migrado o no setup)."""
    for name in LEGACY_XLSX_NAMES:
        f = HERE / "inputs" / name
        if f.is_file():
            return f
    return None


def already_migrated(user_id: str) -> bool:
    """True si ya existe inputs/<user_id>/wealth_management.xlsx."""
    target = HERE / "inputs" / user_id / "wealth_management.xlsx"
    return target.is_file()


def migrate(user_id: str, dry_run: bool = False) -> dict:
    """Aplica la migración. Devuelve un dict con resumen de operaciones."""
    actions = []

    # 1. Crear inputs/<user_id>/ y data/<user_id>/
    inputs_user = HERE / "inputs" / user_id
    data_user = HERE / "data" / user_id
    backups_user = data_user / "excel_backups"

    for d in [inputs_user, data_user, backups_user]:
        if not d.exists():
            actions.append(("mkdir", str(d.relative_to(HERE))))
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)

    # 2. Mover Excel legacy → inputs/<user_id>/wealth_management.xlsx
    legacy_xlsx = find_legacy_xlsx()
    target_xlsx = inputs_user / "wealth_management.xlsx"
    if legacy_xlsx is not None and not target_xlsx.exists():
        actions.append((
            "move_xlsx",
            f"{legacy_xlsx.relative_to(HERE)} → inputs/{user_id}/wealth_management.xlsx",
        ))
        if not dry_run:
            shutil.move(str(legacy_xlsx), str(target_xlsx))
    elif target_xlsx.exists():
        actions.append(("xlsx_already_in_place", str(target_xlsx.relative_to(HERE))))
    else:
        actions.append(("no_legacy_xlsx", "(nada que mover, setup vacío)"))

    # 3. Mover data/wealth.db → data/<user_id>/wealth.db
    legacy_db = HERE / "data" / "wealth.db"
    target_db = data_user / "wealth.db"
    if legacy_db.is_file() and not target_db.exists():
        actions.append(("move_db",
                        f"data/wealth.db → data/{user_id}/wealth.db"))
        if not dry_run:
            shutil.move(str(legacy_db), str(target_db))
    elif target_db.is_file():
        actions.append(("db_already_in_place", str(target_db.relative_to(HERE))))

    # 4. Mover data/excel_backups/ → data/<user_id>/excel_backups/
    legacy_backups = HERE / "data" / "excel_backups"
    if legacy_backups.is_dir():
        # Mover cada archivo dentro de excel_backups al destino
        moved = 0
        for f in legacy_backups.iterdir():
            if not f.is_file():
                continue
            tgt = backups_user / f.name
            if tgt.exists():
                continue
            if not dry_run:
                shutil.move(str(f), str(tgt))
            moved += 1
        if moved > 0:
            actions.append(("move_backups",
                            f"{moved} backup(s) → data/{user_id}/excel_backups/"))
        # Si quedó vacío, removerlo
        if not dry_run:
            try:
                if not any(legacy_backups.iterdir()):
                    legacy_backups.rmdir()
                    actions.append(("rmdir", "data/excel_backups/ (vacío)"))
            except OSError:
                pass

    return {"user_id": user_id, "actions": actions, "dry_run": dry_run}


def main():
    p = argparse.ArgumentParser(description="Migra single-tenant → multi-tenant")
    p.add_argument("--user-id", type=str, default="rodricor",
                   help="user_id a usar para el setup actual (default: rodricor)")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo muestra qué haría, no toca archivos")
    args = p.parse_args()

    user_id = args.user_id.strip().lower()
    if not user_id.replace("_", "").replace("-", "").isalnum():
        print(f"[migrate] user_id inválido: '{user_id}'")
        return 1

    # Detectar si ya está migrado
    if already_migrated(user_id) and not find_legacy_xlsx():
        print(f"[migrate] Setup ya está migrado a multi-tenant para user '{user_id}'.")
        print(f"[migrate] inputs/{user_id}/wealth_management.xlsx existe y no hay legacy.")
        return 0

    print(f"[migrate] Migración a multi-tenant — user_id='{user_id}', "
          f"dry_run={args.dry_run}")
    print("[migrate] Antes:")
    print("  inputs/wealth_management_rodricor.xlsx")
    print("  data/wealth.db")
    print("  data/excel_backups/")
    print(f"[migrate] Después:")
    print(f"  inputs/{user_id}/wealth_management.xlsx")
    print(f"  data/{user_id}/wealth.db")
    print(f"  data/{user_id}/excel_backups/")
    print(f"  data/  (CSVs de precios SIGUEN acá, compartidos)")
    print()

    summary = migrate(user_id, dry_run=args.dry_run)
    print(f"[migrate] {len(summary['actions'])} acciones:")
    for action, detail in summary["actions"]:
        prefix = "  [DRY] " if args.dry_run else "  ✓ "
        print(f"{prefix}{action}: {detail}")

    if not args.dry_run:
        print()
        print("[migrate] OK. Próximos pasos:")
        print(f"  1. Editá tu WSGI file en PythonAnywhere para usar WM_USERS_JSON:")
        print(f'     os.environ[\'WM_USERS_JSON\'] = \'{{"{user_id}": ' \
              f'{{"token":"<tu_token_actual>", "is_admin":true}}}}\'')
        print(f'     os.environ[\'WM_ADMIN_USER\'] = \'{user_id}\'')
        print(f"  2. Reload del web app en PA.")
        print(f"  3. Tu token actual sigue funcionando — el back-compat con")
        print(f"     WM_API_TOKEN sigue activo si no setea WM_USERS_JSON.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
