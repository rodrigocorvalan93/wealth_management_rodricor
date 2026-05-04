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
    abort, Response, render_template, g,
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
    get_settings, get_user_settings, list_user_ids,
    db_conn, excel_write_lock, backup_excel,
    list_backups, prune_backups, reimport_excel,
    DEFAULT_USER_ID,
)
from .users import (
    load_users, resolve_user_by_token, get_active_user,
    admin_switch_to, admin_switch_clear, is_switched,
    add_user_to_config, remove_user_from_config, export_users_json,
    UserConfig,
)
from .excel_io import (
    SHEET_PREFIX, MASTER_SHEETS, ALLOWED_SHEETS, is_master_sheet,
    list_rows, get_row, append_row, update_row, delete_row,
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

    # --- Auth + user resolution (multi-tenant) ---

    @app.before_request
    def _resolve_user():
        """Resuelve el user activo desde el bearer token y lo setea en g.

        - g.auth_user: el user dueño del token
        - g.active_user_id: el user_id efectivamente activo (=auth_user, o
          el target si el admin hizo switch)
        - g.is_admin: True si el auth_user es admin
        - g.is_switched: True si el admin está viendo datos de otro
        """
        # Endpoints públicos: no resolver user
        if not request.path.startswith("/api/"):
            return
        if request.method == "OPTIONS":
            return
        if request.path == "/api/health":
            return

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return  # _require_auth abortará después
        token = auth[len("Bearer "):].strip()

        active = get_active_user(token)
        if active is None:
            return  # _require_auth abortará

        auth_user, switched = active
        # auth_user es el user dueño del token (puede ser admin con switch)
        # Buscamos el user real al que apunta:
        actual_token_owner = resolve_user_by_token(token)
        g.auth_user = actual_token_owner
        g.active_user_id = auth_user.user_id
        g.is_admin = bool(actual_token_owner and actual_token_owner.is_admin)
        g.is_switched = switched
        g.user_token = token

    def _require_auth():
        """Verifica que haya user resuelto en g."""
        if not load_users():
            abort(500, "Sin users configurados. Setea WM_USERS_JSON o WM_API_TOKEN.")
        if not getattr(g, "active_user_id", None):
            abort(401, "Token inválido o ausente")

    def _require_admin():
        """Verifica que el caller sea admin (independiente de switch state)."""
        _require_auth()
        if not getattr(g, "is_admin", False):
            abort(403, "Acción solo para admin")

    def _block_if_switched_mutation():
        """En vista 'switched', el admin no puede mutar datos del target user.
        Aborta 403 si la request es POST/PUT/DELETE y está switched."""
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if getattr(g, "is_switched", False):
                abort(403,
                      "Estás en modo 'switch user' (read-only). Volvé a tu user "
                      "para mutar datos.")

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
        import os as _os
        users = load_users()
        is_multi = bool(_os.environ.get("WM_USERS_JSON"))
        # Back-compat: si single-tenant, exponer flags del user "default"
        # para que clientes viejos sigan funcionando.
        body = {
            "status": "ok",
            "version": "2.0",
            "auth_configured": len(users) > 0,
            "n_users": len(users),
            "multi_tenant": is_multi,
            "now": datetime.now().isoformat(),
        }
        if not is_multi and users:
            s = get_user_settings(users[0].user_id)
            body["xlsx_present"] = s.xlsx_path.is_file()
            body["db_present"] = s.db_path.is_file()
            body["anchor_default"] = s.anchor
        return jsonify(body)

    @app.get("/api/config")
    def config():
        _require_auth(); _block_if_switched_mutation()
        s = get_settings()
        return jsonify({
            "user_id": g.active_user_id,
            "is_admin": g.is_admin,
            "is_switched": g.is_switched,
            "auth_user_id": g.auth_user.user_id if g.auth_user else None,
            "auth_user_display_name": g.auth_user.display_name if g.auth_user else None,
            "xlsx_path": str(s.xlsx_path),
            "xlsx_present": s.xlsx_path.is_file(),
            "db_present": s.db_path.is_file(),
            "anchor": s.anchor,
            "supported_sheets": sorted(ALLOWED_SHEETS),
            "event_sheets": list(SHEET_PREFIX.keys()),
            "master_sheets": {k: v for k, v in MASTER_SHEETS.items() if v is not None},
        })

    # =========================================================================
    # READ: portfolio analytics
    # =========================================================================

    @app.get("/api/summary")
    def summary():
        _require_auth(); _block_if_switched_mutation()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        holdings, conn = _holdings(fecha, anchor)
        try:
            tp = total_pn(holdings, anchor)
            return jsonify({
                "fecha": fecha.isoformat(),
                "anchor": anchor,
                "patrimonio_total": tp["total_anchor"],            # NETO
                "patrimonio_invertible": tp["total_investible"],
                "patrimonio_no_invertible": tp["total_non_investible"],
                "total_assets": tp["total_assets"],
                "total_liabilities": tp["total_liabilities"],
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
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
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

    @app.get("/api/holdings-near-target")
    def holdings_near_target_endpoint():
        """Holdings cuya distancia al target o stop-loss está dentro del
        umbral configurado en `alert_distance_bps`. Query param `bps`
        opcional para override on-the-fly."""
        _require_auth(); _block_if_switched_mutation()
        from engine.holdings import filter_near_target
        from engine.schema import get_setting
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        holdings, conn = _holdings(fecha, anchor)
        try:
            override = request.args.get("bps")
            if override is not None:
                try:
                    bps = float(override)
                except ValueError:
                    abort(400, "bps debe ser numérico")
            else:
                bps = get_setting(conn, "alert_distance_bps",
                                   default=10.0, cast=float)
            alerts = filter_near_target(holdings, alert_distance_bps=bps)
            return jsonify({
                "alert_distance_bps": bps,
                "fecha": fecha.isoformat(),
                "anchor": anchor,
                "n_alerts": len(alerts),
                "alerts": alerts,
            })
        finally:
            conn.close()

    @app.get("/api/settings")
    def get_settings_endpoint():
        """Devuelve los settings persistidos en DB (key/value)."""
        _require_auth()
        from engine.schema import get_setting
        conn = db_conn()
        try:
            return jsonify({
                "alert_distance_bps": get_setting(conn, "alert_distance_bps",
                                                    default=10.0, cast=float),
                "anchor_currency": get_setting(conn, "anchor_currency",
                                                default=get_settings().anchor),
                "pnl_method": get_setting(conn, "pnl_method", default="FIFO"),
            })
        finally:
            conn.close()

    @app.put("/api/settings")
    def update_settings_endpoint():
        """Actualiza un setting. Body: {"key": "alert_distance_bps", "value": 50}.

        IMPORTANTE: el valor se persiste a la DB pero NO al Excel master,
        así que se va a perder en el próximo refresh. Para hacerlo permanente
        editá la hoja `config` del master.
        """
        _require_auth(); _block_if_switched_mutation()
        from engine.schema import set_setting
        body = request.get_json(silent=True) or {}
        key = body.get("key")
        value = body.get("value")
        if not key:
            abort(400, "key requerido")
        # Whitelist de keys editables
        allowed = {"alert_distance_bps"}
        if key not in allowed:
            abort(400, f"key '{key}' no editable. Permitidos: {sorted(allowed)}")
        conn = db_conn()
        try:
            set_setting(conn, key, value)
            conn.commit()
            return jsonify({"ok": True, "key": key, "value": value})
        finally:
            conn.close()

    @app.get("/api/buying-power")
    def buying_power_endpoint():
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
        s = get_settings()
        if sheet not in ALLOWED_SHEETS:
            abort(404, f"Sheet '{sheet}' no soportada. Disponibles: {list(SHEET_PREFIX.keys())}")
        if not s.xlsx_path.is_file():
            abort(404, "Excel master no presente")
        rows = list_rows(s.xlsx_path, sheet)
        return jsonify({"sheet": sheet, "n": len(rows), "items": rows})

    @app.get("/api/sheets/<sheet>/<row_id>")
    def get_sheet_row(sheet, row_id):
        _require_auth(); _block_if_switched_mutation()
        s = get_settings()
        if sheet not in ALLOWED_SHEETS:
            abort(404)
        row = get_row(s.xlsx_path, sheet, row_id)
        if row is None:
            abort(404, f"Row ID '{row_id}' no encontrado en '{sheet}'")
        return jsonify(row)

    @app.post("/api/sheets/<sheet>")
    def create_sheet_row(sheet):
        _require_auth(); _block_if_switched_mutation()
        s = get_settings()
        if sheet not in ALLOWED_SHEETS:
            abort(404)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400, "Body debe ser un JSON object con {header: value}")
        with excel_write_lock():
            backup_excel()
            try:
                row_id = append_row(s.xlsx_path, sheet, data)
            except ValueError as e:
                abort(400, str(e))
            stats = reimport_excel()
            prune_backups(keep_last=50)
        return jsonify({
            "sheet": sheet, "row_id": row_id,
            "row": get_row(s.xlsx_path, sheet, row_id),
            "import_stats": stats,
        }), 201

    @app.put("/api/sheets/<sheet>/<row_id>")
    def update_sheet_row(sheet, row_id):
        _require_auth(); _block_if_switched_mutation()
        s = get_settings()
        if sheet not in ALLOWED_SHEETS:
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
            except ValueError as e:
                abort(400, str(e))
            stats = reimport_excel()
            prune_backups(keep_last=50)
        return jsonify({
            "sheet": sheet, "row_id": row_id, "row": row,
            "import_stats": stats,
        })

    @app.delete("/api/sheets/<sheet>/<row_id>")
    def delete_sheet_row(sheet, row_id):
        _require_auth(); _block_if_switched_mutation()
        s = get_settings()
        if sheet not in ALLOWED_SHEETS:
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
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
        with excel_write_lock():
            stats = reimport_excel()
        return jsonify({"refreshed": True, "import_stats": stats})

    @app.get("/api/backups")
    def backups():
        _require_auth(); _block_if_switched_mutation()
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
    # Cotizaciones (precios + FX) y cash por cuenta
    # =========================================================================

    @app.get("/api/prices")
    def prices_endpoint():
        """Devuelve la última cotización conocida por ticker.

        Query params:
          ?ticker=AL30D        — filtra por uno
          ?asset_class=BOND_AR — filtra por clase
        """
        _require_auth(); _block_if_switched_mutation()
        conn = db_conn()
        try:
            ticker = request.args.get("ticker")
            asset_class = request.args.get("asset_class")
            sql = """
                SELECT p.ticker, p.price, p.currency, p.fecha, p.source,
                       a.name, a.asset_class
                FROM prices p
                INNER JOIN (
                    SELECT ticker, MAX(fecha) AS max_fecha
                    FROM prices GROUP BY ticker
                ) m ON p.ticker = m.ticker AND p.fecha = m.max_fecha
                LEFT JOIN assets a ON a.ticker = p.ticker
            """
            params = []
            wheres = []
            if ticker:
                wheres.append("p.ticker = ?")
                params.append(ticker)
            if asset_class:
                wheres.append("a.asset_class = ?")
                params.append(asset_class)
            if wheres:
                sql += " WHERE " + " AND ".join(wheres)
            sql += " ORDER BY a.asset_class, p.ticker"
            rows = conn.execute(sql, params).fetchall()
            return jsonify({
                "n": len(rows),
                "items": [
                    {
                        "ticker": r["ticker"],
                        "name": r["name"] or "",
                        "asset_class": r["asset_class"] or "",
                        "price": r["price"],
                        "currency": r["currency"],
                        "fecha": r["fecha"],
                        "source": r["source"] or "",
                    } for r in rows
                ],
            })
        finally:
            conn.close()

    @app.get("/api/fx-rates")
    def fx_rates_endpoint():
        """Devuelve la última cotización FX conocida por (moneda, base)."""
        _require_auth(); _block_if_switched_mutation()
        conn = db_conn()
        try:
            sql = """
                SELECT fr.fecha, fr.moneda, fr.rate, fr.base, fr.source
                FROM fx_rates fr
                INNER JOIN (
                    SELECT moneda, base, MAX(fecha) AS max_fecha
                    FROM fx_rates GROUP BY moneda, base
                ) m ON fr.moneda = m.moneda AND fr.base = m.base
                       AND fr.fecha = m.max_fecha
                ORDER BY fr.base, fr.moneda
            """
            rows = conn.execute(sql).fetchall()
            return jsonify({
                "n": len(rows),
                "items": [
                    {
                        "fecha": r["fecha"],
                        "moneda": r["moneda"],
                        "rate": r["rate"],
                        "base": r["base"],
                        "source": r["source"] or "",
                    } for r in rows
                ],
            })
        finally:
            conn.close()

    @app.get("/api/cash")
    def cash_endpoint():
        """Saldos cash por cuenta (todas las monedas).

        Query: ?anchor=USD para incluir conversión a moneda ancla.
        """
        _require_auth(); _block_if_switched_mutation()
        anchor = _parse_query_anchor()
        fecha = _parse_query_fecha()
        holdings, conn = _holdings(fecha, anchor)
        try:
            cash_items = [h for h in holdings if h.get("is_cash")]
            # Excluir pasivos (los saldos deudores de tarjetas no son cash positivo)
            cash_items = [h for h in cash_items if not h.get("is_liability")]
            cash_items.sort(key=lambda h: -(h.get("mv_anchor") or 0))
            # Subtotales por moneda
            by_ccy = {}
            for h in cash_items:
                ccy = h["native_currency"]
                if ccy not in by_ccy:
                    by_ccy[ccy] = {"qty": 0.0, "mv_anchor": 0.0, "n": 0}
                by_ccy[ccy]["qty"] += h["qty"]
                if h.get("mv_anchor") is not None:
                    by_ccy[ccy]["mv_anchor"] += h["mv_anchor"]
                by_ccy[ccy]["n"] += 1
            total_anchor = sum(h.get("mv_anchor") or 0 for h in cash_items)
            return jsonify({
                "anchor": anchor,
                "fecha": fecha.isoformat(),
                "n": len(cash_items),
                "total_anchor": total_anchor,
                "by_currency": by_ccy,
                "items": [
                    {
                        "account": h["account"],
                        "account_name": h.get("account_name") or h["account"],
                        "account_kind": h.get("account_kind") or "",
                        "account_institution": h.get("account_institution") or "",
                        "currency": h["native_currency"],
                        "qty": h["qty"],
                        "mv_anchor": h.get("mv_anchor"),
                        "cash_purpose": h.get("cash_purpose") or "",
                        "investible": h.get("investible", True),
                    } for h in cash_items
                ],
            })
        finally:
            conn.close()

    # =========================================================================
    # Calendar — próximos eventos (cupones, vencimientos, cierres tarjetas, fundings)
    # =========================================================================

    @app.get("/api/calendar")
    def calendar_endpoint():
        """Devuelve eventos calendarizados de los próximos N días.

        Query: ?days=60 (default 60).

        Eventos incluidos:
          - Maturity de bonos (assets.maturity)
          - Cierres y vencimientos de tarjetas (próximo + siguiente)
          - Funding cierres (cauciones que vencen)
          - Recurrentes próximos (sueldo, alquiler, servicios)
        """
        _require_auth(); _block_if_switched_mutation()
        from datetime import timedelta
        import calendar as _cal
        days_ahead = int(request.args.get("days", 60))
        today = date.today()
        until = today + timedelta(days=days_ahead)
        events = []

        conn = db_conn()
        try:
            # 1. Maturity de bonos
            cur = conn.execute(
                """SELECT ticker, name, maturity, currency, asset_class
                   FROM assets WHERE maturity IS NOT NULL AND maturity != ''"""
            )
            for r in cur.fetchall():
                try:
                    md = date.fromisoformat(r["maturity"][:10])
                    if today <= md <= until:
                        events.append({
                            "fecha": md.isoformat(),
                            "tipo": "maturity_bono",
                            "icon": "📜",
                            "title": f"Vencimiento {r['ticker']}",
                            "subtitle": r["name"] or "",
                            "amount": None,
                            "currency": r["currency"],
                            "ref_id": r["ticker"],
                        })
                except (ValueError, TypeError):
                    pass

            # 2. Tarjetas: próximos cierres + vencimientos
            cur = conn.execute(
                """SELECT code, name, card_close_day, card_due_day, card_currency
                   FROM accounts
                   WHERE kind='CARD_CREDIT' AND card_close_day IS NOT NULL"""
            )
            for r in cur.fetchall():
                close_day = r["card_close_day"]
                due_day = r["card_due_day"]
                # Próximos N cierres
                cur_y, cur_m = today.year, today.month
                for _ in range(4):  # próximos 4 ciclos = ~4 meses
                    last = _cal.monthrange(cur_y, cur_m)[1]
                    close_d = date(cur_y, cur_m, min(close_day, last))
                    if close_d >= today and close_d <= until:
                        events.append({
                            "fecha": close_d.isoformat(),
                            "tipo": "card_close",
                            "icon": "💳",
                            "title": f"Cierre {r['name']}",
                            "subtitle": r["code"],
                            "amount": None,
                            "currency": r["card_currency"] or "ARS",
                            "ref_id": r["code"],
                        })
                    if due_day:
                        # Due es mes siguiente al cierre
                        due_y = cur_y + (1 if cur_m == 12 else 0)
                        due_m = 1 if cur_m == 12 else cur_m + 1
                        last_due = _cal.monthrange(due_y, due_m)[1]
                        due_d = date(due_y, due_m, min(due_day, last_due))
                        if due_d >= today and due_d <= until:
                            events.append({
                                "fecha": due_d.isoformat(),
                                "tipo": "card_due",
                                "icon": "💸",
                                "title": f"Vto {r['name']}",
                                "subtitle": r["code"],
                                "amount": None,
                                "currency": r["card_currency"] or "ARS",
                                "ref_id": r["code"],
                            })
                    # Avanzar mes
                    cur_m += 1
                    if cur_m > 12:
                        cur_m = 1; cur_y += 1

            # 3. Cauciones que vencen — leemos del Excel para tener
            #    Fecha Fin / Status / Monto sin tener que reconstruirlo
            #    desde events.
            try:
                fund_rows = list_rows(get_settings().xlsx_path, "funding")
            except Exception:
                fund_rows = []
            for fr in fund_rows:
                if (fr.get("Status") or "").upper() == "CLOSED":
                    continue
                if not fr.get("Fecha Fin"):
                    continue
                try:
                    fin = date.fromisoformat(str(fr["Fecha Fin"])[:10])
                except (ValueError, TypeError):
                    continue
                if today <= fin <= until:
                    events.append({
                        "fecha": fin.isoformat(),
                        "tipo": "funding_close",
                        "icon": "💰",
                        "title": f"Vence {fr.get('Tipo', '')} {fr.get('Subtipo', '')}",
                        "subtitle": f"{fr.get('Fund ID', '')} · {fr.get('Cuenta', '')}",
                        "amount": float(fr["Monto"]) if fr.get("Monto") else None,
                        "currency": fr.get("Moneda", ""),
                        "ref_id": fr.get("Fund ID"),
                    })

            # 4. Recurrentes activos en los próximos N días
            cur = conn.execute("SELECT * FROM recurring_rules WHERE active=1")
            rules = cur.fetchall()
            for rule in rules:
                # Generar próximas N ocurrencias dentro de la ventana
                day_of_month = rule["day_of_month"] or 1
                cur_y, cur_m = today.year, today.month
                for _ in range(3):
                    last = _cal.monthrange(cur_y, cur_m)[1]
                    occ = date(cur_y, cur_m, min(day_of_month, last))
                    if occ >= today and occ <= until:
                        events.append({
                            "fecha": occ.isoformat(),
                            "tipo": "recurring_" + rule["event_type"].lower(),
                            "icon": "🔁" if rule["event_type"] == "INCOME" else "📅",
                            "title": rule["rule_name"],
                            "subtitle": rule["description"] or "",
                            "amount": rule["amount"],
                            "currency": rule["asset"],
                            "ref_id": str(rule["rule_id"]),
                        })
                    cur_m += 1
                    if cur_m > 12:
                        cur_m = 1; cur_y += 1

            events.sort(key=lambda e: e["fecha"])
            return jsonify({"days": days_ahead, "n": len(events), "events": events})
        finally:
            conn.close()

    # =========================================================================
    # Admin: gestión de usuarios + switch view
    # =========================================================================

    @app.get("/api/admin/users")
    def admin_list_users():
        """Lista todos los users (sin tokens completos, solo preview)."""
        _require_admin()
        users = load_users()
        # Si hay folders sin user en config, también listamos
        config_ids = set(u.user_id for u in users)
        disk_ids = set(list_user_ids())
        orphans = disk_ids - config_ids
        return jsonify({
            "users": [
                {
                    "user_id": u.user_id,
                    "display_name": u.display_name,
                    "is_admin": u.is_admin,
                    "token_preview": u.token[:8] + "..." if len(u.token) > 8 else "?",
                    "has_xlsx": (get_user_settings(u.user_id).xlsx_path).is_file(),
                    "has_db": (get_user_settings(u.user_id).db_path).is_file(),
                }
                for u in users
            ],
            "orphan_folders": sorted(orphans),
            "n_users": len(users),
        })

    @app.post("/api/admin/users")
    def admin_create_user():
        """Crea un nuevo user.

        Body JSON:
          { "user_id": "amigo", "display_name": "Marcos", "is_admin": false,
            "token": "auto"   ← si "auto", se genera random }

        Acciones:
          1. Genera token (si no se pasa)
          2. Crea folder inputs/<user_id>/
          3. Genera el Excel master (build_master + add_carga_inicial_sheet)
          4. Agrega user a WM_USERS_JSON in-memory
          5. Devuelve el token al admin para que lo comparta

        IMPORTANTE: el WSGI file de PythonAnywhere tiene que actualizarse
        para que el user persista entre reloads. La response incluye el
        snippet a copiar en el WSGI.
        """
        _require_admin()
        body = request.get_json(silent=True) or {}
        user_id = (body.get("user_id") or "").strip().lower()
        if not user_id or not user_id.replace("_", "").replace("-", "").isalnum():
            abort(400, "user_id inválido (solo letras/números/_/-, lowercase)")
        # Verificar duplicado
        existing = resolve_user_by_token("__never_match__")  # noop, just to ensure load
        for u in load_users():
            if u.user_id == user_id:
                abort(400, f"Ya existe un user con id '{user_id}'")

        display_name = body.get("display_name") or user_id
        is_admin = bool(body.get("is_admin", False))
        token = body.get("token") or ""
        if not token or token == "auto":
            import secrets
            token = secrets.token_hex(32)

        # Crear folders y master vacío
        new_settings = get_user_settings(user_id)
        new_settings.inputs_dir.mkdir(parents=True, exist_ok=True)
        new_settings.user_data_dir.mkdir(parents=True, exist_ok=True)
        new_settings.backups_dir.mkdir(parents=True, exist_ok=True)

        # Generar master con build_master + add_carga_inicial
        from build_master import build_master
        try:
            from add_carga_inicial_sheet import add_carga_inicial_sheet
        except ImportError:
            add_carga_inicial_sheet = None

        try:
            build_master(new_settings.xlsx_path)
            if add_carga_inicial_sheet:
                add_carga_inicial_sheet(new_settings.xlsx_path)
        except Exception as e:
            abort(500, f"No se pudo crear el master para '{user_id}': {e}")

        # Persistir en config in-memory (NO sobrevive un reload del web app)
        add_user_to_config(user_id, token, display_name=display_name,
                            is_admin=is_admin)

        return jsonify({
            "created": True,
            "user_id": user_id,
            "display_name": display_name,
            "is_admin": is_admin,
            "token": token,
            "url": request.host_url.rstrip("/"),
            "wsgi_snippet": _build_wsgi_snippet(),
            "warning": ("Para que el user persista a través de reloads del web "
                        "app, copiá el snippet de wsgi_snippet al WSGI file de "
                        "PythonAnywhere. Mientras tanto, el user funciona en "
                        "memoria del server."),
        }), 201

    @app.post("/api/admin/users/<user_id>/seed-demo")
    def admin_seed_demo(user_id):
        """Sobreescribe el master del user con datos demo hard-coded fijos.

        Útil para tener un user 'demo' con datos repetibles para mostrar la app.
        Después de seed, también re-importa la DB.
        """
        _require_admin()
        if not any(u.user_id == user_id for u in load_users()):
            abort(404, f"User '{user_id}' no existe")
        # Hacer backup del master actual antes de overwritearlo
        target = get_user_settings(user_id)
        if target.xlsx_path.is_file():
            with excel_write_lock(settings=target):
                backup_excel(settings=target)
        # Seed
        from seed_demo import seed_demo
        try:
            stats = seed_demo(target.xlsx_path)
        except Exception as e:
            abort(500, f"Seed demo falló: {e}")
        # Re-importar
        try:
            import_stats = reimport_excel(settings=target)
        except Exception as e:
            return jsonify({
                "seeded": True, "seed_stats": stats,
                "import_failed": str(e),
            }), 200
        return jsonify({
            "seeded": True,
            "user_id": user_id,
            "seed_stats": stats,
            "import_stats": import_stats,
        })

    @app.delete("/api/admin/users/<user_id>")
    def admin_delete_user(user_id):
        """Borra un user del config in-memory. NO borra los archivos del disk
        (por seguridad). Si querés borrar también, usá ?delete_data=true."""
        _require_admin()
        if user_id == g.auth_user.user_id:
            abort(400, "No podés borrar tu propio user")
        if not any(u.user_id == user_id for u in load_users()):
            abort(404, f"User '{user_id}' no existe")

        delete_data = request.args.get("delete_data") == "true"
        remove_user_from_config(user_id)

        if delete_data:
            import shutil
            try:
                ud = get_user_settings(user_id).user_data_dir
                if ud.is_dir(): shutil.rmtree(ud)
                inp = get_user_settings(user_id).inputs_dir
                if inp.is_dir(): shutil.rmtree(inp)
            except OSError as e:
                return jsonify({"removed_config": True, "data_deleted": False,
                                 "error": str(e)})
        return jsonify({"removed_config": True, "data_deleted": delete_data})

    @app.post("/api/admin/switch")
    def admin_switch():
        """Switch view del admin a otro user (read-only).

        Body: {"user_id": "amigo"}  o {"user_id": null} para volver.
        """
        _require_admin()
        body = request.get_json(silent=True) or {}
        target = body.get("user_id")
        token = g.user_token
        if target is None or target == "" or target == g.auth_user.user_id:
            admin_switch_clear(token)
            return jsonify({"switched": False, "active_user_id": g.auth_user.user_id})
        try:
            admin_switch_to(token, target)
        except (PermissionError, ValueError) as e:
            abort(400, str(e))
        return jsonify({"switched": True, "active_user_id": target,
                         "warning": "Modo read-only. POST/PUT/DELETE están bloqueados."})

    def _build_wsgi_snippet():
        """Devuelve un fragmento de código para el WSGI file con el JSON
        actual de users."""
        import os, json
        return (
            "# Reemplazar tu WM_API_TOKEN viejo por esto:\n"
            f"os.environ['WM_USERS_JSON'] = '''{os.environ.get('WM_USERS_JSON', '{}')}'''\n"
            f"os.environ['WM_ADMIN_USER'] = '{os.environ.get('WM_ADMIN_USER', '')}'\n"
        )

    # =========================================================================
    # Reports
    # =========================================================================

    @app.get("/api/report/html")
    def report_html():
        _require_auth(); _block_if_switched_mutation()
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
        _require_auth(); _block_if_switched_mutation()
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
