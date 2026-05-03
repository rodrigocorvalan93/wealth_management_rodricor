# wm_engine — Wealth Management Engine

Sistema personal de wealth management para portfolio multi-cuenta, multi-moneda
y multi-asset orientado al mercado argentino. Lleva el ledger del portfolio
(activos, cash, tarjetas, pasivos), valoriza a precios de mercado, calcula PnL
realizado FIFO y genera reportes Excel/HTML.

## Quick start

```bash
# 1. Instalar deps
pip install -r requirements.txt

# 2. Configurar secrets
cp secrets.example.txt secrets.txt
# editá secrets.txt con tus credenciales reales

# 3. Generar Excel master
python build_master.py inputs/wealth_management.xlsx
python add_carga_inicial_sheet.py inputs/wealth_management.xlsx

# 4. Cargar saldos iniciales en hoja `_carga_inicial` del Excel, después:
python -m cli.cargar_iniciales --fecha 2026-04-30

# 5. Bajar precios de mercado
python fx_loader.py
python byma_loader.py --tickers-file mis_tickers.txt
python cafci_loader.py
python cripto_loader.py
python yfinance_loader.py

# 6. Generar reporte
python -m cli.report --xlsx --html
```

## Arquitectura

```
INPUT
└─ inputs/wealth_management_*.xlsx (Excel master, 16 hojas)
       │
       ↓
LOADERS DE PRECIOS (cron-friendly)
├─ fx_loader.py        → data/fx_historico.csv     (MEP, CCL, mayorista)
├─ byma_loader.py      → data/precios_historico.csv (bonos, acciones, CEDEAR)
├─ cafci_loader.py     → data/precios_cafci.csv     (FCIs)
├─ cripto_loader.py    → data/precios_cripto.csv    (BTC, ETH, USDT, USDC)
└─ yfinance_loader.py  → data/precios_us.csv        (ADRs IBKR)
       │
       ↓
MOTOR (engine/)
├─ schema.py        → SQLite ledger doble entrada
├─ importer.py      → Excel → SQLite (events + movements)
├─ fx.py            → cross-rates con paridad implícita stablecoins
├─ prices.py        → upsert precios desde CSVs
├─ holdings.py      → posiciones + valuación a mercado
├─ pnl.py           → PnL realizado FIFO + no-realizado
├─ liabilities.py   → tarjetas (saldo actual / último resumen / próximo vto)
└─ exporter.py      → Excel multi-sheet + HTML autocontenido
       │
       ↓
CLI (cli/)
├─ cli.tarjetas        → resumen tarjetas en consola
├─ cli.summary         → portfolio summary en consola
├─ cli.cargar_iniciales → procesa hoja _carga_inicial → asientos_contables
└─ cli.report          → genera Excel + HTML en reports/
```

## Capacidades

- **Multi-currency** con cross-rates automáticos (ARS ↔ USB ↔ USD CCL ↔ stablecoins)
- **Multi-cuenta**: bancos, brokers, wallets cripto, tarjetas, cash físico
- **Multi-asset**: bonos AR, ADRs, CEDEAR, FCIs, cripto, equity
- **PnL FIFO** realizado y no-realizado, separado por moneda
- **Tarjetas** con cálculo automático de cierre/vencimiento
- **Conversión FX** automática en imports (cargás cost en una moneda, motor convierte a la nativa)
- **Idempotente**: re-correr no duplica data
- **Tolerante a falta de precios**: fallback a cost basis con marca visual

## Documentación

- [docs/setup.md](docs/setup.md) — instalación y configuración inicial
- [docs/excel_master.md](docs/excel_master.md) — guía de las 16 hojas del Excel
- [docs/loaders.md](docs/loaders.md) — loaders de precios y automatización
- [docs/cli.md](docs/cli.md) — comandos CLI y flujo diario
- [docs/architecture.md](docs/architecture.md) — modelo de datos del motor

## Licencia

Uso personal. No redistribuir.
