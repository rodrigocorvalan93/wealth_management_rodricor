# -*- coding: utf-8 -*-
"""
api/app.py

Backend Flask del wealth_management. Wraps el engine y expone:

  AUTH:        Bearer <WM_API_TOKEN> en header Authorization.

  GET    /api/health                       — ping (no requiere auth)
  GET    /api/summary                      — resumen ejecutivo del portfolio
  GET    /api/holdings                     — lista de holdings actuales
  GET    /api/equity-curve                 — serie temporal del PN
  GET    /api/buying-power                 — poder de compra por cuenta
  GET    /api/trade-stats                  — métricas de trading
  GET    /api/realized-pnl                 — fills de PnL realizado FIFO

  GET    /api/sheets/<sheet>               — lista filas de una hoja
  GET    /api/sheets/<sheet>/<row_id>      — una fila específica
  POST   /api/sheets/<sheet>               — agrega fila (devuelve row_id)
  PUT    /api/sheets/<sheet>/<row_id>      — modifica fila
  DELETE /api/sheets/<sheet>/<row_id>      — borra fila (soft, deja Row ID)

  POST   /api/upload/excel                 — sube nuevo Excel master (sobreescribe)
  GET    /api/download/excel               — baja el Excel master actual
  POST   /api/upload/prices                — sube CSV de precios al data dir
  POST   /api/refresh                      — fuerza re-import del Excel
  GET    /api/backups                      — lista los backups del Excel
  GET    /api/report/html                  — HTML autocontenido del reporte
  GET    /api/report/excel                 — Excel multi-sheet del reporte
  GET    /api/config                       — info de paths y config

USO LOCAL:
  WM_API_TOKEN=test python -m flask --app api.app run
"""

from __future__ import annotations

import io
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# Permitir importar engine/ aunque corra como script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import (
    Flask, request, jsonify, send_file, send_from_directory,
    abort, Response, render_template,
)

from engine.holdings import (
    calculate_holdings, total_pn, by_asset_class, by_account, by_currency,
    by_cash_purpose, filter_investible, filter_non_investible,
)
from engine.pnl import calculate_realized_pnl, total_realized_pnl
from engine.trade_stats import (
    calculate_trade_stats, trade_stats_by_asset, trade_stats_by_account,
)
from engine.snapshots import (
    record_snapshots, get_equity_curve, get_equity_curves_by_account,
    calculate_returns,
)
from engine.buying_power import buying_power_summary
from engine.exporter import export_excel, export_html

from .state import (
    get_settings, db_conn, excel_write_lock, backup_excel,
    list_backups, prune_backups, reimport_excel,
)
from .excel_io import (
    SHEET_PREFIX, list_rows, get_row, append_row, update_row, delete_row,
)


def create_app() -> Flask:
    app = Flask(__name__,
                static_folder="static",
                template_folder="templates",
                static_url_path="/static")

    # --- PWA: shell HTML ---
    @app.route("/")
    def root():
        return render_template("pwa.html")

    # Service worker debe servirse desde la raíz (no /static/) para tener
    # scope completo. Lo redirigimos.
    @app.route("/sw.js")
    def service_worker():
        resp = send_from_directory(app.static_folder, "sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # --- CORS abierto (single user, token-protected) ---
    @app.after_request
    def add_cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return resp

    @app.route("/api/<path:_>", methods=["OPTIONS"])
    def cors_preflight(_):
        return ("", 204)

    # --- Auth ---
    def _require_auth():
        s = get_settings()
        if not s.api_token:
            abort(500, "WM_API_TOKEN no configurado en el server")
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            abort(401, "Falta header Authorization: Bearer <token>")
        token = auth[len("Bearer "):].strip()
        if token != s.api_token:
            abort(401, "Token inválido")

    # --- Helpers ---
    def _holdings(fecha=None, anchor=None):
        s = get_settings()
        anchor = (anchor or s.anchor).upper()
        if fecha is None:
            fecha = date.today()
        if isinstance(fecha, str):
            fecha = date.fromisoformat(fecha)
        conn = db_conn()
        try:
            return calculate_holdings(conn, fecha=fecha, anchor_currency=anchor), conn
        except Exception:
            conn.close()
            raise

    def _parse_query_fecha():
        f = request.args.get("fecha")
        return date.fromisoformat(f) if f else date.today()

    def _parse_query_anchor():
        return (request.args.get("anchor") or get_settings().anchor).upper()

    # =========================================================================
    # PUBLIC
    # =========================================================================

    @app.get("/api/health")
    def health():
        s = get_settings()
        xlsx_exists = s.xlsx_path.is_file()
        db_exists = s.db_path.is_file()
        return jsonify({
            "status": "ok",
            "version": "1.0",
            "xlsx_present": xlsx_exists,
            "db_present": db_exists,
            "anchor_default": s.anchor,
            "auth_configured": bool(s.api_token),
            "now": datetime.now().isoformat(),
        })

    @app.get("/api/config")
    def config():
        _require_auth()
        s = get_settings()
        return jsonify({
            "base_dir": str(s.base_dir),
            "xlsx_path": str(s.xlsx_path),
            "db_path": str(s.db_path),
            "data_dir": str(s.data_dir),
            "backups_dir": str(s.backups_dir),
            "anchor": s.anchor,
            "supported_sheets": list(SHEET_PREFIX.keys()),
        })

    # =========================================================================
    # READ: portfolio analytics
    # =========================================================================

    @app.get("/api/summary")
    def summary():
        _require_auth()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        holdings, conn = _holdings(fecha, anchor)
        try:
            tp = total_pn(holdings, anchor)
            return jsonify({
                "fecha": fecha.isoformat(),
                "anchor": anchor,
                "patrimonio_total": tp["total_anchor"],
                "patrimonio_invertible": tp["total_investible"],
                "patrimonio_no_invertible": tp["total_non_investible"],
                "unconverted_count": tp["total_unconverted_count"],
                "by_asset_class": by_asset_class(holdings),
                "by_account": by_account(holdings),
                "by_currency": by_currency(holdings),
                "by_cash_purpose": by_cash_purpose(holdings),
                "n_positions": len(holdings),
            })
        finally:
            conn.close()

    @app.get("/api/holdings")
    def holdings_endpoint():
        _require_auth()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        holdings, conn = _holdings(fecha, anchor)
        try:
            # Filtros opcionales
            account = request.args.get("account")
            asset_class = request.args.get("asset_class")
            investible_only = request.args.get("investible") == "true"
            data = holdings
            if investible_only:
                data = filter_investible(data)
            if account:
                data = [h for h in data if h["account"] == account]
            if asset_class:
                data = [h for h in data if h["asset_class"] == asset_class]
            # Limpiar valores no JSON-friendly
            return jsonify({
                "fecha": fecha.isoformat(),
                "anchor": anchor,
                "n": len(data),
                "items": [_clean_for_json(h) for h in data],
            })
        finally:
            conn.close()

    @app.get("/api/equity-curve")
    def equity_curve():
        _require_auth()
        anchor = _parse_query_anchor()
        investible_only = request.args.get("investible") == "true"
        per_account = request.args.get("per_account") == "true"
        conn = db_conn()
        try:
            total = get_equity_curve(conn, anchor_currency=anchor,
                                      investible_only=investible_only)
            payload = {
                "anchor": anchor,
                "investible_only": investible_only,
                "total": total,
                "metrics": calculate_returns(total),
            }
            if per_account:
                by_acc = get_equity_curves_by_account(
                    conn, anchor_currency=anchor,
                    investible_only=investible_only,
                )
                payload["by_account"] = by_acc
            return jsonify(payload)
        finally:
            conn.close()

    @app.get("/api/buying-power")
    def buying_power_endpoint():
        _require_auth()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        holdings, conn = _holdings(fecha, anchor)
        try:
            summary = buying_power_summary(conn, holdings, anchor)
            out = []
            for item in summary:
                if item["type"] == "BYMA":
                    out.append({
                        "account": item["account"],
                        "type": "BYMA",
                        "result": item["result"].to_dict(),
                    })
                else:
                    out.append({
                        "account": item["account"],
                        "type": "MARGIN",
                        "overnight": item["overnight"].to_dict(),
                        "intraday": item["intraday"].to_dict(),
                    })
            return jsonify({"anchor": anchor, "items": out})
        finally:
            conn.close()

    @app.get("/api/trade-stats")
    def trade_stats_endpoint():
        _require_auth()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        conn = db_conn()
        try:
            fills = calculate_realized_pnl(conn, fecha_hasta=fecha)
            stats = calculate_trade_stats(fills)
            stats_dict = {ccy: s.to_dict() for ccy, s in stats.items()}
            # Sanear inf
            for s in stats_dict.values():
                if s["profit_factor"] == float("inf"):
                    s["profit_factor"] = None
            return jsonify({
                "fecha": fecha.isoformat(),
                "n_fills": len(fills),
                "by_currency": stats_dict,
                "by_asset": trade_stats_by_asset(fills),
                "by_account": trade_stats_by_account(fills),
            })
        finally:
            conn.close()

    @app.get("/api/realized-pnl")
    def realized_pnl_endpoint():
        _require_auth()
        fecha = _parse_query_fecha()
        conn = db_conn()
        try:
            fills = calculate_realized_pnl(conn, fecha_hasta=fecha)
            return jsonify({
                "n": len(fills),
                "totals_by_currency": total_realized_pnl(fills),
                "fills": [
                    {
                        "fecha_venta": f.fecha_venta,
                        "fecha_compra": f.fecha_compra,
                        "account": f.account,
                        "asset": f.asset,
                        "qty": f.qty,
                        "precio_compra": f.precio_compra,
                        "precio_venta": f.precio_venta,
                        "currency": f.currency,
                        "pnl_realizado": f.pnl_realizado,
                        "pnl_pct": f.pnl_pct,
                        "holding_period_days": f.holding_period_days,
                    } for f in fills
                ],
            })
        finally:
            conn.close()

    # =========================================================================
    # CRUD: hojas de eventos del Excel
    # =========================================================================

    @app.get("/api/sheets/<sheet>")
    def list_sheet(sheet):
        _require_auth()
        s = get_settings()
        if sheet not in SHEET_PREFIX:
            abort(404, f"Sheet '{sheet}' no soportada. Disponibles: {list(SHEET_PREFIX.keys())}")
        if not s.xlsx_path.is_file():
            abort(404, "Excel master no presente")
        rows = list_rows(s.xlsx_path, sheet)
        return jsonify({"sheet": sheet, "n": len(rows), "items": rows})

    @app.get("/api/sheets/<sheet>/<row_id>")
    def get_sheet_row(sheet, row_id):
        _require_auth()
        s = get_settings()
        if sheet not in SHEET_PREFIX:
            abort(404)
        row = get_row(s.xlsx_path, sheet, row_id)
        if row is None:
            abort(404, f"Row ID '{row_id}' no encontrado en '{sheet}'")
        return jsonify(row)

    @app.post("/api/sheets/<sheet>")
    def create_sheet_row(sheet):
        _require_auth()
        s = get_settings()
        if sheet not in SHEET_PREFIX:
            abort(404)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400, "Body debe ser un JSON object con {header: value}")
        with excel_write_lock():
            backup_excel()
            row_id = append_row(s.xlsx_path, sheet, data)
            stats = reimport_excel()
            prune_backups(keep_last=50)
        return jsonify({
            "sheet": sheet, "row_id": row_id,
            "row": get_row(s.xlsx_path, sheet, row_id),
            "import_stats": stats,
        }), 201

    @app.put("/api/sheets/<sheet>/<row_id>")
    def update_sheet_row(sheet, row_id):
        _require_auth()
        s = get_settings()
        if sheet not in SHEET_PREFIX:
            abort(404)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400, "Body debe ser un JSON object")
        with excel_write_lock():
            backup_excel()
            try:
                row = update_row(s.xlsx_path, sheet, row_id, data)
            except KeyError as e:
                abort(404, str(e))
            stats = reimport_excel()
            prune_backups(keep_last=50)
        return jsonify({
            "sheet": sheet, "row_id": row_id, "row": row,
            "import_stats": stats,
        })

    @app.delete("/api/sheets/<sheet>/<row_id>")
    def delete_sheet_row(sheet, row_id):
        _require_auth()
        s = get_settings()
        if sheet not in SHEET_PREFIX:
            abort(404)
        with excel_write_lock():
            backup_excel()
            ok = delete_row(s.xlsx_path, sheet, row_id)
            if not ok:
                abort(404, f"Row ID '{row_id}' no encontrado")
            stats = reimport_excel()
            prune_backups(keep_last=50)
        return jsonify({"deleted": True, "row_id": row_id, "import_stats": stats})

    # =========================================================================
    # File upload/download
    # =========================================================================

    @app.post("/api/upload/excel")
    def upload_excel():
        _require_auth()
        s = get_settings()
        if "file" not in request.files:
            abort(400, "Subí el archivo en form-data como 'file'")
        f = request.files["file"]
        if not f.filename.lower().endswith((".xlsx", ".xls")):
            abort(400, "Solo .xlsx / .xls")
        with excel_write_lock():
            if s.xlsx_path.is_file():
                backup_excel()
            f.save(str(s.xlsx_path))
            stats = reimport_excel()
            prune_backups(keep_last=50)
        return jsonify({
            "saved": str(s.xlsx_path),
            "size_bytes": s.xlsx_path.stat().st_size,
            "import_stats": stats,
        })

    @app.get("/api/download/excel")
    def download_excel():
        _require_auth()
        s = get_settings()
        if not s.xlsx_path.is_file():
            abort(404, "No hay Excel master")
        return send_file(
            str(s.xlsx_path),
            as_attachment=True,
            download_name=s.xlsx_path.name,
        )

    @app.post("/api/upload/prices")
    def upload_prices():
        _require_auth()
        s = get_settings()
        if "file" not in request.files:
            abort(400, "Subí el CSV en form-data como 'file'")
        f = request.files["file"]
        # Nombre sugerido del CSV: usá el nombre original
        name = (request.form.get("name") or f.filename).strip()
        if not name.endswith(".csv"):
            abort(400, "Solo .csv")
        target = s.data_dir / name
        f.save(str(target))
        # Re-importar para refrescar precios
        with excel_write_lock():
            stats = reimport_excel()
        return jsonify({
            "saved": str(target),
            "size_bytes": target.stat().st_size,
            "import_stats": stats,
        })

    @app.post("/api/refresh")
    def refresh():
        _require_auth()
        with excel_write_lock():
            stats = reimport_excel()
        return jsonify({"refreshed": True, "import_stats": stats})

    @app.get("/api/backups")
    def backups():
        _require_auth()
        return jsonify({
            "backups": [
                {
                    "name": b.name,
                    "size_bytes": b.stat().st_size,
                    "mtime": datetime.fromtimestamp(b.stat().st_mtime).isoformat(),
                }
                for b in list_backups(limit=50)
            ],
        })

    # =========================================================================
    # Reports
    # =========================================================================

    @app.get("/api/report/html")
    def report_html():
        _require_auth()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        s = get_settings()
        out = s.data_dir / f"_tmp_report_{fecha.isoformat()}.html"
        conn = db_conn()
        try:
            export_html(conn, out, fecha=fecha, anchor_currency=anchor,
                        record_snapshot=True)
        finally:
            conn.close()
        html = out.read_text(encoding="utf-8")
        try:
            out.unlink()
        except OSError:
            pass
        return Response(html, mimetype="text/html")

    @app.get("/api/report/excel")
    def report_excel():
        _require_auth()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        s = get_settings()
        out = s.data_dir / f"_tmp_report_{fecha.isoformat()}.xlsx"
        conn = db_conn()
        try:
            export_excel(conn, out, fecha=fecha, anchor_currency=anchor,
                         record_snapshot=True)
        finally:
            conn.close()
        return send_file(
            str(out), as_attachment=True,
            download_name=f"{fecha.isoformat()}_portfolio.xlsx",
        )

    # =========================================================================
    # Error handlers
    # =========================================================================

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def err(e):
        return jsonify({
            "error": True,
            "code": getattr(e, "code", 500),
            "message": getattr(e, "description", str(e)),
        }), getattr(e, "code", 500)

    return app


def _clean_for_json(d: dict) -> dict:
    """Remueve campos que no son JSON-serializable y formatea None/inf."""
    import math
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# Para `flask --app api.app run` y para WSGI:
app = create_app()
