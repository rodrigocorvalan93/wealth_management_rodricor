# Arquitectura del motor

Modelo de datos y flujo interno del motor `engine/`.

## Modelo conceptual: doble entrada

El motor es un **ledger contable** con doble entrada. Cada operación es un
`event` con N `movements` que deben balancear.

```
event (un trade, un gasto, una transferencia)
  ├─ movement A: cuenta X gana activo (qty positivo)
  ├─ movement B: cuenta Y pierde cash (qty negativo)
  └─ ...
```

**Convención de signos**:
- `qty > 0`: la cuenta GANA el activo
- `qty < 0`: la cuenta PIERDE el activo

**Balance check**: para cada activo, `SUM(qty)` sobre todas las cuentas debe ser 0
(salvo INCOME/EXPENSE donde la contraparte es `external_income`/`external_expense`).

## Tablas principales

### Maestros

```sql
currencies     (code, name, is_stable, quote_vs, is_base)
accounts       (code, name, kind, currency, card_close_day, card_due_day,
                investible, cash_purpose, ...)
assets         (ticker, name, asset_class, currency, issuer, ...)
aforos         (scope_type, scope_value, aforo_pct, source)        -- BYMA
margin_config  (account, mult_overnight, mult_intraday, funding_rate_annual)
```

### Ledger

```sql
events     (event_id, event_type, event_date, description, ...)
movements  (movement_id, event_id, account, asset, qty,
            unit_price, price_currency, cost_basis, ...)
```

### Series temporales

```sql
fx_rates      (fecha, moneda, rate, base, source)
prices        (fecha, ticker, price, currency, source)
pn_snapshots  (fecha, account, anchor_currency, mv_anchor, investible_only)
```

`pn_snapshots`: foto del PN por cuenta + total + total invertible. Append-only,
una entrada por cada corrida del reporte. Genera la equity curve.

## Tipos de eventos

| Tipo | Para qué |
|---|---|
| `TRADE` | BUY/SELL de activo (genera 2 movements: activo + cash) |
| `TRANSFER_ASSET` | Activo entre cuentas propias |
| `TRANSFER_CASH` | Cash entre cuentas propias |
| `INCOME` | Sueldo, dividendo, cupón, premio |
| `EXPENSE` | Gasto cash |
| `CARD_CHARGE` | Gasto con tarjeta (incrementa pasivo) |
| `CARD_INSTALLMENT` | Cuota mensual de un plan multi-mes |
| `CARD_PAYMENT` | Pago/cancelación de tarjeta |
| `LIABILITY_OPEN` | Apertura de préstamo |
| `LIABILITY_PAYMENT` | Cuota de préstamo (capital + interés) |
| `FUNDING_OPEN` | Caución/pase tomada |
| `FUNDING_CLOSE` | Cierre con intereses |
| `OPENING_BALANCE` | Saldo inicial |
| `ACCOUNTING_ADJUSTMENT` | Ajuste manual |

## Tipos de cuentas

| Kind | Para qué |
|---|---|
| `CASH_BANK` | Banco con cuenta corriente/caja ahorro |
| `CASH_BROKER` | ALyC con saldo cash (Cocos, Eco, Delta) |
| `CASH_WALLET` | Wallet cripto (Binance, MetaMask) |
| `CASH_PHYSICAL` | Cash físico (transaccional, reserva) |
| `CARD_CREDIT` | Tarjeta de crédito (es un pasivo) |
| `LIABILITY` | Otros pasivos (préstamos, hipoteca) |
| `EXTERNAL` | Contraparte externa (sueldo, etc) |
| `OPENING_BALANCE` | Cuenta especial de apertura |
| `INTEREST_EXPENSE` | Cuenta de resultados |
| `INTEREST_INCOME` | Cuenta de resultados |

## Flujo de import

```
Excel master (.xlsx)
        │
        ↓
engine/importer.py
        │
        ├─ import_monedas()       → currencies
        ├─ import_cuentas()       → accounts
        ├─ import_especies()      → assets
        ├─ import_fx_csv()        → fx_rates  (precarga FX antes de blotter)
        │
        ├─ import_blotter()       → events + movements (TRADE)
        │     · convierte unit_price a moneda nativa del asset si difiere
        │     · cash se mueve en moneda real del trade
        │
        ├─ import_transferencias_cash()    → events (TRANSFER_CASH)
        ├─ import_transferencias_activos() → events (TRANSFER_ASSET)
        ├─ import_ingresos()      → events (INCOME)
        ├─ import_gastos()        → events (EXPENSE / CARD_CHARGE / CARD_INSTALLMENT)
        │     · expande automáticamente N cuotas en N events
        ├─ import_recurrentes()   → expande regla mensual en N events INCOME/EXPENSE
        ├─ import_pagos_pasivos() → events (CARD_PAYMENT / LIABILITY_PAYMENT)
        └─ import_asientos()      → events (ACCOUNTING_ADJUSTMENT)
```

**Idempotencia**: el importer borra y recrea la DB en cada corrida.
Toda la lógica está en el Excel; la DB es derivada.

## Conversión FX

`engine/fx.py` resuelve cross-rates con cascada:

1. **Búsqueda exacta**: `(fecha, moneda, base)` en `fx_rates`
2. **Fallback hacia atrás**: hasta N días (default 7)
3. **Stablecoin paridad implícita**: si la moneda es `is_stable=1` y tiene
   `quote_vs`, redirige al rate de `quote_vs` (ej USDC → USD CCL)

Ejemplo: USDC sin FX cargado → motor lee `currencies.is_stable=1, quote_vs=USD`
→ devuelve `get_rate(USD)` automáticamente.

## Holdings calculator

`engine/holdings.py` agrupa movements por (cuenta, asset) y calcula:

- `qty`: SUM(qty) de movements
- `avg_cost`: cost basis weighted average de las compras
- `cost_basis_total`: qty × avg_cost
- `market_price`: último precio en `prices` (con fallback a cost basis)
- `mv_native`: qty × market_price
- `mv_anchor`: convertido a moneda ancla (USD CCL por default)
- `unrealized_pnl_native`: mv_native - cost_basis_total
- `unrealized_pct`: unrealized / cost_basis

**Tolerancia a falta de precios**: si no hay precio de mercado, usa cost basis
con flag `price_fallback=True` (visible en reportes con marca visual).

## PnL FIFO

`engine/pnl.py` matchea trades con cola FIFO por (cuenta, asset):

```python
for trade in trades_ordenados_por_fecha:
    if trade.qty > 0:  # compra
        lots.append(Lot(qty, price, fecha))
    elif trade.qty < 0:  # venta
        while qty_to_sell > 0 and lots:
            lot = lots[0]
            qty_match = min(qty_to_sell, lot.qty)
            pnl = (precio_venta - lot.precio) * qty_match
            yield Fill(...)
            lot.qty -= qty_match
            qty_to_sell -= qty_match
            if lot.qty <= 0:
                lots.popleft()
```

Genera lista de `Fill` (matches) con G/P por trade. Después agrega por:
- asset, account, year, currency
- year × currency (para tax reporting)

## Tarjetas

`engine/liabilities.py` calcula 3 vistas para cada tarjeta:

1. **Saldo actual**: SUM(movements) hasta hoy
2. **Último resumen cerrado**: SUM(movements) hasta el último cierre
3. **Próximo vencimiento**: SUM entre último cierre y próximo cierre

Calculado a partir de `card_close_day` (día del mes que cierra) y
`card_due_day` (día del mes que vence, asume mes siguiente al cierre).

## Vistas SQL útiles

```sql
-- Saldos actuales por (cuenta, asset)
SELECT * FROM v_balances;

-- Movimientos enriquecidos con event_date y event_type
SELECT * FROM v_movements_full;

-- Solo movimientos de tarjetas
SELECT * FROM v_card_ledger;
```

## Módulos extra

- `engine/trade_stats.py` — métricas de trading (winrate, profit factor,
  expectancy, drawdowns por moneda) sobre los fills de PnL realizado.
- `engine/snapshots.py` — record/query de snapshots históricos para construir
  la equity curve por cuenta y total.
- `engine/buying_power.py` — poder de compra:
  - **BYMA / Cocos / Eco**: aforo por instrumento × MV → garantía.
    Tabla `aforos`, override por ticker > class > defaults hardcodeados.
  - **IBKR / margin**: multiplier × equity (RegT estándar x2 ON / x4 ID).
    Tabla `margin_config`, parámetros configurables por cuenta.
    Verificá los valores reales con tu broker.

## Filtros invertible (Sprint B)

Las cuentas tienen flag `investible` (0/1) y `cash_purpose` (texto libre).
- Cuentas técnicas (`external_*`, `opening_balance`, `interest_*`) se fuerzan
  a `investible=0`.
- El usuario puede marcar `cash_reserva` como no-invertible si tiene cash
  pendiente de blanqueo.
- `total_pn()` expone `total_anchor`, `total_investible` y `total_non_investible`.
- Reportes muestran 3 KPIs y un panel de "Cash por propósito".

## Decisiones de diseño

### ¿Por qué doble entrada?

Garantiza consistencia contable: para cada activo, el balance global suma 0.
Si rompo algo, queda evidente en `v_balances` (sumatoria != 0).

### ¿Por qué SQLite y no Postgres?

- Setup zero (un archivo)
- Suficiente para 1 usuario con miles de eventos
- Idempotente: borrar la DB y recrear es trivial
- Backups = copiar el archivo

### ¿Por qué Excel master?

- UX familiar para cargar 20 trades/día sin formularios
- Validaciones con DataValidation (dropdowns)
- Vos ves todo de una con autofilter
- Si necesitás cambiar 50 filas, hacés copy-paste

### ¿Por qué motor separado del Excel?

- Excel = inputs, motor = derivación
- El motor se puede portar a otro lenguaje sin tocar tu workflow
- Tests offline reproducibles
- Reportes generados en cualquier momento sin abrir Excel
