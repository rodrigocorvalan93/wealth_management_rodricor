# -*- coding: utf-8 -*-
"""
cli/summary.py

Resumen ejecutivo del portfolio en consola.

USO:
    python -m cli.summary [--fecha YYYY-MM-DD] [--anchor USD|USB|ARS]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.importer import import_all
from engine.holdings import (
    calculate_holdings, total_pn, by_asset_class, by_account, by_currency,
    by_cash_purpose, filter_investible,
)
from engine.pnl import calculate_realized_pnl
from engine.trade_stats import calculate_trade_stats
from engine.buying_power import buying_power_summary


def _fmt_money(value, decimals=2):
    """Formato money con miles separados."""
    if value is None:
        return "-"
    if value == 0:
        return "-"
    return f"{value:,.{decimals}f}"


def _fmt_pct(value):
    if value is None:
        return "-"
    return f"{value*100:+.2f}%"


def print_trade_stats(fills):
    """Sprint A: imprime métricas de trading."""
    if not fills:
        print()
        print("  TRADING METRICS: sin trades cerrados todavía.")
        return
    stats = calculate_trade_stats(fills)
    print()
    print("  TRADING METRICS  (PnL Realizado FIFO)")
    print("  " + "-" * 80)
    print(f"    {'Moneda':<8} {'#Tr':>5} {'Win':>5} {'Loss':>5} {'WR':>7} "
          f"{'Net PnL':>14} {'PF':>7} {'Expect':>10} {'AvgHold(d)':>11}")
    for ccy in sorted(stats.keys()):
        s = stats[ccy]
        pf = "∞" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
        print(f"    {s.currency:<8} {s.n_trades:>5} {s.n_winners:>5} {s.n_losers:>5} "
              f"{s.winrate*100:>6.1f}% {s.net_pnl:>14,.2f} {pf:>7} "
              f"{s.expectancy:>10,.2f} {s.avg_holding_days:>11.1f}")


def print_buying_power(conn, holdings, anchor_ccy):
    """Sprint D: imprime poder de compra por cuenta."""
    summary = buying_power_summary(conn, holdings, anchor_ccy)
    if not summary:
        return
    print()
    print(f"  PODER DE COMPRA POR CUENTA  ({anchor_ccy})")
    print("  " + "-" * 80)
    for item in summary:
        if item["type"] == "BYMA":
            bp = item["result"]
            print(f"    [BYMA] {bp.account:<22} cash {_fmt_money(bp.cash_total):>14} | "
                  f"holdings {_fmt_money(bp.holdings_mv):>14} | "
                  f"BP {_fmt_money(bp.poder_de_compra):>14} ({bp.leverage_ratio:.2f}x)")
        else:
            bp_o = item["overnight"]
            bp_i = item["intraday"]
            print(f"    [MARG] {bp_o.account:<22} equity {_fmt_money(bp_o.equity):>14} | "
                  f"BP-ON {_fmt_money(bp_o.poder_de_compra):>14} (x{bp_o.multiplier:.0f}) | "
                  f"BP-ID {_fmt_money(bp_i.poder_de_compra):>14} (x{bp_i.multiplier:.0f})")
            print(f"           funding {bp_o.funding_rate_annual*100:.2f}%/año "
                  f"({bp_o.funding_currency or '?'}) — costo/día margen máx: "
                  f"{_fmt_money(bp_o.funding_cost_per_day, decimals=4)}")


def print_summary(holdings, anchor_ccy, view_label=None):
    print()
    print("=" * 90)
    title = f"  PORTFOLIO SUMMARY  —  ancla: {anchor_ccy}"
    if view_label:
        title += f"  [{view_label}]"
    print(title.center(90))
    print("=" * 90)

    tp = total_pn(holdings, anchor_ccy)
    print()
    print(f"  PATRIMONIO NETO TOTAL: {_fmt_money(tp['total_anchor']):>20} {anchor_ccy}")
    print(f"  PN INVERTIBLE:         {_fmt_money(tp['total_investible']):>20} {anchor_ccy}")
    if abs(tp['total_non_investible']) > 1e-6:
        print(f"  PN NO-INVERTIBLE:      {_fmt_money(tp['total_non_investible']):>20} {anchor_ccy}  (cash de reserva, etc)")
    if tp["total_unconverted_count"] > 0:
        print(f"  (⚠ {tp['total_unconverted_count']} posiciones sin FX, no incluidas en total)")

    # Por asset class
    print()
    print("  POR ASSET CLASS")
    print("  " + "-" * 60)
    cls = by_asset_class(holdings)
    total = sum(cls.values())
    for c, v in cls.items():
        pct = (v / total * 100) if total else 0
        print(f"    {c:<18} {_fmt_money(v):>20} {anchor_ccy}   {pct:>5.1f}%")

    # Por moneda
    print()
    print("  POR MONEDA NATIVA")
    print("  " + "-" * 60)
    cur = by_currency(holdings)
    total = sum(cur.values())
    for c, v in cur.items():
        pct = (v / total * 100) if total else 0
        print(f"    {c:<18} {_fmt_money(v):>20} {anchor_ccy}   {pct:>5.1f}%")

    # Por cuenta (top 10)
    print()
    print("  POR CUENTA (top 10)")
    print("  " + "-" * 60)
    accs = by_account(holdings)
    total = sum(accs.values())
    for c, v in list(accs.items())[:10]:
        pct = (v / total * 100) if total else 0
        print(f"    {c:<22} {_fmt_money(v):>20} {anchor_ccy}   {pct:>5.1f}%")

    # Top 10 holdings individuales
    print()
    print("  TOP 10 POSICIONES")
    print("  " + "-" * 80)
    print(f"    {'Activo':<22} {'Cuenta':<18} {'Qty':>14} {'Mkt Px':>10} {'MV (' + anchor_ccy + ')':>14}")
    for h in holdings[:10]:
        if h["mv_anchor"] is None:
            continue
        flag = "*" if h["price_fallback"] else " "
        qty = _fmt_money(h["qty"], decimals=4)
        mp = _fmt_money(h["market_price"], decimals=4) if h["market_price"] else "-"
        mv = _fmt_money(h["mv_anchor"])
        print(f"    {h['asset']:<22} {h['account']:<18} {qty:>14} {mp:>10} {mv:>14}{flag}")

    # Holdings con price fallback (advertencia)
    fallbacks = [h for h in holdings if h["price_fallback"] and not h["is_cash"]]
    if fallbacks:
        print()
        print(f"  ⚠ {len(fallbacks)} posiciones con precio fallback (cost basis o último disponible):")
        for h in fallbacks[:10]:
            print(f"    {h['asset']:<22} {h['account']:<18} source: {h['price_source']}, date: {h['price_date']}")

    # No convertibles
    if tp["unconverted"]:
        print()
        print(f"  ⚠ Posiciones sin FX hacia {anchor_ccy}:")
        for asset, ccy, mv in tp["unconverted"]:
            print(f"    {asset:<22} mv: {_fmt_money(mv):>20} {ccy}")

    # Cash por propósito (Sprint B)
    purposes = by_cash_purpose(holdings)
    if len(purposes) > 1:
        print()
        print(f"  CASH POR PROPÓSITO  ({anchor_ccy})")
        print("  " + "-" * 60)
        for p, val in purposes.items():
            print(f"    {p:<28} {_fmt_money(val):>20}")

    print()
    print("=" * 90)
    print(f"  Total filas: {len(holdings)}")
    print()


def main():
    p = argparse.ArgumentParser(description="Resumen del portfolio")
    p.add_argument("--fecha", type=str, default=None,
                   help="Fecha de corte (default: hoy)")
    p.add_argument("--anchor", type=str, default="USD",
                   help="Moneda ancla para valuación (default: USD = CCL)")
    p.add_argument("--xlsx", type=Path,
                   default=Path("inputs/wealth_management_rodricor.xlsx"))
    p.add_argument("--db", type=Path, default=Path("data/wealth.db"))
    p.add_argument("--no-import", action="store_true",
                   help="No re-importar el Excel (usa DB tal como está)")
    p.add_argument("--investible-only", action="store_true",
                   help="Excluye cuentas marcadas como NO-INVERTIBLE de TODOS los breakdowns "
                        "(asset class, moneda, cuenta, top posiciones)")
    p.add_argument("--both", action="store_true",
                   help="Imprime DOS vistas: completa + invertible only")
    args = p.parse_args()

    fecha = date.fromisoformat(args.fecha) if args.fecha else date.today()
    anchor_ccy = args.anchor.upper()

    if not args.no_import:
        if not args.xlsx.is_file():
            print(f"[error] no se encontró {args.xlsx}")
            return 1
        print(f"[summary] importando {args.xlsx}...")
        import_all(str(args.db), str(args.xlsx), fecha)

    if not args.db.is_file():
        print(f"[error] no existe DB: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    print(f"[summary] calculando holdings al {fecha} (ancla: {anchor_ccy})...")
    holdings_all = calculate_holdings(conn, fecha=fecha, anchor_currency=anchor_ccy)
    fills = calculate_realized_pnl(conn, fecha_hasta=fecha)

    # Construir vistas según flags
    if args.both:
        views = [
            (holdings_all, "VISTA COMPLETA (incluye no-invertibles)"),
            (filter_investible(holdings_all), "VISTA SOLO INVERTIBLE (excluye reserva no declarada)"),
        ]
    elif args.investible_only:
        views = [
            (filter_investible(holdings_all),
             "SOLO INVERTIBLE (excluye reserva no declarada)"),
        ]
    else:
        views = [(holdings_all, None)]

    for holdings, label in views:
        print_summary(holdings, anchor_ccy, view_label=label)

    # Trade stats y BP no dependen del filtro (los trades cerrados son cerrados,
    # el BP es por cuenta — siempre se muestran completos)
    print_trade_stats(fills)
    print_buying_power(conn, holdings_all, anchor_ccy)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
