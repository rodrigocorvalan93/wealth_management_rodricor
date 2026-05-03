# Loaders de precios

Cinco loaders independientes que bajan precios de mercado a CSVs en `data/`.
Todos son **idempotentes** (re-correr no duplica) y **append-only** (no pisan
data vieja, solo actualizan filas con la misma key).

## fx_loader.py — FX (MEP, CCL, mayorista)

Bajará MEP, CCL y mayorista oficial desde dolarapi (intraday) o argentinadatos
(histórico).

```bash
# Del día actual
python fx_loader.py

# Histórico: últimos 30 días
python fx_loader.py --dias 30

# Histórico desde fecha específica
python fx_loader.py --desde 2024-01-01

# Solo print, no escribir
python fx_loader.py --dry-run
```

**Output**: `data/fx_historico.csv` con columnas `Fecha, Moneda, Rate, Cotiza vs, Fuente`.

**Mapeo de monedas**:
- `bolsa` → USB (MEP)
- `contadoconliqui` → USD (CCL — ancla de valuación)
- `mayorista` → USD_OFICIAL (A3500)

## byma_loader.py — Bonos AR / acciones / CEDEAR

Foto de precios BYMA en plazo 24hs (T+1). Prioridad LA → CL → ACP.

```bash
# Tickers individuales
python byma_loader.py --tickers AL30D GD30C TX26

# Desde archivo
python byma_loader.py --tickers-file mis_tickers.txt
```

**Requiere**: `OMS_USER` y `OMS_PASS` en `secrets.txt`.

**Output**: `data/precios_historico.csv`.

**Tip**: armá un `tickers_byma.txt` con todos tus instrumentos BYMA
y corré con `--tickers-file` cada día hábil.

## cafci_loader.py — FCIs

Bajará VCP (valor cuotaparte) de FCIs desde la API de CAFCI.

```bash
# Default: usa fcis_cafci.txt + override moneda desde Excel especies
python cafci_loader.py

# Sin override de moneda (respetar lo que dice CAFCI)
python cafci_loader.py --no-xlsx-currency

# Día específico
python cafci_loader.py --fecha 2026-04-30
```

**Requiere**: `CAFCI_TOKEN` en `secrets.txt`.

**Output**: `data/precios_cafci.csv`.

**Override de moneda**: el loader lee tu hoja `especies` del Excel master.
Si declaraste un FCI con `Currency=USB` pero CAFCI lo clasifica como USD
(caso típico de FIMA Premium Dólares, Delta Renta Dólares D), aplica
override automáticamente.

**Archivo `fcis_cafci.txt`**: una línea por FCI con formato `TICKER|NOMBRE_CAFCI`.
Ver template provisto en el repo.

## cripto_loader.py — Cripto

Precios de BTC, ETH, SOL, USDT, USDC desde CoinGecko.

```bash
python cripto_loader.py
python cripto_loader.py --tickers BTC,ETH,SOL
python cripto_loader.py --desde 2026-01-01
```

**Output**: `data/precios_cripto.csv`. Sin auth requerida.

## yfinance_loader.py — ADRs / equity US

Precios de ADRs y equity US desde Yahoo Finance.

```bash
python yfinance_loader.py
python yfinance_loader.py --tickers AMZN_ADR,MSFT_ADR,SPY_ADR
```

**Output**: `data/precios_us.csv`. Sin auth requerida.

**Convención de tickers**: usá sufijo `_ADR` para distinguir del CEDEAR en BYMA.
El loader hace la conversión interna `AMZN_ADR → AMZN` antes de consultar Yahoo.

## Automatización (cron / Task Scheduler)

### Linux/Mac (crontab)

```cron
# Todos los días hábiles a las 17:30 (después del cierre BYMA)
30 17 * * 1-5 cd /ruta/al/repo && /ruta/al/venv/bin/python fx_loader.py
35 17 * * 1-5 cd /ruta/al/repo && /ruta/al/venv/bin/python byma_loader.py --tickers-file mis_tickers.txt
40 17 * * 1-5 cd /ruta/al/repo && /ruta/al/venv/bin/python cafci_loader.py
45 17 * * 1-5 cd /ruta/al/repo && /ruta/al/venv/bin/python cripto_loader.py
50 17 * * 1-5 cd /ruta/al/repo && /ruta/al/venv/bin/python yfinance_loader.py
```

### Windows (PowerShell + Task Scheduler)

Creá un script `daily_loaders.ps1`:

```powershell
Set-Location "C:\Users\rodri\OneDrive - DELTA ASSET MANAGEMENT S.A\Documentos\wealth_management_rodricor"
python fx_loader.py
python byma_loader.py --tickers-file mis_tickers.txt
python cafci_loader.py
python cripto_loader.py
python yfinance_loader.py
```

Luego registralo en Task Scheduler para correr cada día hábil a las 17:30.

## Comportamiento ante fallas

- Si BYMA está caído: el loader loggea y sigue. El motor usa el último precio
  cargado o cae a cost basis (con marca visual `Fallback=Sí`).
- Si un ticker no responde: skipea solo ese, no rompe el resto.
- Si CAFCI cambió un nombre: aparece en "no encontrados" del log.

## Tip: tickers_byma.txt

Armá un archivo con todos tus tickers BYMA, uno por línea. Comentarios con `#`:

```
# Bonos en USB (MEP)
AO27D
AL30D
AE38D
GD30D
GD35D

# Bonos en USD (cable)
GD30C
GD35C

# LECAPs / Boncer / Duales
TX26
TZX28
TZXO6
T30J6
S31L6

# BOPREALES
BPC7D
MGCND
```

Y corrés:
```bash
python byma_loader.py --tickers-file tickers_byma.txt
```
