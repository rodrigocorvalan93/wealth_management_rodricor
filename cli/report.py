# -*- coding: utf-8 -*-
"""
cli/report.py

Wrapper CLI para generar reportes Excel y/o HTML del portfolio.

USO:
    python -m cli.report --xlsx                # genera Excel
    python -m cli.report --html                # genera HTML
    python -m cli.report --xlsx --html         # genera ambos
    python -m cli.report --xlsx --fecha 2026-04-30
    python -m cli.report --xlsx --anchor USB

OUTPUT:
    reports/{fecha}_portfolio.xlsx
    reports/{fecha}_portfolio.html
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.importer import import_all
from engine.exporter import export_excel, export_html


def main():
    p = argparse.ArgumentParser(description="Generador de reportes de portfolio")
    p.add_argument("--xlsx", action="store_true",
                   help="Generar reporte Excel multi-sheet")
    p.add_argument("--html", action="store_true",
                   help="Generar reporte HTML autocontenido")
    p.add_argument("--fecha", type=str, default=None,
                   help="Fecha de corte (default: hoy)")
    p.add_argument("--anchor", type=str, default="USD",
                   help="Moneda ancla del reporte (default: USD = CCL)")
    p.add_argument("--xlsx-input", type=Path,
                   default=Path("inputs/wealth_management_rodricor.xlsx"),
                   help="Excel master de input")
    p.add_argument("--db", type=Path, default=Path("data/wealth.db"))
    p.add_argument("--output-dir", type=Path, default=Path("reports"),
                   help="Carpeta destino (default: reports/)")
    p.add_argument("--no-import", action="store_true",
                   help="No re-importar el Excel (usa DB tal como está)")
    args = p.parse_args()

    if not args.xlsx and not args.html:
        print("[error] tenés que pedir --xlsx o --html (o ambos)")
        return 1

    fecha = date.fromisoformat(args.fecha) if args.fecha else date.today()
    anchor = args.anchor.upper()

    # Re-importar si corresponde
    if not args.no_import:
        if not args.xlsx_input.is_file():
            print(f"[error] no se encontró {args.xlsx_input}")
            return 1
        print(f"[report] importando {args.xlsx_input}...")
        import_all(str(args.db), str(args.xlsx_input), fecha)

    if not args.db.is_file():
        print(f"[error] no existe DB: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    args.output_dir.mkdir(parents=True, exist_ok=True)

    fecha_str = fecha.isoformat()

    if args.xlsx:
        out_path = args.output_dir / f"{fecha_str}_portfolio.xlsx"
        print(f"[report] generando Excel...")
        result = export_excel(conn, out_path, fecha=fecha, anchor_currency=anchor)
        print(f"[report] OK Excel → {result}")

    if args.html:
        out_path = args.output_dir / f"{fecha_str}_portfolio.html"
        print(f"[report] generando HTML...")
        result = export_html(conn, out_path, fecha=fecha, anchor_currency=anchor)
        print(f"[report] OK HTML  → {result}")

    print()
    print(f"[done] reportes generados en {args.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
