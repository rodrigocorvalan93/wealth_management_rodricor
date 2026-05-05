# -*- coding: utf-8 -*-
"""
engine/holdings.py

Calculadora de Holdings (tenencias actuales) del portfolio.

Para cada (cuenta × asset) con saldo > 0:
  1. qty                — cantidad neta
  2. avg_cost           — costo promedio ponderado de las posiciones abiertas
  3. cost_basis_total   — qty × avg_cost (en moneda nativa del activo)
  4. market_price       — precio de mercado en `prices` (o cost basis como fallback)
  5. mv_native          — qty × market_price (moneda nativa del activo)
  6. mv_anchor          — mv_native convertido a moneda ancla (USD CCL por default)
  7. unrealized_pnl     — (market_price - avg_cost) × qty
  8. unrealized_pct     — unrealized_pnl / cost_basis_total
  9. price_source       — "byma", "yfinance", "coingecko", "cost_basis_fallback"

Uso:
    from engine.holdings import calculate_holdings
    holdings = calculate_holdings(conn, fecha=date.today(), anchor_currency="USD")
    for h in holdings:
        print(h)
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Optional

from .prices import get_price, get_latest_price
from .fx import convert as fx_convert, FxError


# Cuentas técnicas que NO son tenencias reales del portfolio
SYSTEM_ACCOUNTS = {
    "opening_balance",
    "external_income",
    "external_expense",
    "interest_income",
    "interest_expense",
}

# Cuentas que representan PASIVOS (deudas). Su saldo positivo = lo que debés.
# En el cómputo de PN se RESTAN (PN = activos - pasivos).
LIABILITY_KINDS = {"LIABILITY", "CARD_CREDIT"}

# Activos que son cash (la moneda misma usada como activo en movements)
def _is_cash_asset(ticker, currencies_set):
    """True si el ticker es una moneda (cash) y no un activo financiero."""
    return ticker in currencies_set


def _load_currencies(conn):
    """Devuelve set de códigos de moneda para detectar cash assets."""
    cur = conn.execute("SELECT code FROM currencies")
    return set(r["code"] for r in cur.fetchall())


def _load_account_meta(conn):
    """Devuelve dict {code: {name, kind, investible, cash_purpose, currency,
    institution}} desde accounts. `name` es el display name (para UI)."""
    out = {}
    try:
        cur = conn.execute(
            "SELECT code, name, kind, investible, cash_purpose, currency, institution "
            "FROM accounts"
        )
        for r in cur.fetchall():
            out[r["code"]] = {
                "name": r["name"] or r["code"],
                "kind": r["kind"],
                "investible": bool(r["investible"]),
                "cash_purpose": r["cash_purpose"],
                "currency": r["currency"],
                "institution": r["institution"],
            }
    except Exception:
        # Schema viejo sin las columnas — degradar a defaults
        cur = conn.execute("SELECT code, name, kind FROM accounts")
        for r in cur.fetchall():
            out[r["code"]] = {
                "name": r["name"] or r["code"],
                "kind": r["kind"],
                "investible": True,
                "cash_purpose": None,
                "currency": None,
                "institution": None,
            }
    return out


def _load_asset_map(conn):
    """Devuelve dict {ticker: {currency, asset_class, name}}."""
    out = {}
    cur = conn.execute(
        "SELECT ticker, currency, asset_class, name FROM assets"
    )
    for r in cur.fetchall():
        out[r["ticker"]] = {
            "currency": r["currency"],
            "asset_class": r["asset_class"],
            "name": r["name"],
        }
    return out


def _load_all_active_targets(conn):
    """Pre-carga targets/stops del BUY más reciente para CADA (account, asset)
    en un solo query. Devuelve dict {(account, asset): target_dict}.

    Es la versión batch de _get_active_target — evita N+1 cuando hay
    muchos holdings.
    """
    out = {}
    cur = conn.execute(
        """
        SELECT m.account, m.asset, e.target_price, e.stop_loss_price,
               e.target_currency, e.event_date, e.event_id
        FROM events e
        JOIN movements m ON m.event_id = e.event_id
        WHERE e.event_type = 'TRADE'
          AND m.qty > 0
          AND (e.target_price IS NOT NULL OR e.stop_loss_price IS NOT NULL)
        ORDER BY m.account, m.asset, e.event_date DESC, e.event_id DESC
        """
    )
    for r in cur.fetchall():
        key = (r["account"], r["asset"])
        # ORDER DESC dentro de cada (account, asset): la primera fila es la
        # más reciente. Los siguientes para el mismo key se ignoran.
        if key in out:
            continue
        out[key] = {
            "target_price": r["target_price"],
            "stop_loss_price": r["stop_loss_price"],
            "target_currency": r["target_currency"],
            "set_at": r["event_date"],
        }
    return out


def _get_active_target(conn, account, asset, _cache=None):
    """Wrapper compatible: usa cache batch si se pasa, si no hace el query
    individual (back-compat con tests viejos que llaman directamente)."""
    if _cache is not None:
        return _cache.get((account, asset))
    cur = conn.execute(
        """
        SELECT e.target_price, e.stop_loss_price, e.target_currency, e.event_date
        FROM events e
        JOIN movements m ON m.event_id = e.event_id
        WHERE e.event_type = 'TRADE'
          AND m.account = ? AND m.asset = ?
          AND m.qty > 0
          AND (e.target_price IS NOT NULL OR e.stop_loss_price IS NOT NULL)
        ORDER BY e.event_date DESC, e.event_id DESC
        LIMIT 1
        """,
        (account, asset),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "target_price": row["target_price"],
        "stop_loss_price": row["stop_loss_price"],
        "target_currency": row["target_currency"],
        "set_at": row["event_date"],
    }


def _calc_position(conn, account, asset, fecha_iso):
    """Calcula posición agregada para (account, asset) hasta fecha_iso.

    Devuelve dict {qty, cost_basis_total, avg_cost} o None si saldo cero.

    Para avg_cost usa weighted average de COMPRAS (no FIFO estricto;
    para FIFO realizado/no-realizado se usa engine/pnl.py).
    """
    cur = conn.execute(
        """
        SELECT m.qty, m.unit_price, m.price_currency, e.event_date
        FROM movements m
        JOIN events e ON e.event_id = m.event_id
        WHERE m.account = ? AND m.asset = ? AND e.event_date <= ?
        ORDER BY e.event_date, m.movement_id
        """,
        (account, asset, fecha_iso),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    qty_neto = 0.0
    cost_total_acum = 0.0  # suma de qty_compra * unit_price
    qty_compra_acum = 0.0  # qty positiva acumulada (para WAC)

    for r in rows:
        qty = r["qty"]
        unit_price = r["unit_price"]
        qty_neto += qty
        if qty > 0 and unit_price is not None:
            cost_total_acum += qty * unit_price
            qty_compra_acum += qty

    if abs(qty_neto) < 1e-9:
        return None  # posición cerrada

    avg_cost = (cost_total_acum / qty_compra_acum) if qty_compra_acum > 0 else None
    cost_basis_total = (qty_neto * avg_cost) if avg_cost is not None else None

    return {
        "qty": qty_neto,
        "avg_cost": avg_cost,
        "cost_basis_total": cost_basis_total,
    }


def _resolve_market_price(conn, ticker, fecha_iso, fallback_to_cost=None):
    """Busca precio de mercado. Si no hay, usa fallback (cost basis).

    Devuelve dict {price, currency, source, fecha_efectiva, fallback_used}.
    """
    p = get_price(conn, ticker, fecha_iso, fallback_days=14)
    if p:
        return {
            "price": p["price"],
            "currency": p["currency"],
            "source": p["source"],
            "fecha_efectiva": p["fecha_efectiva"],
            "fallback_used": p.get("fallback_used", False),
        }
    # Fallback al último precio disponible (sin filtro de fecha)
    latest = get_latest_price(conn, ticker)
    if latest:
        return {
            "price": latest["price"],
            "currency": latest["currency"],
            "source": latest["source"],
            "fecha_efectiva": latest["fecha_efectiva"],
            "fallback_used": True,
        }
    # Último fallback: cost basis
    if fallback_to_cost is not None:
        return {
            "price": fallback_to_cost["price"],
            "currency": fallback_to_cost["currency"],
            "source": "cost_basis_fallback",
            "fecha_efectiva": None,
            "fallback_used": True,
        }
    return None


def _convert_to_anchor(conn, value, from_ccy, to_ccy, fecha):
    """Convierte un valor a la moneda ancla. Devuelve (value_anchor, ok)."""
    if from_ccy == to_ccy:
        return (value, True)
    try:
        converted = fx_convert(conn, value, from_ccy, to_ccy, fecha, fallback_days=14)
        return (converted, True)
    except FxError:
        return (None, False)


def calculate_holdings(conn, fecha=None, anchor_currency="USD"):
    """Calcula holdings del portfolio.

    Args:
        conn: sqlite connection
        fecha: fecha de corte (default: hoy)
        anchor_currency: moneda ancla para valuación (default USD = CCL)

    Returns:
        Lista de dicts con cada posición:
        {
            account, asset, asset_class, name,
            qty, avg_cost, cost_basis_total, native_currency,
            market_price, market_currency, price_source, price_date, price_fallback,
            mv_native, mv_anchor, mv_anchor_ok,
            unrealized_pnl_native, unrealized_pct,
            is_cash,
        }
    """
    if fecha is None:
        fecha = date.today()
    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else str(fecha)

    conn.row_factory = sqlite3.Row
    currencies_set = _load_currencies(conn)
    asset_map = _load_asset_map(conn)
    assets_set = set(asset_map.keys())
    account_meta = _load_account_meta(conn)
    # Pre-load targets en un solo query (evita N+1 si hay muchos holdings)
    targets_cache = _load_all_active_targets(conn)

    # Encontrar todas las (cuenta, activo) que tengan al menos un movement
    cur = conn.execute(
        """
        SELECT DISTINCT account, asset
        FROM movements
        WHERE account NOT IN ({0})
        """.format(",".join("?" for _ in SYSTEM_ACCOUNTS)),
        tuple(SYSTEM_ACCOUNTS),
    )
    pairs = [(r["account"], r["asset"]) for r in cur.fetchall()]

    holdings = []
    for account, asset in pairs:
        pos = _calc_position(conn, account, asset, fecha_iso)
        if pos is None:
            continue

        # Cash = está en currencies Y NO en assets (ej ARS, USB, USD, EUR)
        # Activos cripto (BTC, ETH, USDT, USDC) están en AMBOS — son activos
        is_cash = asset in currencies_set and asset not in assets_set

        if is_cash:
            # Cash: no requiere precio (vale 1 por unidad de su moneda)
            native_ccy = asset
            mv_native = pos["qty"]
            mv_native_currency = native_ccy
            market_price = 1.0
            price_source = "cash"
            price_date = fecha_iso
            price_fallback = False
            avg_cost = 1.0
            cost_basis_total = pos["qty"]
            unrealized_pnl_native = 0.0
            unrealized_pct = 0.0
            asset_class = "CASH"
            name = asset
        else:
            # Activo: necesita precio de mercado
            asset_info = asset_map.get(asset)
            if asset_info is None:
                # asset no está en `assets` table — skipeamos con warning
                continue
            native_ccy = asset_info["currency"]
            asset_class = asset_info["asset_class"]
            name = asset_info["name"]

            # Precio de mercado (con fallback a cost basis)
            cost_fallback = None
            if pos["avg_cost"] is not None:
                cost_fallback = {
                    "price": pos["avg_cost"],
                    "currency": native_ccy,
                }

            mp = _resolve_market_price(conn, asset, fecha_iso, fallback_to_cost=cost_fallback)
            if mp is None:
                # Sin precio NI cost basis: skip
                continue

            market_price = mp["price"]
            price_source = mp["source"]
            price_date = mp["fecha_efectiva"]
            price_fallback = mp["fallback_used"]

            # Si el precio viene en moneda distinta a la nativa, convertir.
            # Si la conversión falla, mantenemos `market_price` en su moneda
            # original (mp["currency"]) y trackeamos esa moneda como "fuente"
            # para la conversión a ancla — sin esto, mv_native quedaría
            # numéricamente "en native_ccy" pero usando un valor de otra
            # moneda, y la conversión a ancla aplicaría una tasa equivocada.
            mv_native_currency = native_ccy
            if mp["currency"] != native_ccy:
                try:
                    market_price = fx_convert(
                        conn, market_price, mp["currency"], native_ccy, fecha,
                        fallback_days=14,
                    )
                except FxError:
                    price_fallback = True
                    price_source = (price_source or "?") + f" (sin FX {mp['currency']}→{native_ccy})"
                    # market_price sigue en mp["currency"]; usamos esa moneda
                    # como source de la conversión a ancla.
                    mv_native_currency = mp["currency"]

            mv_native = pos["qty"] * market_price
            avg_cost = pos["avg_cost"] if pos["avg_cost"] else market_price
            cost_basis_total = pos["cost_basis_total"] if pos["cost_basis_total"] else mv_native
            unrealized_pnl_native = (market_price - avg_cost) * pos["qty"]
            unrealized_pct = (
                (unrealized_pnl_native / cost_basis_total)
                if cost_basis_total and cost_basis_total != 0
                else 0.0
            )

        # Convertir MV a moneda ancla. Si la conversión price→native falló
        # arriba, mv_native_currency != native_ccy y acá usamos la moneda
        # real del precio para no aplicar la tasa equivocada.
        mv_anchor, mv_anchor_ok = _convert_to_anchor(
            conn, mv_native, mv_native_currency, anchor_currency, fecha
        )

        meta = account_meta.get(account, {
            "name": account, "investible": True, "cash_purpose": None,
            "kind": None, "currency": None, "institution": None,
        })
        is_liability = meta["kind"] in LIABILITY_KINDS

        # Target / Stop-loss (solo activos no-cash)
        target_info = None
        target_price = None
        stop_loss_price = None
        target_currency = None
        dist_to_target_bps = None
        dist_to_stop_bps = None
        if not is_cash:
            target_info = _get_active_target(conn, account, asset,
                                                _cache=targets_cache)
            if target_info:
                target_currency = target_info["target_currency"] or native_ccy
                # Si target está en otra moneda, convertir a moneda nativa para
                # comparar contra market_price (que está en moneda nativa).
                tp_native = target_info["target_price"]
                sl_native = target_info["stop_loss_price"]
                if target_currency != native_ccy:
                    if tp_native is not None:
                        try:
                            tp_native = fx_convert(conn, tp_native, target_currency,
                                                    native_ccy, fecha, fallback_days=14)
                        except FxError:
                            tp_native = None
                    if sl_native is not None:
                        try:
                            sl_native = fx_convert(conn, sl_native, target_currency,
                                                    native_ccy, fecha, fallback_days=14)
                        except FxError:
                            sl_native = None
                target_price = tp_native
                stop_loss_price = sl_native
                # Distancia en bps (basis points = 1/100 de %).
                # dist_to_target_bps > 0  → market PASÓ el target (alerta TP).
                # dist_to_target_bps = 0  → exactamente en target.
                # dist_to_target_bps < 0  → falta para llegar.
                if target_price and target_price > 0:
                    dist_to_target_bps = ((market_price - target_price) / target_price) * 10000
                # dist_to_stop_bps > 0  → market está por ENCIMA del stop (safe).
                # dist_to_stop_bps < 0  → market PERFORÓ el stop (alerta SL).
                if stop_loss_price and stop_loss_price > 0:
                    dist_to_stop_bps = ((market_price - stop_loss_price) / stop_loss_price) * 10000

        # PN-signed: pasivos restan al PN. Activos suman.
        # mv_anchor sigue siendo el "balance" (siempre positivo si tenés saldo).
        # mv_pn_anchor es el aporte SIGNADO al PN (negativo para pasivos).
        if is_liability and mv_anchor is not None:
            mv_pn_anchor = -mv_anchor
            mv_pn_native = -mv_native if mv_native is not None else None
        else:
            mv_pn_anchor = mv_anchor
            mv_pn_native = mv_native

        holdings.append({
            "account": account,
            "asset": asset,
            "asset_class": asset_class,
            "name": name,
            "qty": pos["qty"],
            "avg_cost": avg_cost,
            "cost_basis_total": cost_basis_total,
            "native_currency": native_ccy,
            "market_price": market_price,
            "market_currency": native_ccy,
            "price_source": price_source,
            "price_date": price_date,
            "price_fallback": price_fallback,
            "mv_native": mv_native,
            "mv_anchor": mv_anchor,
            "mv_anchor_ok": mv_anchor_ok,
            "mv_pn_anchor": mv_pn_anchor,    # signed: pasivos negativos
            "mv_pn_native": mv_pn_native,
            "anchor_currency": anchor_currency,
            "unrealized_pnl_native": unrealized_pnl_native,
            "unrealized_pct": unrealized_pct,
            "is_cash": is_cash,
            "is_liability": is_liability,
            "investible": meta["investible"],
            "cash_purpose": meta["cash_purpose"],
            "account_kind": meta["kind"],
            "account_name": meta.get("name") or account,
            "account_institution": meta.get("institution"),
            # Target / Stop (None si no fueron seteados o si es cash)
            "target_price": target_price,
            "stop_loss_price": stop_loss_price,
            "target_currency": target_currency,
            "dist_to_target_bps": dist_to_target_bps,
            "dist_to_stop_bps": dist_to_stop_bps,
        })

    # Ordenar por mv_anchor desc (los más grandes primero)
    holdings.sort(
        key=lambda h: -(h["mv_anchor"] if h["mv_anchor"] else 0)
    )
    return holdings


def total_pn(holdings, anchor_currency="USD"):
    """Calcula PN total del portfolio en moneda ancla.

    PN = activos - pasivos (cuentas LIABILITY y CARD_CREDIT restan).

    Devuelve dict:
        {
            total_anchor: float,            # PN total NETO (activos - pasivos)
            total_investible: float,        # PN solo cuentas invertibles, neto de pasivos
            total_non_investible: float,    # PN cuentas excluidas (también neto)
            total_assets: float,            # solo activos (sin restar pasivos)
            total_liabilities: float,       # total pasivos (positivo)
            total_anchor_ok: float,         # alias compat
            total_unconverted_count: int,   # posiciones sin FX
            unconverted: [(asset, native_ccy, mv_native), ...],
        }
    """
    total = 0.0
    total_inv = 0.0
    total_non_inv = 0.0
    total_assets = 0.0
    total_liab = 0.0
    unconverted = []
    for h in holdings:
        if h["mv_anchor_ok"] and h.get("mv_pn_anchor") is not None:
            mv_pn = h["mv_pn_anchor"]
            total += mv_pn
            if h.get("investible", True):
                total_inv += mv_pn
            else:
                total_non_inv += mv_pn
            if h.get("is_liability"):
                total_liab += abs(h["mv_anchor"])
            else:
                total_assets += h["mv_anchor"]
        else:
            unconverted.append((h["asset"], h["native_currency"], h["mv_native"]))
    return {
        "total_anchor": total,
        "total_investible": total_inv,
        "total_non_investible": total_non_inv,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "total_anchor_ok": total,
        "anchor_currency": anchor_currency,
        "total_unconverted_count": len(unconverted),
        "unconverted": unconverted,
    }


def by_asset_class(holdings):
    """Agrupa holdings por asset_class. Pasivos restan (mv_pn_anchor)."""
    out = {}
    for h in holdings:
        v = h.get("mv_pn_anchor")
        if v is None:
            continue
        cls = h["asset_class"] or "UNKNOWN"
        out[cls] = out.get(cls, 0.0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def by_account(holdings):
    """Agrupa holdings por account. Pasivos restan (mv_pn_anchor)."""
    out = {}
    for h in holdings:
        v = h.get("mv_pn_anchor")
        if v is None:
            continue
        out[h["account"]] = out.get(h["account"], 0.0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def by_currency(holdings):
    """Agrupa holdings por native_currency. Pasivos restan (mv_pn_anchor)."""
    out = {}
    for h in holdings:
        v = h.get("mv_pn_anchor")
        if v is None:
            continue
        ccy = h["native_currency"]
        out[ccy] = out.get(ccy, 0.0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def filter_assets(holdings):
    """Devuelve solo holdings de cuentas-activo (NO pasivos)."""
    return [h for h in holdings if not h.get("is_liability")]


def filter_liabilities(holdings):
    """Devuelve solo holdings de cuentas-pasivo (LIABILITY, CARD_CREDIT)."""
    return [h for h in holdings if h.get("is_liability")]


# =============================================================================
# Filtros invertible (Sprint B)
# =============================================================================

def filter_investible(holdings):
    """Devuelve solo holdings de cuentas marcadas como invertibles.

    Excluye típicamente:
      - cuentas técnicas (external_*, opening_balance, interest_*)
      - cash de reserva no declarado
      - cuentas con investible=0
    """
    return [h for h in holdings if h.get("investible", True)]


def filter_non_investible(holdings):
    """Devuelve solo holdings NO invertibles (lo opuesto a filter_investible)."""
    return [h for h in holdings if not h.get("investible", True)]


def filter_near_target(holdings, alert_distance_bps: float = 10.0):
    """Devuelve holdings con target/stop alert activo.

    alert_distance_bps: distancia en bps (1 bp = 0.01%) que define "cerca".
                       Default 10 bps = 0.10%.

    Para CADA holding evalúa:
      - TP: dist_to_target_bps >= -alert_distance_bps  → cerca o pasó target
      - SL: dist_to_stop_bps   <=  +alert_distance_bps → cerca o perforó stop

    Devuelve la lista con un campo adicional `alert` ∈ {'TP', 'SL'}.
    Holdings sin target/stop o lejos del trigger no se incluyen.
    """
    out = []
    for h in holdings:
        alert = None
        d_tp = h.get("dist_to_target_bps")
        d_sl = h.get("dist_to_stop_bps")
        if d_tp is not None and d_tp >= -alert_distance_bps:
            alert = "TP"
        elif d_sl is not None and d_sl <= alert_distance_bps:
            # Cuidado: puede coexistir con TP si target < stop (raro), pero
            # priorizamos TP arriba.
            alert = "SL"
        if alert:
            h2 = dict(h)
            h2["alert"] = alert
            out.append(h2)
    return out


def by_cash_purpose(holdings):
    """Agrupa cash holdings POR PROPÓSITO (excluye pasivos).

    Útil para distinguir 'OPERATIVO' vs 'RESERVA_NO_DECLARADO'.
    Devuelve {purpose: total_anchor}.
    """
    out = {}
    for h in holdings:
        if not h.get("is_cash"):
            continue
        if h.get("is_liability"):
            continue   # pasivos van a su sección propia
        if h["mv_anchor"] is None:
            continue
        key = h.get("cash_purpose") or "(sin clasificar)"
        out[key] = out.get(key, 0.0) + h["mv_anchor"]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
