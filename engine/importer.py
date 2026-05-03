# -*- coding: utf-8 -*-
"""
engine/importer.py

Lee el Excel master `wealth_management_rodricor.xlsx` y popula la DB sqlite
con events + movements según el modelo de doble entrada.

Para cada hoja:
  - cuentas, monedas, especies → tablas maestras
  - blotter → events TRADE + 2 movements (activo en cuenta + cash en cuenta)
  - transferencias_cash → events TRANSFER_CASH + 2 movements
  - transferencias_activos → events TRANSFER_ASSET + 2 movements
  - ingresos → events INCOME + 2 movements (cuenta destino + external_income)
  - gastos → events EXPENSE / CARD_CHARGE + N movements (con expansión cuotas)
  - pasivos + pagos_pasivos → events LIABILITY_OPEN, LIABILITY_PAYMENT
  - asientos_contables → events ACCOUNTING_ADJUSTMENT + N movements
  - recurrentes → expande hasta hoy y genera events según frecuencia
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook

from .schema import (
    EventType,
    AccountKind,
    insert_account, insert_currency, insert_asset,
    insert_event, insert_movement,
)
from .fx import convert as fx_convert, FxError, auto_load_fx


# =============================================================================
# Helpers
# =============================================================================

def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def _to_date_str(v: Any) -> Optional[str]:
    """Convierte cell value a string ISO YYYY-MM-DD."""
    if _is_empty(v):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10]).isoformat()
        except ValueError:
            return None
    return None


def _to_float(v: Any) -> Optional[float]:
    if _is_empty(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if _is_empty(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_str(v: Any) -> Optional[str]:
    if _is_empty(v):
        return None
    return str(v).strip()


def _read_rows(ws, header_row: int = 4):
    """Itera filas de datos de una hoja, devuelve dict {header: value}.
    Saltea filas donde TODAS las columnas (excepto la primera) están vacías.
    """
    headers = []
    for c in range(1, ws.max_column + 1):
        h = ws.cell(row=header_row, column=c).value
        if isinstance(h, str):
            h = h.strip()
        headers.append(h)
    data_start = header_row + 1
    for r in range(data_start, ws.max_row + 1):
        row_vals = [ws.cell(row=r, column=c + 1).value
                    for c in range(len(headers))]
        if all(_is_empty(v) for v in row_vals):
            continue
        yield r, dict(zip(headers, row_vals))


# =============================================================================
# Importadores por hoja
# =============================================================================

def import_monedas(conn, ws):
    """Hoja 'monedas': popula tabla currencies."""
    n = 0
    for r, row in _read_rows(ws):
        code = _to_str(row.get("Code"))
        if not code:
            continue
        insert_currency(
            conn,
            code=code,
            name=_to_str(row.get("Name")) or code,
            is_stable=bool(_to_int(row.get("Is Stable")) or 0),
            quote_vs=_to_str(row.get("Quote vs")),
            is_base=bool(_to_int(row.get("Is Base")) or 0),
            notes=_to_str(row.get("Notas")),
        )
        n += 1
    return n


def import_cuentas(conn, ws):
    """Hoja 'cuentas': popula tabla accounts."""
    n = 0
    for r, row in _read_rows(ws):
        code = _to_str(row.get("Code"))
        if not code:
            continue
        insert_account(
            conn,
            code=code,
            name=_to_str(row.get("Name")) or code,
            kind=_to_str(row.get("Kind")) or AccountKind.CASH_BANK,
            institution=_to_str(row.get("Institution")),
            currency=_to_str(row.get("Currency")),
            card_cycle_kind=_to_str(row.get("Card Cycle")) or "NONE",
            card_close_day=_to_int(row.get("Close Day")),
            card_due_day=_to_int(row.get("Due Day")),
            card_currency=_to_str(row.get("Card Currency")),
            notes=_to_str(row.get("Notes")),
        )
        n += 1
    return n


def import_especies(conn, ws):
    """Hoja 'especies': popula tabla assets."""
    n = 0
    for r, row in _read_rows(ws):
        ticker = _to_str(row.get("Ticker"))
        if not ticker:
            continue
        insert_asset(
            conn,
            ticker=ticker,
            name=_to_str(row.get("Name")) or ticker,
            asset_class=_to_str(row.get("Asset Class")) or "OTHER",
            currency=_to_str(row.get("Currency")) or "ARS",
            issuer=_to_str(row.get("Issuer")),
            sector=_to_str(row.get("Sector")),
            country=_to_str(row.get("Country")),
            notes=_to_str(row.get("Notes")),
        )
        n += 1
    return n


def _get_asset_currency(conn, ticker: str) -> Optional[str]:
    """Lee la moneda nativa de un asset desde la tabla `assets`."""
    cur = conn.execute("SELECT currency FROM assets WHERE ticker=?", (ticker,))
    row = cur.fetchone()
    if row is None:
        return None
    return row["currency"] if hasattr(row, "keys") else row[0]


def import_blotter(conn, ws):
    """Hoja 'blotter': cada fila → un event TRADE + 2 movements.

    Si la moneda del trade != moneda nativa del asset, convierte el unit_price
    a la moneda nativa usando FX del día. El cash sigue moviéndose en la
    moneda real del trade. Esto mantiene:
      - inventario en moneda nativa del bono (USB para AL30, USD para AAPL)
      - cash en la moneda real que se intercambió (ARS, USB, USDT, etc)
    """
    n = 0
    for r, row in _read_rows(ws):
        ticker = _to_str(row.get("Ticker"))
        side = _to_str(row.get("Side"))
        qty = _to_float(row.get("Qty"))
        precio = _to_float(row.get("Precio"))
        moneda = _to_str(row.get("Moneda Trade"))
        cuenta = _to_str(row.get("Cuenta"))
        cuenta_cash = _to_str(row.get("Cuenta Cash")) or cuenta

        if not all([ticker, side, qty, precio, moneda, cuenta]):
            continue
        if side not in ("BUY", "SELL"):
            continue

        trade_date = _to_date_str(row.get("Trade Date"))
        settle_date = _to_date_str(row.get("Settle Date"))
        if not trade_date:
            continue

        external_id = _to_str(row.get("Trade ID"))
        description = (_to_str(row.get("Description"))
                       or f"{side} {qty} {ticker} @ {precio}")

        # FX: si la moneda del trade != moneda nativa del asset, convertir
        # el unit_price a moneda nativa para que el inventario sea consistente.
        asset_ccy = _get_asset_currency(conn, ticker)
        if asset_ccy is None:
            # Asset no existe en `assets` — usar moneda del trade como fallback
            asset_ccy = moneda

        if asset_ccy != moneda:
            try:
                # Convertir el precio unitario: precio (en moneda) → precio en asset_ccy
                precio_native = fx_convert(
                    conn, precio, moneda, asset_ccy, trade_date,
                )
            except FxError as e:
                # Falla limpia: log y skip esta fila
                print(f"[importer] WARN blotter row {r}: {e} — skipping")
                continue
        else:
            precio_native = precio

        # Crear evento
        eid = insert_event(
            conn, EventType.TRADE,
            event_date=trade_date, settle_date=settle_date,
            description=description, external_id=external_id,
            source_row=r, source_sheet="blotter",
            notes=_to_str(row.get("Notes")),
        )

        # Movements:
        # BUY: cuenta GANA ticker, cuenta_cash PIERDE cash (qty × precio_trade)
        # SELL: cuenta PIERDE ticker, cuenta_cash GANA cash
        sign = 1 if side == "BUY" else -1

        # Cost basis y unit_price del INVENTARIO en moneda nativa del asset
        cost_basis_native = qty * precio_native
        # Cash en moneda real del trade
        cash_total = qty * precio  # siempre positivo

        # Movement 1: el activo (precio en moneda nativa)
        insert_movement(
            conn, eid,
            account=cuenta, asset=ticker,
            qty=sign * qty,
            unit_price=precio_native,
            price_currency=asset_ccy,
            cost_basis=sign * cost_basis_native,
            notes=(f"FX: {moneda}→{asset_ccy} en {trade_date}"
                   if asset_ccy != moneda else None),
        )
        # Movement 2: el cash (en moneda del trade)
        insert_movement(
            conn, eid,
            account=cuenta_cash, asset=moneda,
            qty=-sign * cash_total,
        )

        # Comisión opcional
        comision = _to_float(row.get("Comisión"))
        moneda_com = _to_str(row.get("Moneda Com")) or moneda
        if comision and comision > 0:
            insert_movement(
                conn, eid,
                account=cuenta_cash, asset=moneda_com,
                qty=-comision,
                notes=f"Comisión {side}",
            )
            insert_movement(
                conn, eid,
                account="external_expense", asset=moneda_com,
                qty=comision,
                notes=f"Comisión {side}",
            )

        n += 1
    return n


def import_transferencias_cash(conn, ws):
    """Hoja 'transferencias_cash': cash entre cuentas propias."""
    n = 0
    for r, row in _read_rows(ws):
        monto = _to_float(row.get("Monto"))
        moneda = _to_str(row.get("Moneda"))
        origen = _to_str(row.get("Cuenta Origen"))
        destino = _to_str(row.get("Cuenta Destino"))
        fecha = _to_date_str(row.get("Fecha"))
        if not all([monto, moneda, origen, destino, fecha]):
            continue

        eid = insert_event(
            conn, EventType.TRANSFER_CASH,
            event_date=fecha,
            description=_to_str(row.get("Description")) or f"Transfer {origen}→{destino}",
            source_row=r, source_sheet="transferencias_cash",
            external_id=_to_str(row.get("Trade ID Externo")),
            notes=_to_str(row.get("Notes")),
        )
        insert_movement(conn, eid, account=origen,  asset=moneda, qty=-monto)
        insert_movement(conn, eid, account=destino, asset=moneda, qty=monto)
        n += 1
    return n


def import_transferencias_activos(conn, ws):
    """Hoja 'transferencias_activos': activos entre cuentas propias."""
    n = 0
    for r, row in _read_rows(ws):
        ticker = _to_str(row.get("Ticker"))
        qty = _to_float(row.get("Qty"))
        origen = _to_str(row.get("Cuenta Origen"))
        destino = _to_str(row.get("Cuenta Destino"))
        fecha = _to_date_str(row.get("Fecha"))
        if not all([ticker, qty, origen, destino, fecha]):
            continue

        eid = insert_event(
            conn, EventType.TRANSFER_ASSET,
            event_date=fecha,
            description=_to_str(row.get("Description")) or f"Transfer {ticker} {origen}→{destino}",
            source_row=r, source_sheet="transferencias_activos",
            notes=_to_str(row.get("Notes")),
        )
        insert_movement(conn, eid, account=origen,  asset=ticker, qty=-qty)
        insert_movement(conn, eid, account=destino, asset=ticker, qty=qty)
        n += 1
    return n


def import_ingresos(conn, ws):
    """Hoja 'ingresos': ingresos manuales (los recurrentes los maneja otra hoja)."""
    n = 0
    for r, row in _read_rows(ws):
        monto = _to_float(row.get("Monto"))
        moneda = _to_str(row.get("Moneda"))
        cuenta_dest = _to_str(row.get("Cuenta Destino"))
        fecha = _to_date_str(row.get("Fecha"))
        recurrente = _to_str(row.get("Recurrente?"))
        if recurrente == "YES":
            # Se carga desde 'recurrentes', skip
            continue
        if not all([monto, moneda, cuenta_dest, fecha]):
            continue

        eid = insert_event(
            conn, EventType.INCOME,
            event_date=fecha,
            description=_to_str(row.get("Concepto")) or "Ingreso",
            source_row=r, source_sheet="ingresos",
            notes=_to_str(row.get("Notes")),
        )
        insert_movement(conn, eid, account=cuenta_dest,    asset=moneda, qty=monto,
                        notes=_to_str(row.get("Categoría")))
        insert_movement(conn, eid, account="external_income", asset=moneda, qty=-monto)
        n += 1
    return n


def import_gastos(conn, ws):
    """Hoja 'gastos': gasto cash o tarjeta. Soporta cuotas (1 fila → N events).

    Si la cuenta destino es CARD_CREDIT y cuotas > 1:
      genera N CARD_CHARGE events, uno por cuota mensual a partir del cierre indicado.
    Si cuotas = 1:
      genera 1 EXPENSE (si cash) o 1 CARD_CHARGE (si tarjeta).
    """
    # Cargar lookup de cuentas para saber si es tarjeta
    accounts_kind = {}
    cur = conn.execute("SELECT code, kind, card_close_day FROM accounts")
    for a in cur.fetchall():
        accounts_kind[a["code"]] = (a["kind"], a["card_close_day"])

    n = 0
    for r, row in _read_rows(ws):
        monto = _to_float(row.get("Monto"))
        moneda = _to_str(row.get("Moneda"))
        cuenta_dest = _to_str(row.get("Cuenta Destino"))
        fecha = _to_date_str(row.get("Fecha"))
        cuotas = _to_int(row.get("Cuotas")) or 1
        recurrente = _to_str(row.get("Recurrente?"))
        if recurrente == "YES":
            continue
        if not all([monto, moneda, cuenta_dest, fecha]):
            continue

        kind, _close_day = accounts_kind.get(cuenta_dest, (None, None))
        is_card = (kind == AccountKind.CARD_CREDIT)
        concepto = _to_str(row.get("Concepto")) or "Gasto"
        categoria = _to_str(row.get("Categoría")) or ""
        tipo = _to_str(row.get("Tipo")) or "VARIABLE"  # FIJO/VARIABLE
        notes = _to_str(row.get("Notes"))

        if cuotas <= 1 or not is_card:
            # Caso simple: 1 evento
            event_type = EventType.CARD_CHARGE if is_card else EventType.EXPENSE
            eid = insert_event(
                conn, event_type,
                event_date=fecha,
                description=concepto,
                source_row=r, source_sheet="gastos",
                notes=f"{categoria} | {tipo}{' | ' + notes if notes else ''}",
            )
            if is_card:
                # CARD_CHARGE: incrementa pasivo de tarjeta (qty positiva en tarjeta)
                # contra external_expense
                insert_movement(
                    conn, eid, account=cuenta_dest, asset=moneda,
                    qty=monto,
                    notes=f"{categoria} | {tipo}",
                )
                insert_movement(
                    conn, eid, account="external_expense", asset=moneda,
                    qty=-monto,
                )
            else:
                # EXPENSE cash: cuenta destino paga (qty negativa)
                insert_movement(
                    conn, eid, account=cuenta_dest, asset=moneda,
                    qty=-monto,
                    notes=f"{categoria} | {tipo}",
                )
                insert_movement(
                    conn, eid, account="external_expense", asset=moneda,
                    qty=monto,
                )
            n += 1
        else:
            # Caso cuotas: N events CARD_INSTALLMENT
            cuota_monto = monto / cuotas
            # Parent event (la compra original como CARD_CHARGE diferida)
            parent_eid = insert_event(
                conn, EventType.CARD_CHARGE,
                event_date=fecha,
                description=f"{concepto} ({cuotas} cuotas)",
                source_row=r, source_sheet="gastos",
                notes=f"{categoria} | {tipo} | Cuotas={cuotas}",
            )
            # Generar N CARD_INSTALLMENT mensuales
            f0 = date.fromisoformat(fecha)
            for i in range(cuotas):
                # Cuota i va al mes i (mismo día)
                month_offset = i
                year = f0.year + (f0.month - 1 + month_offset) // 12
                month = (f0.month - 1 + month_offset) % 12 + 1
                try:
                    cuota_date = date(year, month, f0.day).isoformat()
                except ValueError:
                    # Día no válido para ese mes (ej 31 en febrero)
                    import calendar
                    last_day = calendar.monthrange(year, month)[1]
                    cuota_date = date(year, month, min(f0.day, last_day)).isoformat()
                eid = insert_event(
                    conn, EventType.CARD_INSTALLMENT,
                    event_date=cuota_date,
                    description=f"Cuota {i+1}/{cuotas}: {concepto}",
                    source_row=r, source_sheet="gastos",
                    parent_event_id=parent_eid,
                    notes=f"{categoria} | {tipo}",
                )
                insert_movement(
                    conn, eid, account=cuenta_dest, asset=moneda,
                    qty=cuota_monto,
                )
                insert_movement(
                    conn, eid, account="external_expense", asset=moneda,
                    qty=-cuota_monto,
                )
            n += 1
    return n


def import_recurrentes(conn, ws, fecha_corte: date):
    """Hoja 'recurrentes': expande hasta fecha_corte generando ingresos/gastos."""
    n = 0
    for r, row in _read_rows(ws):
        active = _to_str(row.get("Active"))
        if active != "YES":
            continue
        rule_name = _to_str(row.get("Rule Name"))
        event_type = _to_str(row.get("Event Type"))
        cuenta = _to_str(row.get("Cuenta"))
        asset = _to_str(row.get("Asset"))
        amount = _to_float(row.get("Amount"))
        description = _to_str(row.get("Description")) or rule_name
        categoria = _to_str(row.get("Categoría")) or ""
        tipo = _to_str(row.get("Tipo")) or ""
        start = _to_date_str(row.get("Start Date"))
        end = _to_date_str(row.get("End Date"))
        day_of_month = _to_int(row.get("Day of Month")) or 1

        if not all([rule_name, event_type, cuenta, asset, amount, start]):
            continue

        # Generar ocurrencias mensuales desde start hasta min(end, fecha_corte)
        d_start = date.fromisoformat(start)
        d_end = date.fromisoformat(end) if end else fecha_corte
        d_end = min(d_end, fecha_corte)

        # Iterar mes por mes
        y, m = d_start.year, d_start.month
        # Si d_start tiene día > day_of_month, arrancar el mes siguiente
        if d_start.day > day_of_month:
            m += 1
            if m > 12:
                m = 1; y += 1

        import calendar
        while True:
            last_day = calendar.monthrange(y, m)[1]
            d = date(y, m, min(day_of_month, last_day))
            if d > d_end:
                break
            if d >= d_start:
                # Generar evento
                if event_type == "INCOME":
                    eid = insert_event(
                        conn, EventType.INCOME,
                        event_date=d.isoformat(),
                        description=description,
                        source_row=r, source_sheet="recurrentes",
                        notes=f"Auto: {rule_name}",
                    )
                    insert_movement(conn, eid, account=cuenta, asset=asset, qty=amount,
                                    notes=categoria)
                    insert_movement(conn, eid, account="external_income", asset=asset, qty=-amount)
                elif event_type == "EXPENSE":
                    eid = insert_event(
                        conn, EventType.EXPENSE,
                        event_date=d.isoformat(),
                        description=description,
                        source_row=r, source_sheet="recurrentes",
                        notes=f"Auto: {rule_name} | {categoria} | {tipo}",
                    )
                    insert_movement(conn, eid, account=cuenta, asset=asset, qty=-amount,
                                    notes=f"{categoria} | {tipo}")
                    insert_movement(conn, eid, account="external_expense", asset=asset, qty=amount)
                elif event_type == "CARD_CHARGE":
                    eid = insert_event(
                        conn, EventType.CARD_CHARGE,
                        event_date=d.isoformat(),
                        description=description,
                        source_row=r, source_sheet="recurrentes",
                        notes=f"Auto: {rule_name} | {categoria} | {tipo}",
                    )
                    insert_movement(conn, eid, account=cuenta, asset=asset, qty=amount)
                    insert_movement(conn, eid, account="external_expense", asset=asset, qty=-amount)
                n += 1
            m += 1
            if m > 12:
                m = 1; y += 1

    return n


def import_pagos_pasivos(conn, ws):
    """Pagos a pasivos: cuotas de préstamos y cancelaciones de tarjeta."""
    n = 0
    for r, row in _read_rows(ws):
        fecha = _to_date_str(row.get("Fecha"))
        target = _to_str(row.get("Pasivo / Tarjeta"))
        monto_total = _to_float(row.get("Monto Total"))
        capital = _to_float(row.get("Capital")) or monto_total
        interes = _to_float(row.get("Interés")) or 0
        moneda = _to_str(row.get("Moneda"))
        cuenta_origen = _to_str(row.get("Cuenta Origen"))
        if not all([fecha, target, monto_total, moneda, cuenta_origen]):
            continue

        # Determinar si target es CARD_CREDIT o LIABILITY
        cur = conn.execute("SELECT kind FROM accounts WHERE code=?", (target,))
        row_acc = cur.fetchone()
        if row_acc is None:
            continue
        kind = row_acc["kind"]
        is_card = (kind == AccountKind.CARD_CREDIT)
        event_type = EventType.CARD_PAYMENT if is_card else EventType.LIABILITY_PAYMENT

        eid = insert_event(
            conn, event_type,
            event_date=fecha,
            description=_to_str(row.get("Description"))
                       or f"Pago {target} {monto_total:.2f} {moneda}",
            source_row=r, source_sheet="pagos_pasivos",
            notes=_to_str(row.get("Notes")),
        )
        # cash sale de cuenta_origen
        insert_movement(conn, eid, account=cuenta_origen, asset=moneda, qty=-monto_total)
        # tarjeta/pasivo se reduce: qty negativa (saldo decrece)
        insert_movement(conn, eid, account=target, asset=moneda, qty=-capital,
                        notes=f"Capital pagado")
        # interés (si > 0)
        if interes > 0:
            insert_movement(conn, eid, account="interest_expense", asset=moneda, qty=interes)
            # ajuste: total - capital - interés debería balancear
            # Si capital + interés ≠ total, generamos un movement de ajuste implícito
            # En el modelo simple, asumimos capital + interés = monto_total
        n += 1
    return n


def import_asientos(conn, ws):
    """Asientos contables manuales. Filas con mismo Event ID se agrupan en 1 evento."""
    grupos = {}  # event_id externo → list of rows
    for r, row in _read_rows(ws):
        eid_ext = _to_str(row.get("Event ID"))
        if not eid_ext:
            continue
        grupos.setdefault(eid_ext, []).append((r, row))

    n = 0
    for eid_ext, rows in grupos.items():
        # Tomar fecha y descripción de la primera fila
        first = rows[0][1]
        fecha = _to_date_str(first.get("Fecha"))
        desc = _to_str(first.get("Description"))
        if not fecha:
            continue
        eid = insert_event(
            conn, EventType.ACCOUNTING_ADJUSTMENT,
            event_date=fecha,
            description=desc or f"Asiento {eid_ext}",
            external_id=eid_ext,
            source_row=rows[0][0], source_sheet="asientos_contables",
        )
        # Generar todos los movements
        for r, row in rows:
            cuenta = _to_str(row.get("Cuenta"))
            asset = _to_str(row.get("Activo"))
            qty = _to_float(row.get("Qty (signada)"))
            unit_price = _to_float(row.get("Unit Price"))
            price_curr = _to_str(row.get("Price Currency"))
            if not all([cuenta, asset, qty]):
                continue
            cost_basis = qty * unit_price if unit_price else None
            insert_movement(
                conn, eid,
                account=cuenta, asset=asset, qty=qty,
                unit_price=unit_price, price_currency=price_curr,
                cost_basis=cost_basis,
                notes=_to_str(row.get("Notes")),
            )
        n += 1
    return n


# =============================================================================
# Importer principal
# =============================================================================

def import_all(db_path: str | Path, xlsx_path: str | Path,
               fecha_corte: date = None,
               fx_csv_path: str | Path = None,
               data_dir: str | Path = "data") -> dict:
    """Importa el master Excel completo a la DB. Devuelve estadísticas.

    fx_csv_path: si se pasa, importa ese CSV específicamente.
                 Si es None, intenta auto-cargar `<data_dir>/fx_historico.csv`.
    """
    from .schema import init_db
    if fecha_corte is None:
        fecha_corte = date.today()

    print(f"[importer] DB: {db_path}")
    print(f"[importer] XLSX: {xlsx_path}")
    print(f"[importer] Fecha de corte (para recurrentes): {fecha_corte}")

    # Recrear DB para idempotencia
    conn = init_db(db_path, drop_existing=True)
    wb = load_workbook(filename=str(xlsx_path), data_only=True)

    stats = {}

    # 1. Maestros (en orden de FK)
    if "monedas" in wb.sheetnames:
        stats["monedas"] = import_monedas(conn, wb["monedas"])
    if "cuentas" in wb.sheetnames:
        stats["cuentas"] = import_cuentas(conn, wb["cuentas"])
    if "especies" in wb.sheetnames:
        stats["especies"] = import_especies(conn, wb["especies"])

    conn.commit()

    # 1b. Cargar FX histórico ANTES de procesar trades
    #     (necesario para conversión cuando Moneda Trade != asset.currency)
    if fx_csv_path:
        from .fx import import_fx_csv
        stats["fx_rates"] = import_fx_csv(conn, fx_csv_path)
    else:
        stats["fx_rates"] = auto_load_fx(conn, data_dir)
    conn.commit()

    # 2. Eventos
    if "blotter" in wb.sheetnames:
        stats["blotter"] = import_blotter(conn, wb["blotter"])
    if "transferencias_cash" in wb.sheetnames:
        stats["transferencias_cash"] = import_transferencias_cash(conn, wb["transferencias_cash"])
    if "transferencias_activos" in wb.sheetnames:
        stats["transferencias_activos"] = import_transferencias_activos(conn, wb["transferencias_activos"])
    if "ingresos" in wb.sheetnames:
        stats["ingresos"] = import_ingresos(conn, wb["ingresos"])
    if "gastos" in wb.sheetnames:
        stats["gastos"] = import_gastos(conn, wb["gastos"])
    if "recurrentes" in wb.sheetnames:
        stats["recurrentes"] = import_recurrentes(conn, wb["recurrentes"], fecha_corte)
    if "pagos_pasivos" in wb.sheetnames:
        stats["pagos_pasivos"] = import_pagos_pasivos(conn, wb["pagos_pasivos"])
    if "asientos_contables" in wb.sheetnames:
        stats["asientos_contables"] = import_asientos(conn, wb["asientos_contables"])

    conn.commit()
    conn.close()
    return stats


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("USO: python -m engine.importer <xlsx_path> <db_path>")
        sys.exit(1)
    stats = import_all(sys.argv[2], sys.argv[1])
    for k, v in stats.items():
        print(f"  {k}: {v}")
