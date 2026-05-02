# Loader histórico BYMA → CSV planilla v3.1

Script Python que se loguea contra el OMS, descarga precios de cierre BYMA
(con fallback CL → ACP → LA) para una lista de tickers, y calcula el FX
MEP (USB) y CCL (USD) implícitos por promedio ponderado por volumen sobre
los pares AL30/AL30D|C, GD30/GD30D|C, GD35/GD35D|C.

Genera dos CSVs en el formato exacto que espera la planilla v3.1:
- `precios_historico.csv` → para pegar en hoja **Precios Histórico**
- `fx_historico.csv` → para pegar en hoja **FX Histórico**

## Convención de valuación

**Todo en plazo 24hs (T+1)** — bonos AR, acciones, CEDEARs, BOPREALEs, AL30/GD30/GD35.
Cripto va aparte (no incluido en este loader).

## Setup

1. Asegurate de tener `secrets.txt` en la misma carpeta del script (o las
   env vars `OMS_USER` y `OMS_PASS` seteadas):

   ```
   OMS_USER=tu_usuario
   OMS_PASS=tu_password
   ```

2. Dependencias: `pip install pandas numpy requests`

## Uso

```bash
# Un ticker
python historico_byma_loader.py --tickers AL30D

# Varios
python historico_byma_loader.py --tickers AL30D GD30C TX26 TXMJ9

# Desde archivo
python historico_byma_loader.py --tickers-file tickers_ejemplo.txt

# Solo recalcular FX (sin lista de tickers)
python historico_byma_loader.py --solo-fx

# Solo precios, sin FX
python historico_byma_loader.py --tickers AL30D --skip-fx

# Output a otra carpeta (default es ./data)
python historico_byma_loader.py --tickers AL30D --output-dir ./mi_finanzas

# Forzar fecha (default: hoy). Útil si corrés tarde y querés marcar la
# fecha del cierre que estás capturando.
python historico_byma_loader.py --tickers AL30D --fecha 2026-04-30
```

## Comportamiento

- **Anexa, no pisa**: si el CSV ya existe, agrega filas nuevas y actualiza
  filas existentes que matcheen por (Fecha, Ticker) o (Fecha, Moneda).
- **Robusto**: si un ticker no responde o no tiene CL/ACP/LA válido, lo
  loggea y sigue con los demás.
- **Auto-incluye los 6 tickers FX** (AL30, AL30D, AL30C, GD30, GD30D,
  GD30C, GD35, GD35D, GD35C) en cada corrida — no hace falta pasarlos.
  Si pasás `--skip-fx`, no los pide.
- **Promedio ponderado por volumen** sobre los pares disponibles. Si para
  una fecha no hay volumen en ningún par, cae a promedio simple. Si ningún
  par cotizó, no escribe el FX de ese día.

## Workflow sugerido

Crontab (Linux) o Task Scheduler (Windows) para correrlo cada día hábil
después del cierre BYMA (~17:30 hora Argentina):

```cron
30 17 * * 1-5 cd /ruta/al/loader && python historico_byma_loader.py --tickers-file mis_tickers.txt --output-dir /ruta/a/mis_csvs
```

Después abrís los CSVs en Excel y pegás las filas nuevas en las hojas
`Precios Histórico` y `FX Histórico` de la planilla. O pegás todo y
ordenás por fecha (la planilla resuelve por la fecha más cercana ≤
fecha del trade).
