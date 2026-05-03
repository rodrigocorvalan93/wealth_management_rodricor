# CLI — Comandos del motor

Cuatro comandos CLI para el flujo del día a día.

## cli.cargar_iniciales — Setup inicial de saldos

Lee la hoja temporal `_carga_inicial` del Excel master y genera filas de
doble entrada en `asientos_contables`. Idempotente: re-correr regenera limpio.

```bash
python -m cli.cargar_iniciales --fecha 2026-04-30
python -m cli.cargar_iniciales --dry-run    # solo muestra qué generaría
```

**Cómo usar**:

1. Editá la hoja `_carga_inicial` en el Excel master con tus saldos:
   - Una fila por (cuenta, activo)
   - Para activos: completá `Unit Price` + `Price Currency`
   - Para cash: dejá `Unit Price` vacío, `Activo` = código de moneda (ARS, USB)
   - `Qty` siempre POSITIVO (el motor invierte el signo en `opening_balance`)
2. Cerrá Excel
3. Corré el comando

**FX automático**: si `Price Currency` ≠ moneda nativa del activo (definida en
hoja `especies`), el motor convierte el unit_price usando FX del día (con
fallback de 14 días). Marca cada conversión en `Notes` para auditoría.

## cli.summary — Portfolio summary en consola

Imprime una vista del portfolio en la terminal:

```bash
python -m cli.summary --fecha 2026-05-03 --anchor USD
```

**Output**:
- PN total en moneda ancla (USD CCL por default)
- Por asset class con %
- Por moneda nativa con %
- Top 10 cuentas
- Top 10 posiciones
- Posiciones con precio fallback (cost basis)
- Posiciones sin FX disponible (no convertibles)

**Flags**:
- `--fecha YYYY-MM-DD` — fecha de corte (default: hoy)
- `--anchor USD|USB|ARS` — moneda ancla del reporte (default: USD)

## cli.tarjetas — Resumen de tarjetas

Las 3 vistas de cada tarjeta de crédito:

```bash
python -m cli.tarjetas
```

Muestra para cada tarjeta:
1. **Saldo actual**: todo lo cargado, no pagado
2. **Último resumen cerrado**: del último ciclo cerrado
3. **Próximo vencimiento**: lo que se va a debitar en el próximo cierre

Calculado a partir de `card_close_day` y `card_due_day` configurados en hoja
`cuentas`.

## cli.report — Reportes Excel + HTML

Genera reporte completo del portfolio:

```bash
# Excel multi-sheet (8 hojas)
python -m cli.report --xlsx

# HTML autocontenido con gráficos
python -m cli.report --html

# Ambos
python -m cli.report --xlsx --html

# Fecha histórica
python -m cli.report --xlsx --fecha 2026-04-30

# Cambiar moneda ancla
python -m cli.report --xlsx --anchor USB

# No re-importar el Excel (usa la DB tal como está)
python -m cli.report --xlsx --no-import
```

**Output**: `reports/{fecha}_portfolio.xlsx` y `reports/{fecha}_portfolio.html`.

### Excel — 8 hojas

1. **Dashboard** — KPI PN total + tablas chicas de asset class, moneda, cuentas
2. **Holdings** — todas las posiciones con qty, avg cost, mkt price, MV, PnL
3. **PN por cuenta** — agregado por broker/banco/wallet
4. **PN por asset class** — bonos / equity / FCI / cripto / cash
5. **Cash Position** — solo cash + subtotales por moneda
6. **Tarjetas** — saldo actual, último resumen, próximo vencimiento
7. **PnL Realizado FIFO** — todos los trades cerrados con G/P + total por moneda
8. **PnL No-Realizado** — todas las posiciones abiertas con MV vs cost basis

### HTML — Dashboard interactivo

- KPI grande con PN total
- 2 gráficos donut (chart.js): Asset Class y Moneda
- Top 15 posiciones
- PN por cuenta
- PnL realizado por año + moneda

**Nota**: el HTML usa chart.js desde CDN (jsdelivr). Si lo abrís sin
internet, se ven las tablas pero no los gráficos.

## Flujo diario recomendado

```bash
# 1. Después del cierre BYMA, bajar precios (cron-able)
python fx_loader.py
python byma_loader.py --tickers-file tickers_byma.txt
python cafci_loader.py
python cripto_loader.py
python yfinance_loader.py

# 2. Cargar trades del día en Excel master, hoja `blotter`
# 3. Cerrar Excel

# 4. Generar reportes
python -m cli.report --xlsx --html

# 5. Abrir reports/2026-05-03_portfolio.xlsx
```
