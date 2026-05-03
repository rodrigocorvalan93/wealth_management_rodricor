# -*- coding: utf-8 -*-
"""
cli/tarjetas.py

Imprime en terminal las 3 vistas de saldo de cada tarjeta de crédito.

USO:
    python3 -m cli.tarjetas
    python3 -m cli.tarjetas --xlsx inputs/wealth_management_rodricor.xlsx
    python3 -m cli.tarjetas --fecha 2026-05-15
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Permitir import desde la carpeta padre
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.importer import import_all
from engine.liabilities import all_card_snapshots
from engine.schema import init_db


def fmt_money(amt: float, currency: str) -> str:
    """Formato monetario con 2 decimales y separadores."""
    if amt == 0:
        return f"{currency} -"
    sign = "-" if amt < 0 else " "
    return f"{currency} {sign}{abs(amt):,.2f}"


def main():
    p = argparse.ArgumentParser(description="Vista de tarjetas de crédito")
    p.add_argument("--xlsx", type=Path,
                   default=Path("inputs/wealth_management_rodricor.xlsx"),
                   help="Path al Excel master")
    p.add_argument("--db", type=Path, default=Path("data/wealth.db"),
                   help="Path al sqlite (default: data/wealth.db)")
    p.add_argument("--fecha", type=str, default=None,
                   help="Fecha de referencia (default: hoy)")
    p.add_argument("--skip-import", action="store_true",
                   help="No re-importar Excel (usa la DB existente)")
    args = p.parse_args()

    ref_date = date.fromisoformat(args.fecha) if args.fecha else date.today()

    # Importar el Excel a la DB (idempotente: drop & recreate)
    if not args.skip_import:
        if not args.xlsx.is_file():
            print(f"[error] no se encontró {args.xlsx}", file=sys.stderr)
            return 1
        print(f"[import] {args.xlsx} → {args.db}")
        stats = import_all(args.db, args.xlsx, fecha_corte=ref_date)
        print(f"[import] estadísticas:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print()

    # Conectar y leer
    import sqlite3
    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    snapshots = all_card_snapshots(conn, ref_date)

    if not snapshots:
        print("No hay tarjetas de crédito configuradas en la hoja 'cuentas'.")
        return 0

    print(f"{'='*78}")
    print(f"TARJETAS DE CRÉDITO  —  fecha de referencia: {ref_date}")
    print(f"{'='*78}")
    print()

    for s in snapshots:
        print(f"┌─ {s.card_name} ({s.card_code})  [{s.currency}]")
        print(f"│")
        print(f"│  Saldo actual (todo lo cargado, no pagado):")
        print(f"│      {fmt_money(s.saldo_actual, s.currency)}")
        print(f"│")
        if s.fecha_ultimo_cierre:
            print(f"│  Último resumen cerrado ({s.fecha_ultimo_cierre}):")
            print(f"│      {fmt_money(s.saldo_ultimo_resumen, s.currency)}")
            print(f"│")
        if s.fecha_proximo_cierre:
            print(f"│  Próximo vencimiento (cierre {s.fecha_proximo_cierre}, "
                  f"vto {s.fecha_proximo_vto or '?'}):")
            print(f"│      {fmt_money(s.saldo_proximo_vto, s.currency)}")
        print(f"└─")
        print()

    # Total acumulado por moneda (sumando saldos actuales)
    by_currency = {}
    for s in snapshots:
        by_currency.setdefault(s.currency, 0)
        by_currency[s.currency] += s.saldo_actual

    if len(by_currency) > 0:
        print(f"{'─'*78}")
        print(f"TOTAL DEUDA EN TARJETAS (saldo actual):")
        for cur_, total in by_currency.items():
            print(f"  {fmt_money(total, cur_)}")
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
