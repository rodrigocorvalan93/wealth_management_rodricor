# -*- coding: utf-8 -*-
"""
engine/exporter.py

Generador de reportes Excel multi-sheet y HTML autocontenido del portfolio.

USO:
    from engine.exporter import export_excel, export_html

    export_excel(conn, "reports/2026-05-03.xlsx", fecha=date(2026, 5, 3))
    export_html(conn, "reports/2026-05-03.html", fecha=date(2026, 5, 3))

HOJAS DEL EXCEL:
    1. Dashboard
    2. Holdings
    3. PN por cuenta
    4. PN por asset class
    5. Cash Position
    6. Tarjetas
    7. PnL Realizado FIFO
    8. PnL No-Realizado
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .holdings import (
    calculate_holdings, total_pn,
    by_asset_class, by_account, by_currency,
)
from .pnl import (
    calculate_realized_pnl, aggregate_pnl_by_asset,
    aggregate_pnl_by_year, aggregate_pnl_by_year_currency,
    total_realized_pnl,
    calculate_unrealized_pnl_summary,
)
from .liabilities import all_card_snapshots


# =============================================================================
# Estilos Excel
# =============================================================================

NAVY = "1F3864"
LIGHT_NAVY = "2E5B9C"
WHITE = "FFFFFF"
LIGHT_GRAY = "F2F2F2"
GREEN = "00B050"
RED = "C00000"

FONT_TITLE = Font(name="Arial", size=18, bold=True, color=NAVY)
FONT_SUBTITLE = Font(name="Arial", size=11, italic=True, color="595959")
FONT_HEADER = Font(name="Arial", size=11, bold=True, color=WHITE)
FONT_NORMAL = Font(name="Arial", size=10)
FONT_BOLD = Font(name="Arial", size=10, bold=True)
FONT_KPI = Font(name="Arial", size=22, bold=True, color=NAVY)
FONT_KPI_LABEL = Font(name="Arial", size=10, color="595959")

FILL_HEADER = PatternFill("solid", fgColor=NAVY)
FILL_SUBTOTAL = PatternFill("solid", fgColor=LIGHT_GRAY)

ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_LEFT = Alignment(horizontal="left", vertical="center")

THIN = Side(style="thin", color="BFBFBF")
BORDER_THIN = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

FMT_NUMBER = '#,##0.00;[Red](#,##0.00);"-"'
FMT_NUMBER4 = '#,##0.0000;[Red](#,##0.0000);"-"'
FMT_PCT = '0.00%;[Red](0.00%);"-"'
FMT_INT = '#,##0;[Red](#,##0);"-"'
FMT_DATE = "yyyy-mm-dd"


# =============================================================================
# Helpers
# =============================================================================

def _set_col_widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_headers(ws, row, headers, widths=None):
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=c, value=h)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.alignment = ALIGN_CENTER
        cell.border = BORDER_THIN
    if widths:
        _set_col_widths(ws, widths)


def _write_row(ws, row, values, formats=None, bold=False):
    formats = formats or [None] * len(values)
    for c, (val, fmt) in enumerate(zip(values, formats), start=1):
        cell = ws.cell(row=row, column=c, value=val)
        cell.font = FONT_BOLD if bold else FONT_NORMAL
        cell.border = BORDER_THIN
        if fmt:
            cell.number_format = fmt
        if isinstance(val, (int, float)):
            cell.alignment = ALIGN_RIGHT


# =============================================================================
# Sheets
# =============================================================================

def _sheet_dashboard(wb, holdings, anchor_ccy, fecha):
    ws = wb.create_sheet("Dashboard", 0)

    # Título
    ws.cell(row=1, column=1, value=f"PORTFOLIO  —  {fecha.isoformat()}").font = FONT_TITLE
    ws.cell(row=2, column=1, value=f"Moneda ancla: {anchor_ccy}").font = FONT_SUBTITLE
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=8)

    # KPI: PN total
    tp = total_pn(holdings, anchor_ccy)
    ws.cell(row=4, column=1, value="PATRIMONIO NETO TOTAL").font = FONT_KPI_LABEL
    cell = ws.cell(row=5, column=1, value=tp["total_anchor"])
    cell.font = FONT_KPI
    cell.number_format = FMT_NUMBER
    ws.cell(row=6, column=1, value=anchor_ccy).font = FONT_SUBTITLE

    if tp["total_unconverted_count"] > 0:
        ws.cell(row=7, column=1,
                value=f"⚠ {tp['total_unconverted_count']} posiciones sin FX, no incluidas").font = FONT_SUBTITLE

    # PN por asset class
    ws.cell(row=9, column=1, value="POR ASSET CLASS").font = FONT_BOLD
    _write_headers(ws, 10, ["Clase", f"MV ({anchor_ccy})", "% Total"])
    cls_data = by_asset_class(holdings)
    cls_total = sum(cls_data.values())
    row = 11
    for cls, val in cls_data.items():
        pct = (val / cls_total) if cls_total else 0
        _write_row(ws, row, [cls, val, pct], formats=[None, FMT_NUMBER, FMT_PCT])
        row += 1

    # PN por moneda nativa (a la derecha)
    ws.cell(row=9, column=5, value="POR MONEDA NATIVA").font = FONT_BOLD
    _write_headers(ws, 10, ["Moneda", f"MV ({anchor_ccy})", "% Total"])
    # Reescribir headers en columnas 5-7
    for c, h in enumerate(["Moneda", f"MV ({anchor_ccy})", "% Total"], start=5):
        cell = ws.cell(row=10, column=c, value=h)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.alignment = ALIGN_CENTER
        cell.border = BORDER_THIN

    cur_data = by_currency(holdings)
    cur_total = sum(cur_data.values())
    row = 11
    for ccy, val in cur_data.items():
        pct = (val / cur_total) if cur_total else 0
        for c, (v, fmt) in enumerate(zip([ccy, val, pct], [None, FMT_NUMBER, FMT_PCT]), start=5):
            cell = ws.cell(row=row, column=c, value=v)
            cell.font = FONT_NORMAL
            cell.border = BORDER_THIN
            if fmt: cell.number_format = fmt
            if isinstance(v, (int, float)): cell.alignment = ALIGN_RIGHT
        row += 1

    # Top 10 cuentas
    next_row = max(11 + len(cls_data), 11 + len(cur_data)) + 2
    ws.cell(row=next_row, column=1, value="TOP CUENTAS").font = FONT_BOLD
    _write_headers(ws, next_row + 1, ["Cuenta", f"MV ({anchor_ccy})", "% Total"])
    accs = by_account(holdings)
    accs_total = sum(accs.values())
    row = next_row + 2
    for acc, val in list(accs.items())[:10]:
        pct = (val / accs_total) if accs_total else 0
        _write_row(ws, row, [acc, val, pct], formats=[None, FMT_NUMBER, FMT_PCT])
        row += 1

    _set_col_widths(ws, [22, 18, 12, 4, 22, 18, 12, 4])
    ws.row_dimensions[5].height = 35


def _sheet_holdings(wb, holdings, anchor_ccy):
    ws = wb.create_sheet("Holdings")

    headers = [
        "Cuenta", "Activo", "Asset Class", "Qty",
        "Avg Cost", "Mkt Price", "Native Ccy",
        "MV (Native)", f"MV ({anchor_ccy})",
        "Unrealized PnL (Native)", "Unrealized %",
        "Price Source", "Price Date", "Fallback",
    ]
    widths = [22, 22, 14, 14, 12, 12, 9, 16, 16, 18, 12, 14, 12, 9]
    _write_headers(ws, 1, headers, widths)

    for i, h in enumerate(holdings, start=2):
        _write_row(ws, i, [
            h["account"],
            h["asset"],
            h["asset_class"] or "",
            h["qty"],
            h["avg_cost"],
            h["market_price"],
            h["native_currency"],
            h["mv_native"],
            h["mv_anchor"],
            h["unrealized_pnl_native"],
            h["unrealized_pct"],
            h["price_source"] or "",
            h["price_date"] or "",
            "Sí" if h["price_fallback"] else "",
        ], formats=[
            None, None, None, FMT_NUMBER4,
            FMT_NUMBER4, FMT_NUMBER4, None,
            FMT_NUMBER, FMT_NUMBER,
            FMT_NUMBER, FMT_PCT,
            None, None, None,
        ])

    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(holdings) + 1}"


def _sheet_pn_by_account(wb, holdings, anchor_ccy):
    ws = wb.create_sheet("PN por cuenta")

    headers = ["Cuenta", f"MV ({anchor_ccy})", "% Total", "# Posiciones"]
    widths = [25, 18, 12, 14]
    _write_headers(ws, 1, headers, widths)

    accs = by_account(holdings)
    total = sum(accs.values())
    counts = defaultdict(int)
    for h in holdings:
        if h["mv_anchor"] is not None:
            counts[h["account"]] += 1

    for i, (acc, val) in enumerate(accs.items(), start=2):
        pct = (val / total) if total else 0
        _write_row(ws, i, [acc, val, pct, counts[acc]],
                   formats=[None, FMT_NUMBER, FMT_PCT, FMT_INT])

    # Total
    row = len(accs) + 2
    _write_row(ws, row, ["TOTAL", total, 1.0, sum(counts.values())],
               formats=[None, FMT_NUMBER, FMT_PCT, FMT_INT], bold=True)
    for c in range(1, 5):
        ws.cell(row=row, column=c).fill = FILL_SUBTOTAL


def _sheet_pn_by_class(wb, holdings, anchor_ccy):
    ws = wb.create_sheet("PN por asset class")

    headers = ["Asset Class", f"MV ({anchor_ccy})", "% Total", "# Posiciones"]
    widths = [22, 18, 12, 14]
    _write_headers(ws, 1, headers, widths)

    cls = by_asset_class(holdings)
    total = sum(cls.values())
    counts = defaultdict(int)
    for h in holdings:
        if h["mv_anchor"] is not None:
            counts[h["asset_class"] or "UNKNOWN"] += 1

    for i, (c_name, val) in enumerate(cls.items(), start=2):
        pct = (val / total) if total else 0
        _write_row(ws, i, [c_name, val, pct, counts[c_name]],
                   formats=[None, FMT_NUMBER, FMT_PCT, FMT_INT])

    row = len(cls) + 2
    _write_row(ws, row, ["TOTAL", total, 1.0, sum(counts.values())],
               formats=[None, FMT_NUMBER, FMT_PCT, FMT_INT], bold=True)
    for c in range(1, 5):
        ws.cell(row=row, column=c).fill = FILL_SUBTOTAL


def _sheet_cash_position(wb, holdings, anchor_ccy):
    ws = wb.create_sheet("Cash Position")

    headers = ["Cuenta", "Moneda", "Qty", f"MV ({anchor_ccy})"]
    widths = [25, 10, 18, 18]
    _write_headers(ws, 1, headers, widths)

    cash_only = [h for h in holdings if h["is_cash"]]
    cash_only.sort(key=lambda h: -(h["mv_anchor"] or 0))

    for i, h in enumerate(cash_only, start=2):
        _write_row(ws, i, [
            h["account"],
            h["native_currency"],
            h["qty"],
            h["mv_anchor"],
        ], formats=[None, None, FMT_NUMBER, FMT_NUMBER])

    # Subtotales por moneda
    by_ccy = defaultdict(float)
    for h in cash_only:
        if h["mv_anchor"] is not None:
            by_ccy[h["native_currency"]] += h["mv_anchor"]

    row = len(cash_only) + 3
    ws.cell(row=row, column=1, value="POR MONEDA").font = FONT_BOLD
    row += 1
    _write_headers(ws, row, ["Moneda", f"Total ({anchor_ccy})"])
    for ccy, val in sorted(by_ccy.items(), key=lambda kv: -kv[1]):
        row += 1
        _write_row(ws, row, [ccy, val], formats=[None, FMT_NUMBER])


def _sheet_tarjetas(wb, conn, fecha):
    ws = wb.create_sheet("Tarjetas")

    cards = all_card_snapshots(conn, fecha)

    headers = [
        "Tarjeta", "Code", "Moneda",
        "Saldo Actual", "Último Resumen", "Fecha Último Cierre",
        "Próximo Vencimiento", "Fecha Próximo Cierre", "Fecha Próximo Vto",
    ]
    widths = [25, 24, 8, 16, 16, 18, 18, 18, 18]
    _write_headers(ws, 1, headers, widths)

    for i, c in enumerate(cards, start=2):
        _write_row(ws, i, [
            c.card_name,
            c.card_code,
            c.currency,
            c.saldo_actual,
            c.saldo_ultimo_resumen,
            c.fecha_ultimo_cierre or "",
            c.saldo_proximo_vto,
            c.fecha_proximo_cierre or "",
            c.fecha_proximo_vto or "",
        ], formats=[
            None, None, None,
            FMT_NUMBER, FMT_NUMBER, None,
            FMT_NUMBER, None, None,
        ])

    # Totales por moneda
    if cards:
        from collections import defaultdict
        totals = defaultdict(float)
        for c in cards:
            totals[c.currency] += c.saldo_actual

        row = len(cards) + 3
        ws.cell(row=row, column=1, value="TOTAL POR MONEDA").font = FONT_BOLD
        row += 1
        for ccy, val in totals.items():
            ws.cell(row=row, column=1, value=ccy).font = FONT_BOLD
            cell = ws.cell(row=row, column=4, value=val)
            cell.font = FONT_BOLD
            cell.number_format = FMT_NUMBER
            row += 1


def _sheet_pnl_realizado(wb, fills, anchor_ccy):
    ws = wb.create_sheet("PnL Realizado FIFO")

    if not fills:
        ws.cell(row=1, column=1, value="No hay PnL realizado todavía.").font = FONT_SUBTITLE
        return

    headers = [
        "Fecha Venta", "Fecha Compra", "Holding (días)",
        "Cuenta", "Activo", "Qty",
        "Precio Compra", "Precio Venta", "Moneda",
        "PnL Realizado", "PnL %",
    ]
    widths = [12, 12, 12, 22, 22, 14, 14, 14, 8, 16, 10]
    _write_headers(ws, 1, headers, widths)

    for i, f in enumerate(fills, start=2):
        _write_row(ws, i, [
            f.fecha_venta,
            f.fecha_compra,
            f.holding_period_days,
            f.account,
            f.asset,
            f.qty,
            f.precio_compra,
            f.precio_venta,
            f.currency,
            f.pnl_realizado,
            f.pnl_pct,
        ], formats=[
            None, None, FMT_INT,
            None, None, FMT_NUMBER4,
            FMT_NUMBER4, FMT_NUMBER4, None,
            FMT_NUMBER, FMT_PCT,
        ])

    # Total al final
    row = len(fills) + 3
    ws.cell(row=row, column=1, value="TOTAL POR MONEDA").font = FONT_BOLD
    row += 1
    totals = total_realized_pnl(fills)
    for ccy, val in totals.items():
        ws.cell(row=row, column=1, value=ccy).font = FONT_BOLD
        cell = ws.cell(row=row, column=2, value=val)
        cell.font = FONT_BOLD
        cell.number_format = FMT_NUMBER
        row += 1

    ws.freeze_panes = "A2"


def _sheet_pnl_no_realizado(wb, holdings, anchor_ccy):
    ws = wb.create_sheet("PnL No-Realizado")

    summary = calculate_unrealized_pnl_summary(holdings)

    ws.cell(row=1, column=1, value="UNREALIZED PnL TOTAL").font = FONT_BOLD
    ws.cell(row=2, column=1, value=summary["total_unrealized_anchor"]).number_format = FMT_NUMBER
    ws.cell(row=3, column=1, value=anchor_ccy).font = FONT_SUBTITLE

    ws.cell(row=1, column=4, value=f"{summary['n_winners']} ganadoras / {summary['n_losers']} perdedoras").font = FONT_SUBTITLE

    headers = [
        "Cuenta", "Activo", "Qty",
        "Avg Cost", "Mkt Price", "Native Ccy",
        "Cost Basis", "Market Value",
        "Unrealized PnL", "Unrealized %",
    ]
    widths = [22, 22, 14, 12, 12, 9, 16, 16, 16, 12]
    _write_headers(ws, 5, headers, widths)

    # Solo activos no-cash, ordenados por unrealized_pnl desc
    items = [h for h in holdings if not h["is_cash"] and h["unrealized_pnl_native"] is not None]
    items.sort(key=lambda h: -(h["unrealized_pnl_native"] or 0))

    for i, h in enumerate(items, start=6):
        _write_row(ws, i, [
            h["account"], h["asset"], h["qty"],
            h["avg_cost"], h["market_price"], h["native_currency"],
            h["cost_basis_total"], h["mv_native"],
            h["unrealized_pnl_native"], h["unrealized_pct"],
        ], formats=[
            None, None, FMT_NUMBER4,
            FMT_NUMBER4, FMT_NUMBER4, None,
            FMT_NUMBER, FMT_NUMBER,
            FMT_NUMBER, FMT_PCT,
        ])

    ws.freeze_panes = "A6"


# =============================================================================
# Excel export
# =============================================================================

def export_excel(conn, output_path, fecha=None, anchor_currency="USD"):
    """Genera Excel multi-sheet del portfolio. Devuelve Path del archivo."""
    if fecha is None:
        fecha = date.today()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    holdings = calculate_holdings(conn, fecha=fecha, anchor_currency=anchor_currency)
    fills = calculate_realized_pnl(conn, fecha_hasta=fecha)

    wb = Workbook()
    # Eliminar la hoja default que crea Workbook()
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    _sheet_dashboard(wb, holdings, anchor_currency, fecha)
    _sheet_holdings(wb, holdings, anchor_currency)
    _sheet_pn_by_account(wb, holdings, anchor_currency)
    _sheet_pn_by_class(wb, holdings, anchor_currency)
    _sheet_cash_position(wb, holdings, anchor_currency)
    _sheet_tarjetas(wb, conn, fecha)
    _sheet_pnl_realizado(wb, fills, anchor_currency)
    _sheet_pnl_no_realizado(wb, holdings, anchor_currency)

    wb.save(str(output_path))
    return output_path


# =============================================================================
# HTML export (autocontenido con chart.js inline)
# =============================================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Portfolio — {fecha}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f7;
    color: #1d1d1f;
    margin: 0;
    padding: 20px;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ color: #1F3864; font-size: 28px; margin: 0 0 8px 0; }}
  .subtitle {{ color: #595959; font-size: 14px; margin-bottom: 24px; }}
  .kpi {{
    background: white; border-radius: 12px; padding: 24px;
    margin-bottom: 24px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
  }}
  .kpi-label {{ color: #595959; font-size: 12px; text-transform: uppercase; }}
  .kpi-value {{ font-size: 36px; font-weight: bold; color: #1F3864; }}
  .kpi-currency {{ font-size: 16px; color: #595959; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  .card {{
    background: white; border-radius: 12px; padding: 20px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
  }}
  h2 {{ color: #1F3864; font-size: 18px; margin: 0 0 16px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{
    background: #1F3864; color: white; padding: 10px;
    text-align: left; font-weight: 600;
  }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #e0e0e0; }}
  tr:hover {{ background: #f9f9f9; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .positive {{ color: #00B050; }}
  .negative {{ color: #C00000; }}
  .chart-container {{ position: relative; height: 300px; }}
  .warn {{
    background: #FFF3CD; border-left: 4px solid #FFC107;
    padding: 12px; border-radius: 4px; margin-bottom: 16px; font-size: 13px;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>📊 Portfolio</h1>
  <div class="subtitle">Fecha: {fecha}  ·  Moneda ancla: {anchor_ccy}</div>

  <div class="kpi">
    <div class="kpi-label">Patrimonio Neto Total</div>
    <div class="kpi-value">{pn_formatted}</div>
    <div class="kpi-currency">{anchor_ccy}</div>
  </div>

  {warn_html}

  <div class="grid">
    <div class="card">
      <h2>Por Asset Class</h2>
      <div class="chart-container"><canvas id="chartClass"></canvas></div>
    </div>
    <div class="card">
      <h2>Por Moneda Nativa</h2>
      <div class="chart-container"><canvas id="chartCcy"></canvas></div>
    </div>
  </div>

  <div class="card" style="margin-top: 24px;">
    <h2>Top 15 Posiciones</h2>
    <table>
      <thead>
        <tr>
          <th>Cuenta</th>
          <th>Activo</th>
          <th>Asset Class</th>
          <th class="num">Qty</th>
          <th class="num">Mkt Price</th>
          <th class="num">MV ({anchor_ccy})</th>
          <th class="num">Unrealized %</th>
        </tr>
      </thead>
      <tbody>
{top_holdings_rows}
      </tbody>
    </table>
  </div>

  <div class="grid" style="margin-top: 24px;">
    <div class="card">
      <h2>PN por Cuenta</h2>
      <table>
        <thead><tr><th>Cuenta</th><th class="num">MV ({anchor_ccy})</th><th class="num">%</th></tr></thead>
        <tbody>{by_account_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>PnL Realizado por Año + Moneda</h2>
      <table>
        <thead><tr><th>Año</th><th>Moneda</th><th class="num"># Trades</th><th class="num">PnL Total</th></tr></thead>
        <tbody>{pnl_year_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="subtitle" style="margin-top: 24px; text-align: center;">
    Generado por wm_engine
  </div>
</div>

<script>
const dataClass = {data_class_json};
const dataCcy = {data_ccy_json};

new Chart(document.getElementById('chartClass'), {{
  type: 'doughnut',
  data: {{
    labels: dataClass.labels,
    datasets: [{{
      data: dataClass.values,
      backgroundColor: ['#1F3864','#2E5B9C','#4F81BD','#8DB4E2','#B8CCE4','#DCE6F1','#F2F2F2'],
    }}]
  }},
  options: {{ responsive: true, maintainAspectRatio: false }}
}});

new Chart(document.getElementById('chartCcy'), {{
  type: 'doughnut',
  data: {{
    labels: dataCcy.labels,
    datasets: [{{
      data: dataCcy.values,
      backgroundColor: ['#1F3864','#00B050','#C00000','#FFC107','#9C27B0','#FF5722'],
    }}]
  }},
  options: {{ responsive: true, maintainAspectRatio: false }}
}});
</script>
</body>
</html>
"""


def _fmt_number(v, decimals=2):
    if v is None:
        return "-"
    return f"{v:,.{decimals}f}"


def _fmt_pct(v):
    if v is None:
        return "-"
    cls = "positive" if v > 0 else "negative" if v < 0 else ""
    return f'<span class="{cls}">{v*100:+.2f}%</span>'


def export_html(conn, output_path, fecha=None, anchor_currency="USD"):
    """Genera HTML autocontenido del portfolio. Devuelve Path."""
    if fecha is None:
        fecha = date.today()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    holdings = calculate_holdings(conn, fecha=fecha, anchor_currency=anchor_currency)
    fills = calculate_realized_pnl(conn, fecha_hasta=fecha)

    tp = total_pn(holdings, anchor_currency)
    cls_data = by_asset_class(holdings)
    ccy_data = by_currency(holdings)
    accs = by_account(holdings)
    accs_total = sum(accs.values())

    # Top 15 holdings
    top_15 = [h for h in holdings[:15]]
    top_rows = []
    for h in top_15:
        top_rows.append(
            f'<tr>'
            f'<td>{h["account"]}</td>'
            f'<td>{h["asset"]}</td>'
            f'<td>{h.get("asset_class", "")}</td>'
            f'<td class="num">{_fmt_number(h["qty"], 4)}</td>'
            f'<td class="num">{_fmt_number(h["market_price"], 4)}</td>'
            f'<td class="num">{_fmt_number(h["mv_anchor"])}</td>'
            f'<td class="num">{_fmt_pct(h.get("unrealized_pct"))}</td>'
            f'</tr>'
        )

    # By account
    by_acc_rows = []
    for acc, val in list(accs.items())[:15]:
        pct = (val / accs_total) if accs_total else 0
        by_acc_rows.append(
            f'<tr><td>{acc}</td>'
            f'<td class="num">{_fmt_number(val)}</td>'
            f'<td class="num">{pct*100:.1f}%</td></tr>'
        )

    # PnL por año + moneda
    pnl_yc = aggregate_pnl_by_year_currency(fills)
    pnl_year_rows = []
    for item in pnl_yc:
        cls = "positive" if item["pnl_total"] > 0 else "negative" if item["pnl_total"] < 0 else ""
        pnl_year_rows.append(
            f'<tr><td>{item["year"]}</td>'
            f'<td>{item["currency"]}</td>'
            f'<td class="num">{item["n_trades"]}</td>'
            f'<td class="num"><span class="{cls}">{_fmt_number(item["pnl_total"])}</span></td></tr>'
        )
    if not pnl_year_rows:
        pnl_year_rows.append('<tr><td colspan="4" style="text-align: center; color: #595959;">Sin trades cerrados aún</td></tr>')

    # Warn
    warn = ""
    if tp["total_unconverted_count"] > 0:
        warn = f'<div class="warn">⚠ {tp["total_unconverted_count"]} posiciones sin FX hacia {anchor_currency}, no incluidas en el total</div>'

    html = HTML_TEMPLATE.format(
        fecha=fecha.isoformat(),
        anchor_ccy=anchor_currency,
        pn_formatted=_fmt_number(tp["total_anchor"]),
        warn_html=warn,
        top_holdings_rows="\n".join(top_rows),
        by_account_rows="\n".join(by_acc_rows),
        pnl_year_rows="\n".join(pnl_year_rows),
        data_class_json=json.dumps({
            "labels": list(cls_data.keys()),
            "values": list(cls_data.values()),
        }),
        data_ccy_json=json.dumps({
            "labels": list(ccy_data.keys()),
            "values": list(ccy_data.values()),
        }),
    )

    output_path.write_text(html, encoding="utf-8")
    return output_path
