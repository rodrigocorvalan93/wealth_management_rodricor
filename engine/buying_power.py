# -*- coding: utf-8 -*-
"""
engine/buying_power.py

Cálculo del PODER DE COMPRA por cuenta.

Dos modelos:

1) BYMA / Cocos / Eco — basado en AFOROS:
   La cámara compensadora (BYMA) define un aforo por instrumento (en
   porcentaje sobre el valor de mercado). Ese aforo es la fracción del
   valor que puede usarse como GARANTÍA para tomar caución.

   - garantia_disponible = SUM( mv_anchor * aforo_pct ) por cada holding
                           dentro de la cuenta
   - cash_disponible     = mv del cash de la misma cuenta (también puede
                           garantizarse, típicamente al 100%)
   - poder_de_compra     = cash + garantia_disponible (lo que podés usar
                           para comprar más, tomando caución por la diferencia)

   NOTA: el aforo es publicado y modificado periódicamente por BYMA. Los
   valores que se cargan en la hoja `aforos` son una aproximación; el broker
   puede aplicar haircuts adicionales por riesgo crediticio/operativo.

2) IBKR / cuentas con MARGIN:
   El broker permite multiplicar el cash + valor de portfolio por un
   `multiplier` (RegT estándar: x2 overnight, x4 intraday day-trade).
   Sobre la diferencia se paga una `funding_rate_annual`.

   IMPORTANTE: el cálculo de RegT real es más complejo (margin maintenance,
   margin requirement por instrumento, SMA, etc.). Esta implementación
   asume el caso base configurable por cuenta. **Verificá los parámetros
   reales con tu cuenta IBKR antes de operar apalancado.**

USO:
    from engine.buying_power import buying_power_byma, buying_power_margin
    bp = buying_power_byma(conn, holdings, account='cocos', anchor_currency='USD')
    bp_ibkr = buying_power_margin(conn, holdings, account='ibkr',
                                   mode='overnight', anchor_currency='USD')
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional


# Aforos por defecto (BYMA, aproximados — sobreescribibles vía hoja `aforos`).
# Fuente: tabla pública de aforos de garantías BYMA (vigente al 2026-Q1).
# Se entiende como porcentaje del valor de mercado aceptado como garantía.
DEFAULT_AFOROS_BY_CLASS = {
    "BOND_AR":    0.85,   # bonos soberanos AR (AL30, GD30) — alto aforo
    "EQUITY_AR":  0.70,   # acciones líderes Merval
    "EQUITY_US":  0.70,   # CEDEARs
    "FCI":        0.90,   # Money Market FCIs
    "STABLECOIN": 0.50,   # USDT / USDC (no típicamente aceptado)
    "CRYPTO":     0.0,    # cripto NO aceptado como garantía BYMA
    "DERIVATIVE": 0.0,    # derivados no aceptados
    "CASH":       1.00,   # cash en moneda local: 100%
    "OTHER":      0.0,
}

# Cash en moneda extranjera dentro de cuenta argentina: aforo típico
DEFAULT_CASH_AFORO = 1.00

# Cuentas con margin "estilo IBKR" RegT — defaults conservadores.
# El usuario debe verificarlos contra su cuenta real.
IBKR_DEFAULT_MULT_OVERNIGHT = 2.0   # RegT: 50% margin requirement → x2
IBKR_DEFAULT_MULT_INTRADAY = 4.0    # Day-trading buying power
IBKR_DEFAULT_FUNDING_RATE = 0.06    # ~6% anual estimado en USD


@dataclass
class BuyingPowerByma:
    """Resultado del cálculo de poder de compra estilo BYMA."""
    account: str
    anchor_currency: str
    cash_total: float                    # cash en la cuenta (en ancla)
    holdings_mv: float                   # MV de holdings no-cash
    garantia_holdings: float             # SUM(mv * aforo) de holdings
    garantia_cash: float                 # cash * aforo_cash (típicamente 100%)
    garantia_total: float                # garantia_holdings + garantia_cash
    poder_de_compra: float               # capacidad para comprar más
    leverage_ratio: float                # poder_de_compra / (cash + holdings_mv)
    detalle_por_holding: list            # detalle por activo

    def to_dict(self):
        return asdict(self)


@dataclass
class BuyingPowerMargin:
    """Resultado del cálculo de poder de compra con margin (IBKR)."""
    account: str
    anchor_currency: str
    equity: float                        # cash + holdings (en ancla)
    multiplier: float                    # ej 2.0 overnight, 4.0 intraday
    mode: str                            # 'overnight' | 'intraday'
    poder_de_compra: float               # equity * multiplier
    margin_disponible: float             # poder_de_compra - equity
    funding_rate_annual: float           # tasa anual decimal
    funding_cost_per_day: float          # costo si usás todo el margen 1 día
    funding_currency: Optional[str]
    notes: str = ""

    def to_dict(self):
        return asdict(self)


# =============================================================================
# Aforos lookup
# =============================================================================

def _load_aforos(conn) -> dict:
    """Carga aforos desde DB. Retorna dict con dos sub-dicts:
    {'CLASS': {asset_class: aforo}, 'TICKER': {ticker: aforo}}.

    Si la tabla no existe (schema viejo), retorna defaults vacíos.
    """
    out = {"CLASS": {}, "TICKER": {}}
    try:
        cur = conn.execute(
            "SELECT scope_type, scope_value, aforo_pct FROM aforos"
        )
        for r in cur.fetchall():
            out[r["scope_type"]][r["scope_value"]] = r["aforo_pct"]
    except sqlite3.OperationalError:
        pass
    return out


def get_aforo_for(asset_class, ticker, aforos_db) -> float:
    """Resuelve aforo: primero busca por TICKER, después por CLASS, default por hardcode."""
    if ticker and ticker in aforos_db.get("TICKER", {}):
        return aforos_db["TICKER"][ticker]
    if asset_class and asset_class in aforos_db.get("CLASS", {}):
        return aforos_db["CLASS"][asset_class]
    return DEFAULT_AFOROS_BY_CLASS.get(asset_class or "OTHER", 0.0)


# =============================================================================
# BYMA / Cocos / Eco
# =============================================================================

def buying_power_byma(conn, holdings, account, anchor_currency="USD",
                       cash_aforo=DEFAULT_CASH_AFORO):
    """Calcula poder de compra para una cuenta BYMA (Cocos, Eco).

    Args:
        conn: sqlite (para leer aforos custom)
        holdings: lista de holdings (de calculate_holdings)
        account: code de la cuenta (ej 'cocos')
        anchor_currency: moneda en la que reportar
        cash_aforo: aforo aplicado al cash (default 100%)

    Returns:
        BuyingPowerByma con detalle por holding.
    """
    aforos_db = _load_aforos(conn)

    cash_total = 0.0
    holdings_mv = 0.0
    garantia_holdings = 0.0
    detalle = []

    for h in holdings:
        if h["account"] != account:
            continue
        if not h["mv_anchor_ok"] or h["mv_anchor"] is None:
            continue
        mv = h["mv_anchor"]

        if h["is_cash"]:
            cash_total += mv
            detalle.append({
                "asset": h["asset"],
                "is_cash": True,
                "asset_class": "CASH",
                "mv_anchor": mv,
                "aforo_pct": cash_aforo,
                "garantia": mv * cash_aforo,
            })
        else:
            aforo = get_aforo_for(h["asset_class"], h["asset"], aforos_db)
            g = mv * aforo
            holdings_mv += mv
            garantia_holdings += g
            detalle.append({
                "asset": h["asset"],
                "is_cash": False,
                "asset_class": h["asset_class"],
                "mv_anchor": mv,
                "aforo_pct": aforo,
                "garantia": g,
            })

    garantia_cash = cash_total * cash_aforo
    garantia_total = garantia_holdings + garantia_cash

    # Poder de compra: si tomás caución por la garantía completa, podés comprar
    # hasta el monto que esa garantía cubra. En el caso simple, garantía =
    # poder de compra adicional vía caución, MÁS el cash propio que ya tenías.
    # Muchos brokers exponen "BP = cash + garantía_holdings" porque el cash ya
    # está disponible para comprar sin caución.
    poder_de_compra = cash_total + garantia_holdings

    equity = cash_total + holdings_mv
    leverage_ratio = (poder_de_compra / equity) if equity > 0 else 0.0

    detalle.sort(key=lambda d: -d["garantia"])

    return BuyingPowerByma(
        account=account,
        anchor_currency=anchor_currency,
        cash_total=cash_total,
        holdings_mv=holdings_mv,
        garantia_holdings=garantia_holdings,
        garantia_cash=garantia_cash,
        garantia_total=garantia_total,
        poder_de_compra=poder_de_compra,
        leverage_ratio=leverage_ratio,
        detalle_por_holding=detalle,
    )


# =============================================================================
# IBKR / margin
# =============================================================================

def _load_margin_config(conn, account):
    """Devuelve config de margin para account, o None si no existe."""
    try:
        cur = conn.execute(
            """SELECT multiplier_overnight, multiplier_intraday,
                      funding_rate_annual, funding_currency, notes
               FROM margin_config WHERE account = ?""",
            (account,),
        )
        row = cur.fetchone()
        if row:
            return {
                "multiplier_overnight": row["multiplier_overnight"],
                "multiplier_intraday": row["multiplier_intraday"],
                "funding_rate_annual": row["funding_rate_annual"],
                "funding_currency": row["funding_currency"],
                "notes": row["notes"],
            }
    except sqlite3.OperationalError:
        pass
    return None


def buying_power_margin(conn, holdings, account, anchor_currency="USD",
                         mode="overnight", multiplier=None,
                         funding_rate_annual=None):
    """Calcula poder de compra para cuenta con margin (estilo IBKR RegT).

    Args:
        conn: sqlite (para leer margin_config)
        holdings: lista de holdings
        account: code de la cuenta (ej 'ibkr')
        anchor_currency: moneda de reporte
        mode: 'overnight' o 'intraday'
        multiplier: override del config (None = usa config o defaults)
        funding_rate_annual: override del config

    Returns:
        BuyingPowerMargin
    """
    cfg = _load_margin_config(conn, account) or {}

    if multiplier is None:
        if mode == "intraday":
            multiplier = cfg.get("multiplier_intraday") or IBKR_DEFAULT_MULT_INTRADAY
        else:
            multiplier = cfg.get("multiplier_overnight") or IBKR_DEFAULT_MULT_OVERNIGHT

    if funding_rate_annual is None:
        funding_rate_annual = cfg.get("funding_rate_annual")
        if funding_rate_annual in (None, 0.0):
            funding_rate_annual = IBKR_DEFAULT_FUNDING_RATE

    # Equity de la cuenta = SUM mv_anchor de la cuenta (cash + holdings)
    equity = 0.0
    for h in holdings:
        if h["account"] != account:
            continue
        if not h["mv_anchor_ok"] or h["mv_anchor"] is None:
            continue
        equity += h["mv_anchor"]

    poder_de_compra = equity * multiplier
    margin_disp = poder_de_compra - equity
    funding_cost_day = (margin_disp * funding_rate_annual) / 365.0

    return BuyingPowerMargin(
        account=account,
        anchor_currency=anchor_currency,
        equity=equity,
        multiplier=multiplier,
        mode=mode,
        poder_de_compra=poder_de_compra,
        margin_disponible=margin_disp,
        funding_rate_annual=funding_rate_annual,
        funding_cost_per_day=funding_cost_day,
        funding_currency=cfg.get("funding_currency"),
        notes=cfg.get("notes") or "",
    )


# =============================================================================
# Reporte combinado
# =============================================================================

def buying_power_summary(conn, holdings, anchor_currency="USD"):
    """Calcula buying power para todas las cuentas relevantes en una corrida.

    - Cuentas con entry en margin_config: usan margin (IBKR-style).
    - Cuentas con kind='CASH_BROKER' que no estén en margin_config: usan BYMA-style aforos.

    Returns:
        list de dicts con tipo y resultado para cada cuenta evaluada.
    """
    # Cargar accounts + sus kinds + qué cuentas tienen margin_config
    out = []

    cur = conn.execute("SELECT code, kind FROM accounts WHERE kind IN ('CASH_BROKER','CASH_BANK')")
    cuentas = [(r["code"], r["kind"]) for r in cur.fetchall()]

    margin_accounts = set()
    try:
        cur2 = conn.execute("SELECT account FROM margin_config")
        margin_accounts = set(r["account"] for r in cur2.fetchall())
    except sqlite3.OperationalError:
        pass

    for code, kind in cuentas:
        if code in margin_accounts:
            bp_o = buying_power_margin(conn, holdings, code, anchor_currency, mode="overnight")
            bp_i = buying_power_margin(conn, holdings, code, anchor_currency, mode="intraday")
            out.append({
                "account": code,
                "type": "MARGIN",
                "overnight": bp_o,
                "intraday": bp_i,
            })
        else:
            if kind != "CASH_BROKER":
                continue  # bancos no tienen poder de compra apalancado
            bp = buying_power_byma(conn, holdings, code, anchor_currency)
            # Solo incluir si hay equity > 0
            if bp.cash_total + bp.holdings_mv > 1e-6:
                out.append({
                    "account": code,
                    "type": "BYMA",
                    "result": bp,
                })

    return out
