# Excel master — 16 hojas

El archivo `inputs/wealth_management_*.xlsx` es la única fuente de verdad
para inputs. Todo lo cargás ahí; el motor lo lee y popula la DB.

## Generar el master

```bash
python build_master.py inputs/wealth_management.xlsx
python add_carga_inicial_sheet.py inputs/wealth_management.xlsx
```

## Convención de colores

- **Celdas amarillas + texto azul**: vos cargás
- **Celdas blancas con texto negro**: lectura/automático
- **Headers azul navy con texto blanco**: no editar

## Las 16 hojas

### Maestros (cargás 1 vez al inicio)

| # | Hoja | Para qué |
|---|---|---|
| 1 | `INDEX` | Índice general del archivo |
| 2 | `config` | Parámetros (moneda base, fecha arranque, método PnL) |
| 3 | `monedas` | ARS, USD, USB, USDT, BTC, ETH, etc + cross-rates |
| 4 | `cuentas` | Brokers, bancos, wallets, tarjetas |
| 5 | `especies` | Master de instrumentos |

### Operaciones (cargás cada operación)

| # | Hoja | Cuándo cargar |
|---|---|---|
| 6 | `blotter` | Trades de activos (BUY/SELL) |
| 7 | `transferencias_cash` | Cash entre cuentas propias |
| 8 | `transferencias_activos` | Activos entre cuentas propias |
| 9 | `funding` | Cauciones, pases, préstamos cortos |
| 10 | `ingresos` | Sueldos, dividendos, cupones (no recurrentes) |
| 11 | `gastos` | Gastos cash o tarjeta (con cuotas) |
| 12 | `pasivos` | Préstamos personales, hipoteca |
| 13 | `pagos_pasivos` | Pagos de cuotas y cancelación de tarjetas |
| 14 | `recurrentes` | Sueldo/alquiler/servicios — auto-repetición |
| 15 | `asientos_contables` | Saldos iniciales + ajustes manuales |

### Hoja temporal

| # | Hoja | Para qué |
|---|---|---|
| 16 | `_carga_inicial` | Carga simplificada de saldos iniciales (vos cargás 1 fila por activo, motor genera doble entrada) |

## Flujo recomendado de carga

### Setup inicial (1 vez)

1. **`monedas`** — ya viene pre-cargada con ARS, USD, USB, USDT, USDC, BTC, ETH, EUR, BRL, UYU, PEN. Agregá las que faltan.
2. **`cuentas`** — todas tus cuentas de broker/banco/wallet/tarjetas.
3. **`especies`** — todos los tickers que tenés/operás.
4. **`_carga_inicial`** — saldos iniciales al día de arranque.
5. Correr:
   ```bash
   python -m cli.cargar_iniciales --fecha 2026-04-30
   ```
   El motor genera filas en `asientos_contables` con doble entrada automática.

### Operatoria diaria

- **Trades**: cargás en `blotter`
- **Transferencias entre tus cuentas**: `transferencias_cash` o `transferencias_activos`
- **Sueldos/dividendos/cupones**: `ingresos` (si NO es recurrente, sino ya está en `recurrentes`)
- **Gastos**: `gastos` — indicá si es FIJO/VARIABLE, cantidad de cuotas
- **Pagos de tarjeta o cuotas de préstamo**: `pagos_pasivos`

### Recurrentes (1 vez por concepto)

Cargá en `recurrentes` el sueldo, alquiler, suscripciones fijas, etc.
El motor los expande automáticamente al importar.

## Convenciones específicas

### Columna `Strategy` (en blotter y _carga_inicial)

Etiqueta de estrategia para filtrar/agrupar después:

- `BH` — Buy & Hold (carry, holdeo de bonos)
- `TRADING` — operativa de corto plazo
- `CORE` — posición core del portfolio
- `FCI` — fondos comunes
- `CRYPTO` — cripto
- `CASH` — saldos de efectivo
- `DEBT` — pasivos

### Tickers BYMA con sufijo

Convención BYMA: bonos en moneda extranjera tienen sufijo:
- Sin sufijo → ARS (AL30, GD30, TX26)
- Sufijo `D` → USB MEP (AL30D, GD30D)
- Sufijo `C` → USD cable (AL30C, GD30C)

Si vos podés tener el mismo bono en distintas monedas (compraste el mismo
con MEP y con cable), declarás 2 especies con tickers distintos.

### ADRs en IBKR

Para distinguir ADRs en IBKR (USD) de CEDEARs en BYMA (ARS), usá sufijo `_ADR`:
- `AAPL` → CEDEAR Apple en BYMA (ARS)
- `AAPL_ADR` → ADR Apple en IBKR (USD)

### Stablecoins

USDC y USDT se declaran con `is_stable=1` y `quote_vs=USD` en hoja `monedas`.
El motor resuelve el FX por paridad implícita: USDC se convierte a USD usando
el rate de USD CCL automáticamente.

### Fondos USB en CAFCI

CAFCI clasifica algunos fondos como "USD" pero en realidad son USB MEP
(operan localmente, no en el exterior). Marcalos en `especies` con
`Currency = USB`. El `cafci_loader.py` aplica el override automáticamente.

Ejemplos: `FIMA_PREMIUM_DOLARES_A`, `DELTA_RENTA_DOLARES_D`,
`FIMA_RENTA_FIJA_DOLARES_C`.
