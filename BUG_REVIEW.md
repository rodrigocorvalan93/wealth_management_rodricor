# Bug review contable — 2026-05-04

## TL;DR

Auditamos en profundidad `holdings.py`, `pnl.py`, `buying_power.py`,
`snapshots.py`, `fx.py`, `importer.py` y `schema.py`.

**El motor está bien**. La contabilidad de doble-entrada se sostiene en
todos los flujos críticos. Los pasivos restan correctamente del PN. El
FIFO de PnL realizado funciona. Las conversiones FX cross-currency están
bien implementadas.

Encontramos **1 bug menor real** (FX silent fail) y **2 bugs de UX en el
flujo de seed_demo** que ya corregimos. Los 4 supuestos bugs "críticos"
que un análisis automático sugirió resultaron ser **falsos positivos**
después de verificarlos contra el código real.

---

## Lo verificado contra código real

### ❌ Falsos positivos descartados

**1. holdings.py:178 — "div por cero en avg_cost si SHORT antes de BUY"**
- Real: línea 178 ya tiene guard `if qty_compra_acum > 0 else None`.
  Si nunca hubo BUY, `avg_cost = None` y el código continúa con
  `market_price` como fallback (líneas 338-339).
- Status: ✅ Código robusto.

**2. buying_power.py:286-292 — "equity cuenta margin cuenta pasivos mal"**
- Real: `mv_anchor` es `qty * market_price` con qty SIGNADA. Para una
  cuenta que toma margin (cash negativo), qty<0 → mv_anchor<0 → ya está
  neteado. La función `buying_power_margin` filtra por una cuenta
  específica; las cauciones BYMA viven en `caucion_pasivo_*` (otra
  cuenta), no se cuentan.
- El agente confundió `mv_anchor` (signed por qty) con `mv_pn_anchor`
  (que además flippa por liability).
- Status: ✅ Cálculo correcto.

**3. importer.py:585-591 — "comisión suma 2x"**
- Real: el patrón es `cuenta_cash -comision` + `external_expense +comision`.
  Suma per-asset = 0. Es double-entry estándar.
- Mismo patrón en gastos:738-744 (-monto/+monto), ingresos:674
  (+monto/-monto). Convención consistente.
- Status: ✅ Código correcto.

**4. importer.py:792-872 — "recurrentes sin idempotencia → duplicados"**
- Real: `import_all` (línea 988) llama `init_db(drop_existing=True)`.
  La DB se DROP+RECREATE en cada import. Es imposible duplicar.
- El agente no leyó `import_all`.
- Status: ✅ Idempotente por construcción.

### ✅ Bugs reales encontrados (y arreglados)

**1. `engine/holdings.py:334` — FX silent fail con precio en moneda extranjera**
- **Síntoma**: si el `market_price` viene de un loader en una moneda
  distinta a la nativa del activo (ej fetch dio precio en USD pero el
  bono cotiza en USB) y el `fx_convert` falla por falta de rate,
  silenciosamente se usa el precio EN LA MONEDA EQUIVOCADA. El holding
  queda valuado en USD pero etiquetado como USB.
- **Frecuencia**: rara — la mayoría de loaders devuelven precios en la
  moneda nativa. Triggerea solo si hay un loader nuevo o un override.
- **Severidad**: BAJA en uso típico, MEDIA en uso intensivo de loaders
  multi-currency.
- **Fix aplicado** (commit nuevo):
  ```python
  except FxError:
      price_fallback = True
      price_source = (price_source or "?") + f" (sin FX {mp['currency']}→{native_ccy})"
  ```
  Ahora el holding queda marcado con `px*` en la PWA y el `price_source`
  cuenta la historia para auditoría.

**2. `seed_demo.py` — column mismatch en gastos/ingresos/especies**
- **Síntoma**: el seed escribía las tuplas en orden equivocado, los
  campos caían en columnas erradas, el importer skipea la fila.
- **Severidad**: solo afecta al demo, no a usuarios reales.
- **Fix aplicado**: tuples ahora coinciden con el schema de
  build_master.

**3. `seed_demo.py` — Recurrente?=YES dropea silenciosamente**
- **Síntoma del importer (no fix)**: en `gastos`, si `Recurrente?=YES`
  el row se skipea (línea 701-702). La idea es que las recurrencias
  vienen de la sheet `recurrentes`. Pero un user puede poner YES sin
  agregar la fila a `recurrentes` y el gasto desaparece silenciosamente.
- **Fix aplicado** (en seed_demo): cambiamos a NO. **El bug del importer
  sigue ahí** — un mensaje de WARN sería deseable, pero está fuera de
  scope. Lo documentamos acá.

---

## Cosas auditadas y confirmadas correctas

### `engine/holdings.py`

- ✅ WAC NO contamina con SELLs (línea 171 verifica `qty > 0`).
- ✅ Pasivos restan via `mv_pn_anchor` con `is_liability` flag.
- ✅ Tolerance `1e-9` para qty cero — razonable para todos los assets
  que tracker (incluye satoshis).
- ✅ Cost-basis fallback a market_price en posiciones sin BUYs (cubre
  edge case de OPENING_BALANCE).

### `engine/pnl.py`

- ✅ FIFO matching simple y correcto. `lot.qty < 1e-9` triggers popleft
  para limpiar lotes residuales.
- ✅ PnL = (sell_price - lot_price) * matched_qty. Sin doble conteo.
- ✅ Cross-currency PnL etiqueta `currency` con la moneda de la venta.

### `engine/buying_power.py`

- ✅ Aforos BYMA aplicados multiplicativamente sin signos invertidos.
- ✅ Margin equity = SUM(mv_anchor) por cuenta — correcto cuando cash
  negativo (margin loan) ya viene neteado.

### `engine/snapshots.py`

- ✅ Snapshots idempotentes via PRIMARY KEY (fecha, account, anchor,
  investible_only).
- ✅ Equity curve filtra por `investible` cuando se pide.
- ✅ Returns: drawdown, Sharpe, Sortino bien implementados.

### `engine/fx.py`

- ✅ Cross-rate via ARS: `amount * rate_from_ARS / rate_to_ARS`.
  Algoritmo correcto.
- ✅ Stablecoins redirigidas via `quote_vs` correctamente.

### `engine/importer.py`

- ✅ TRADE: leg activo + leg cash, ambos con sign correcto.
- ✅ TRANSFER_CASH: origen -monto / destino +monto. Balanceado.
- ✅ INCOME: cuenta +monto / external_income -monto.
- ✅ EXPENSE cash: cuenta -monto / external_expense +monto.
- ✅ CARD_CHARGE: pasivo +monto / external_expense -monto. (Atención:
  signo del expense es OPUESTO al cash; ver "convención" abajo).
- ✅ FUNDING_OPEN/CLOSE: cash + pasivo crecen/decrecen sincronizados.
- ✅ Asientos contables: validación de balance per-asset por evento.
- ✅ FX en blotter falla con `continue` + log a stderr (línea 533) —
  acceptable, aunque podría retornar stat de skipped rows.

### `engine/schema.py`

- ✅ DDL robusta: FKs, CHECKs, índices.
- ✅ Convención documentada (línea 217-220).
- ✅ Settings table simple y funcional (agregada en commit reciente).

---

## Convenciones internas que conviene saber

### Per-event balance per asset

Para eventos **sin liabilities ni cross-asset** (TRANSFER_CASH, INCOME,
EXPENSE cash, asientos contables): suma de `qty` por asset = 0.

Para eventos **cross-asset** (TRADE, CARD_CHARGE) o **con liabilities**
(FUNDING_OPEN, LIABILITY_PAYMENT): la suma per-asset NO es 0. La
contabilidad se mantiene a nivel PN total via:
- `is_liability` flag → flip de signo en `mv_pn_anchor`
- LIABILITY accounts tienen qty positivo cuando hay deuda activa

### `external_expense` no es un termómetro de gastos

Como CARD_CHARGE pone `external_expense -monto` (porque la cuenta de
gastos "pierde" hacia el pasivo), la suma directa de `external_expense`
NO da el total de gastos. Para reportes de "cuánto gasté este mes"
preferir contar via `events WHERE event_type IN ('EXPENSE','CARD_CHARGE',
'CARD_INSTALLMENT')` y sumar `monto` positivo desde el source data.

---

## Recomendaciones futuras (NO aplicadas)

Estos NO son bugs activos, pero serían mejoras razonables:

1. **Stats de skipped rows**: cuando `import_blotter` skipea por FX
   error, devolver el conteo en stats para que la PWA pueda mostrar un
   warning "5 trades no se importaron por FX faltante".
2. **Warn cuando `Recurrente?=YES` y no hay fila en `recurrentes`**.
3. **CHECK en movements**: `abs(cost_basis - qty * unit_price) < 0.01`
   si `unit_price` no es null. Catch errores de input.
4. **Validación de FX rate negativo en importador** (defensivo, raro).
