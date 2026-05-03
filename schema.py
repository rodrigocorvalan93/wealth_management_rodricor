# -*- coding: utf-8 -*-
"""
engine/schema.py

Schema SQLite del motor wealth_management.

Modelo: contabilidad de doble entrada con `events` y `movements`.
Cada evento (un trade, un gasto, una transferencia) se compone de
≥2 movements que deben balancearse contra el modelo correcto.

Tablas:
  Maestros (configuración):
    currencies        — monedas (ARS, USD, USB, USDT, BTC, ETH...)
    accounts          — cuentas (Cocos, Eco, Galicia, Galicia Visa ARS...)
    assets            — instrumentos (AL30D, GD30C, BTC, ...)

  Eventos y movimientos:
    events            — un evento atómico (TRADE, EXPENSE, TRANSFER, etc)
    movements         — patas de cada evento (debit/credit por activo)

  Series temporales:
    fx_rates          — cotizaciones cargadas (origen, destino, fecha, rate)
    prices            — precios de activos por fecha

  Recurrencia y cuotas:
    recurring_rules   — reglas de auto-repetición (ej alquiler mensual)
    installment_plans — cuotas (compra en N cuotas con tarjeta)

USO:
    from engine.schema import init_db, EventType, MovementSign
    conn = init_db("data/wealth.db")
"""

from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path
from typing import Optional


# =============================================================================
# Enumeraciones (como constantes string para sqlite)
# =============================================================================

class EventType:
    """Tipos de eventos en el ledger."""
    TRADE = "TRADE"                     # BUY/SELL de activo
    TRANSFER_ASSET = "TRANSFER_ASSET"   # Movimiento de activo entre cuentas
    TRANSFER_CASH = "TRANSFER_CASH"     # Movimiento de cash entre cuentas
    INCOME = "INCOME"                   # Sueldo, dividendo, cupón, premio
    EXPENSE = "EXPENSE"                 # Gasto (cash o tarjeta)
    OPENING_BALANCE = "OPENING_BALANCE" # Saldo inicial de apertura
    FUNDING_OPEN = "FUNDING_OPEN"       # Apertura de caución/préstamo
    FUNDING_CLOSE = "FUNDING_CLOSE"     # Cierre con intereses
    LIABILITY_OPEN = "LIABILITY_OPEN"   # Apertura de pasivo (préstamo personal)
    LIABILITY_PAYMENT = "LIABILITY_PAYMENT"  # Pago de cuota (capital + interés)
    CARD_CHARGE = "CARD_CHARGE"         # Gasto con tarjeta (incrementa pasivo)
    CARD_PAYMENT = "CARD_PAYMENT"       # Cancelación de tarjeta
    CARD_INSTALLMENT = "CARD_INSTALLMENT"  # Cuota de un plan multi-mes
    ACCOUNTING_ADJUSTMENT = "ACCOUNTING_ADJUSTMENT"  # Asiento contable manual

    ALL = (TRADE, TRANSFER_ASSET, TRANSFER_CASH, INCOME, EXPENSE,
           OPENING_BALANCE, FUNDING_OPEN, FUNDING_CLOSE,
           LIABILITY_OPEN, LIABILITY_PAYMENT, CARD_CHARGE, CARD_PAYMENT,
           CARD_INSTALLMENT, ACCOUNTING_ADJUSTMENT)


class AccountKind:
    """Tipos de cuenta. Define cómo se valoriza y reporta."""
    CASH_BANK = "CASH_BANK"             # Banco con cuenta corriente/caja ahorro
    CASH_BROKER = "CASH_BROKER"         # ALyC con saldo cash (Cocos, Eco, Delta)
    CASH_WALLET = "CASH_WALLET"         # Wallet cripto (Binance, MetaMask)
    CASH_PHYSICAL = "CASH_PHYSICAL"     # Cash físico ("transaccional", "reserva")
    CARD_CREDIT = "CARD_CREDIT"         # Tarjeta de crédito (es un pasivo)
    LIABILITY = "LIABILITY"             # Otros pasivos (préstamos, hipoteca)
    EXTERNAL = "EXTERNAL"               # Contraparte externa (sueldo, etc)
    OPENING_BALANCE = "OPENING_BALANCE" # Cuenta especial de apertura
    INTEREST_EXPENSE = "INTEREST_EXPENSE"  # Cuenta de resultados (intereses)
    INTEREST_INCOME = "INTEREST_INCOME" # Cuenta de resultados (intereses cobrados)

    ALL = (CASH_BANK, CASH_BROKER, CASH_WALLET, CASH_PHYSICAL,
           CARD_CREDIT, LIABILITY, EXTERNAL, OPENING_BALANCE,
           INTEREST_EXPENSE, INTEREST_INCOME)

    # Cuentas que efectivamente "tenés" (suman al PN como activo o pasivo)
    PN_RELEVANT = (CASH_BANK, CASH_BROKER, CASH_WALLET, CASH_PHYSICAL,
                   CARD_CREDIT, LIABILITY)


class AssetClass:
    """Clase de activo. Para reportes y agregaciones."""
    CASH = "CASH"                       # Efectivo / saldos en banco / wallet
    BOND_AR = "BOND_AR"                 # Bonos AR (AL30, GD30, TX26, BPC...)
    EQUITY_AR = "EQUITY_AR"             # Acciones AR (GGAL, BMA, YPFD...)
    EQUITY_US = "EQUITY_US"             # CEDEARs y ADRs
    FCI = "FCI"                         # Fondos comunes de inversión
    CRYPTO = "CRYPTO"                   # BTC, ETH, ALT
    STABLECOIN = "STABLECOIN"           # USDT, USDC, DAI
    DERIVATIVE = "DERIVATIVE"           # Futuros, opciones
    OTHER = "OTHER"

    ALL = (CASH, BOND_AR, EQUITY_AR, EQUITY_US, FCI, CRYPTO, STABLECOIN,
           DERIVATIVE, OTHER)


class CardCycle:
    """Tipos de ciclo de tarjeta."""
    MONTHLY = "MONTHLY"                 # Cierra día X de cada mes
    NONE = "NONE"                       # Sin ciclo (debit-like)


class LiabilityKind:
    """Tipos de préstamos para amortización."""
    BULLET = "BULLET"                   # Capital al final, intereses periódicos
    FRENCH = "FRENCH"                   # Cuota constante (decrece capital)
    GERMAN = "GERMAN"                   # Capital constante (decrece cuota)
    CAUCION = "CAUCION"                 # Bullet a corto plazo, sin intereses periódicos
    AMERICAN = "AMERICAN"               # Solo intereses, capital al final


# =============================================================================
# Schema DDL
# =============================================================================

SCHEMA_DDL = """
-- Versionado del schema
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =============================================================================
-- Maestros
-- =============================================================================

-- Monedas
CREATE TABLE IF NOT EXISTS currencies (
    code         TEXT PRIMARY KEY,           -- 'ARS', 'USD', 'USB', 'USDT', 'BTC'
    name         TEXT NOT NULL,
    is_stable    INTEGER NOT NULL DEFAULT 0, -- 1 si es stablecoin
    quote_vs     TEXT,                        -- moneda base de cotización ('ARS', 'USD', null si es base)
    is_base      INTEGER NOT NULL DEFAULT 0, -- 1 si es la unidad de cuenta de referencia
    notes        TEXT
);

-- Cuentas (brokers, bancos, wallets, tarjetas, contracuentas)
CREATE TABLE IF NOT EXISTS accounts (
    code         TEXT PRIMARY KEY,           -- 'cocos', 'galicia_visa_ars', 'binance'
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,              -- AccountKind
    institution  TEXT,                        -- 'Banco Galicia', 'Cocos Capital', ...
    currency     TEXT,                        -- moneda base de la cuenta (cash) o NULL
    -- Para CARD_CREDIT:
    card_cycle_kind TEXT DEFAULT 'NONE',     -- 'MONTHLY' | 'NONE'
    card_close_day  INTEGER,                  -- día del mes en que cierra (1-31)
    card_due_day    INTEGER,                  -- día del mes en que vence (1-31)
    card_currency   TEXT,                     -- moneda en la que cierra ('ARS', 'USD')
    notes        TEXT,
    FOREIGN KEY (currency) REFERENCES currencies(code),
    FOREIGN KEY (card_currency) REFERENCES currencies(code),
    CHECK (kind IN ('CASH_BANK','CASH_BROKER','CASH_WALLET','CASH_PHYSICAL',
                    'CARD_CREDIT','LIABILITY','EXTERNAL','OPENING_BALANCE',
                    'INTEREST_EXPENSE','INTEREST_INCOME'))
);

-- Activos (instrumentos: bonos, acciones, FCI, cripto, además de monedas como asset)
CREATE TABLE IF NOT EXISTS assets (
    ticker       TEXT PRIMARY KEY,           -- 'AL30D', 'GD30C', 'BTC', 'DELTA_AHORRO_A'
    name         TEXT NOT NULL,
    asset_class  TEXT NOT NULL,              -- AssetClass
    currency     TEXT NOT NULL,              -- moneda nativa de cotización del activo
    issuer       TEXT,
    sector       TEXT,
    country      TEXT,
    -- Para bonos:
    maturity     TEXT,                        -- ISO date
    -- Notes
    notes        TEXT,
    FOREIGN KEY (currency) REFERENCES currencies(code),
    CHECK (asset_class IN ('CASH','BOND_AR','EQUITY_AR','EQUITY_US','FCI',
                           'CRYPTO','STABLECOIN','DERIVATIVE','OTHER'))
);

-- =============================================================================
-- Eventos y movimientos (núcleo del ledger)
-- =============================================================================

-- Eventos: una entrada en el ledger
CREATE TABLE IF NOT EXISTS events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,              -- EventType
    event_date   TEXT NOT NULL,              -- ISO date
    settle_date  TEXT,                        -- ISO date (T+1, etc)
    description  TEXT,
    source_row   INTEGER,                     -- fila en el Excel input (para auditing)
    source_sheet TEXT,                        -- hoja origen
    external_id  TEXT,                        -- ID externo (ej Trade ID del broker)
    parent_event_id INTEGER,                  -- para cuotas: vincula al evento padre
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (parent_event_id) REFERENCES events(event_id),
    CHECK (event_type IN ('TRADE','TRANSFER_ASSET','TRANSFER_CASH','INCOME','EXPENSE',
                          'OPENING_BALANCE','FUNDING_OPEN','FUNDING_CLOSE',
                          'LIABILITY_OPEN','LIABILITY_PAYMENT',
                          'CARD_CHARGE','CARD_PAYMENT','CARD_INSTALLMENT',
                          'ACCOUNTING_ADJUSTMENT'))
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- Movements: las patas (debit/credit) de cada evento
-- Convención: qty positiva = la cuenta GANA el activo (DEBIT de activo)
--             qty negativa = la cuenta PIERDE el activo (CREDIT de activo)
-- Cada evento debe tener movements que sumen a 0 por activo (balance check),
-- excepto eventos como INCOME (donde la contraparte es la cuenta external_income).
CREATE TABLE IF NOT EXISTS movements (
    movement_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL,
    account      TEXT NOT NULL,              -- accounts.code
    asset        TEXT NOT NULL,              -- assets.ticker o currencies.code
    qty          REAL NOT NULL,              -- con signo (+ = entra a cuenta, - = sale)
    unit_price   REAL,                        -- precio en moneda del trade (NULL si es cash mov)
    price_currency TEXT,                      -- moneda del precio (NULL si es cash mov)
    cost_basis   REAL,                        -- qty × unit_price (con signo)
    notes        TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY (account) REFERENCES accounts(code),
    FOREIGN KEY (price_currency) REFERENCES currencies(code)
);

CREATE INDEX IF NOT EXISTS idx_movements_event ON movements(event_id);
CREATE INDEX IF NOT EXISTS idx_movements_account ON movements(account);
CREATE INDEX IF NOT EXISTS idx_movements_asset ON movements(asset);
CREATE INDEX IF NOT EXISTS idx_movements_acc_asset ON movements(account, asset);

-- Vista: movimientos enriquecidos con event_date y event_type
CREATE VIEW IF NOT EXISTS v_movements_full AS
SELECT
    m.movement_id,
    m.event_id,
    e.event_type,
    e.event_date,
    e.settle_date,
    m.account,
    m.asset,
    m.qty,
    m.unit_price,
    m.price_currency,
    m.cost_basis,
    e.description AS event_description,
    m.notes AS movement_notes
FROM movements m
JOIN events e ON e.event_id = m.event_id;

-- =============================================================================
-- Series temporales
-- =============================================================================

CREATE TABLE IF NOT EXISTS fx_rates (
    fecha        TEXT NOT NULL,              -- ISO date
    moneda       TEXT NOT NULL,              -- moneda cotizada
    rate         REAL NOT NULL,              -- valor en moneda base
    base         TEXT NOT NULL DEFAULT 'ARS',-- moneda base de la cotización
    source       TEXT,                        -- 'dolarapi mid', 'manual', 'argentinadatos mid'
    PRIMARY KEY (fecha, moneda, base)
);

CREATE INDEX IF NOT EXISTS idx_fx_moneda_fecha ON fx_rates(moneda, fecha);

CREATE TABLE IF NOT EXISTS prices (
    fecha        TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    price        REAL NOT NULL,
    currency     TEXT NOT NULL,
    source       TEXT,
    PRIMARY KEY (fecha, ticker)
);

CREATE INDEX IF NOT EXISTS idx_prices_ticker_fecha ON prices(ticker, fecha);

-- =============================================================================
-- Recurrencia y cuotas
-- =============================================================================

CREATE TABLE IF NOT EXISTS recurring_rules (
    rule_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name    TEXT NOT NULL,              -- 'Alquiler mensual'
    event_type   TEXT NOT NULL,              -- 'INCOME', 'EXPENSE', 'CARD_CHARGE'
    -- Datos del template
    account      TEXT NOT NULL,
    counterparty TEXT,                        -- cuenta contraparte
    asset        TEXT NOT NULL,              -- moneda en que se carga
    amount       REAL NOT NULL,              -- monto base (puede actualizarse)
    description  TEXT NOT NULL,
    category     TEXT,                        -- 'Vivienda', 'Servicios'
    is_fixed     INTEGER DEFAULT 1,          -- gasto fijo vs variable
    -- Recurrencia
    start_date   TEXT NOT NULL,
    end_date     TEXT,                        -- NULL = indefinido
    frequency    TEXT NOT NULL DEFAULT 'MONTHLY', -- 'MONTHLY', 'WEEKLY', 'YEARLY'
    day_of_month INTEGER,                     -- ej 5 = día 5 del mes
    -- Estado
    active       INTEGER NOT NULL DEFAULT 1,
    notes        TEXT,
    FOREIGN KEY (account) REFERENCES accounts(code),
    FOREIGN KEY (asset) REFERENCES currencies(code)
);

-- Planes de cuotas (compras en N cuotas con tarjeta)
CREATE TABLE IF NOT EXISTS installment_plans (
    plan_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_event INTEGER,                   -- event_id de la compra original
    description    TEXT NOT NULL,
    card_account   TEXT NOT NULL,             -- accounts.code de la tarjeta
    asset          TEXT NOT NULL,             -- moneda
    total_amount   REAL NOT NULL,
    n_installments INTEGER NOT NULL,
    first_close_date TEXT NOT NULL,           -- fecha del primer cierre que la incluye
    cft            REAL DEFAULT 0,            -- costo financiero total (anual decimal)
    status         TEXT NOT NULL DEFAULT 'ACTIVE',  -- 'ACTIVE', 'CLOSED', 'CANCELLED'
    notes          TEXT,
    FOREIGN KEY (purchase_event) REFERENCES events(event_id),
    FOREIGN KEY (card_account) REFERENCES accounts(code),
    FOREIGN KEY (asset) REFERENCES currencies(code)
);

-- =============================================================================
-- Vistas de conveniencia
-- =============================================================================

-- Saldo actual de cualquier cuenta para cualquier asset
CREATE VIEW IF NOT EXISTS v_balances AS
SELECT
    account,
    asset,
    SUM(qty) AS balance
FROM movements
GROUP BY account, asset
HAVING ABS(SUM(qty)) > 1e-6;

-- Ledger de tarjeta: solo eventos que afectan tarjetas
CREATE VIEW IF NOT EXISTS v_card_ledger AS
SELECT
    m.account AS card,
    e.event_date,
    e.event_type,
    m.asset,
    m.qty,
    e.description,
    e.event_id
FROM movements m
JOIN events e ON e.event_id = m.event_id
JOIN accounts a ON a.code = m.account
WHERE a.kind = 'CARD_CREDIT'
ORDER BY m.account, e.event_date;
"""

CURRENT_VERSION = 1


def init_db(db_path: str | Path, drop_existing: bool = False) -> sqlite3.Connection:
    """Inicializa la DB. Si drop_existing=True, borra y recrea.
    Devuelve conexión con foreign_keys ON."""
    db_path = Path(db_path)
    if drop_existing and db_path.is_file():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(SCHEMA_DDL)

    # Versionado
    cur = conn.execute("SELECT MAX(version) FROM schema_version")
    current = cur.fetchone()[0]
    if current is None or current < CURRENT_VERSION:
        conn.execute(
            "INSERT INTO schema_version(version) VALUES (?)",
            (CURRENT_VERSION,),
        )

    conn.commit()
    return conn


# =============================================================================
# Helpers básicos
# =============================================================================

def insert_currency(conn, code, name, is_stable=False, quote_vs=None,
                    is_base=False, notes=None):
    conn.execute(
        """INSERT OR REPLACE INTO currencies
           (code, name, is_stable, quote_vs, is_base, notes)
           VALUES (?,?,?,?,?,?)""",
        (code, name, int(is_stable), quote_vs, int(is_base), notes),
    )


def insert_account(conn, code, name, kind, institution=None, currency=None,
                   card_cycle_kind="NONE", card_close_day=None,
                   card_due_day=None, card_currency=None, notes=None):
    conn.execute(
        """INSERT OR REPLACE INTO accounts
           (code, name, kind, institution, currency, card_cycle_kind,
            card_close_day, card_due_day, card_currency, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (code, name, kind, institution, currency, card_cycle_kind,
         card_close_day, card_due_day, card_currency, notes),
    )


def insert_asset(conn, ticker, name, asset_class, currency,
                 issuer=None, sector=None, country=None,
                 maturity=None, notes=None):
    conn.execute(
        """INSERT OR REPLACE INTO assets
           (ticker, name, asset_class, currency, issuer, sector, country,
            maturity, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (ticker, name, asset_class, currency, issuer, sector, country,
         maturity, notes),
    )


def insert_event(conn, event_type, event_date, settle_date=None,
                 description=None, source_row=None, source_sheet=None,
                 external_id=None, parent_event_id=None, notes=None) -> int:
    """Inserta un evento. Devuelve el event_id generado."""
    cur = conn.execute(
        """INSERT INTO events
           (event_type, event_date, settle_date, description,
            source_row, source_sheet, external_id, parent_event_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (event_type, event_date, settle_date, description,
         source_row, source_sheet, external_id, parent_event_id, notes),
    )
    return cur.lastrowid


def insert_movement(conn, event_id, account, asset, qty,
                    unit_price=None, price_currency=None, cost_basis=None,
                    notes=None):
    conn.execute(
        """INSERT INTO movements
           (event_id, account, asset, qty, unit_price, price_currency,
            cost_basis, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (event_id, account, asset, qty, unit_price, price_currency,
         cost_basis, notes),
    )
