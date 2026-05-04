# Deploy a PythonAnywhere — `rodricor.pythonanywhere.com`

Guía paso a paso para deployar el backend Flask del wealth_management.

## Pre-requisitos

- Cuenta PythonAnywhere "Beginner" (free) — ✅ ya la tenés.
- Tu Excel master ya migrado con `python migrate_master.py` — corré esto en tu PC primero.

## ⚠️ Limitaciones del free tier que afectan este proyecto

- **Outbound HTTPS limitado a una whitelist** (Google, GitHub, etc.). Tus loaders de precios (BYMA, dolarapi, CAFCI, CoinGecko, yfinance) **probablemente no funcionen ahí**. Workaround: corré los loaders en tu PC y subí los CSVs vía `POST /api/upload/prices`.
- **1 web app**, dominio fijo `rodricor.pythonanywhere.com`.
- Sin SSH (Bash console sí). Sin cron jobs en free.

## 1. Subir el código

### Opción A: clonar desde GitHub (recomendado)

Abrí una **Bash console** en PA y corré:

```bash
cd ~
git clone https://github.com/rodrigocorvalan93/wealth_management_rodricor.git
cd wealth_management_rodricor
```

Si ya está clonado, actualizalo:
```bash
cd ~/wealth_management_rodricor
git fetch origin claude/check-repo-version-YGx91
git checkout claude/check-repo-version-YGx91
git pull
```

### Opción B: subir por SFTP

Subí toda la carpeta `wealth_management_rodricor/` a `/home/rodricor/`.

## 2. Crear virtualenv e instalar dependencias

En la Bash console:

```bash
cd ~/wealth_management_rodricor
mkvirtualenv --python=python3.10 wm_env
pip install -r requirements.txt
```

`mkvirtualenv` es un helper de PA que crea el venv en `~/.virtualenvs/wm_env`.

## 3. Crear la web app en PythonAnywhere

1. Andá a **Web** → **Add a new web app** → click "Next".
2. Elegí **Manual configuration** (no Flask wizard).
3. Elegí **Python 3.10**.
4. Click "Next" hasta crear.

## 4. Configurar paths del web app

En la sección **Web → tu app**:

- **Source code**: `/home/rodricor/wealth_management_rodricor`
- **Working directory**: `/home/rodricor/wealth_management_rodricor`
- **Virtualenv**: `/home/rodricor/.virtualenvs/wm_env`

## 5. Editar el WSGI configuration file

Click en el link **WSGI configuration file** (algo como `/var/www/rodricor_pythonanywhere_com_wsgi.py`) y reemplazá TODO el contenido por:

```python
import os
import sys

project = '/home/rodricor/wealth_management_rodricor'
if project not in sys.path:
    sys.path.insert(0, project)

# === REQUERIDO: token de auth ===
# Generá uno largo random — ej: openssl rand -hex 32
os.environ['WM_API_TOKEN'] = 'PEGÁ_ACÁ_UN_TOKEN_LARGO_RANDOM'

# === Paths (defaults derivan de WM_BASE_DIR) ===
os.environ['WM_BASE_DIR'] = project
os.environ['WM_ANCHOR'] = 'USD'   # o 'ARS', 'USB'

# Importar la app
from api.wsgi import application
```

**Importante**: ese token es el password del API. Generá uno random fuerte:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 6. Subir el Excel master inicial

En la Bash console:

```bash
mkdir -p ~/wealth_management_rodricor/inputs
mkdir -p ~/wealth_management_rodricor/data
```

Después subí tu Excel **ya migrado** vía:

- **Files** tab en PA → navegá a `wealth_management_rodricor/inputs/` → "Upload a file" → elegí tu `wealth_management_rodricor.xlsx` migrado.

O por API una vez que la app esté arriba (ver paso 9).

## 7. Reload de la web app

Click en **Reload rodricor.pythonanywhere.com** (botón verde arriba).

## 8. Verificar que arranca

```bash
curl https://rodricor.pythonanywhere.com/api/health
```

Esperado:
```json
{
  "status": "ok",
  "version": "1.0",
  "xlsx_present": true,
  "db_present": false,
  "anchor_default": "USD",
  "auth_configured": true,
  "now": "2026-05-04T..."
}
```

Si `xlsx_present: false`, subí el Excel (paso 6).

## 9. Primer import

```bash
TOKEN='tu_token_aquí'
curl -X POST https://rodricor.pythonanywhere.com/api/refresh \
  -H "Authorization: Bearer $TOKEN"
```

Esperado: `{ "refreshed": true, "import_stats": { ... } }`.

## 10. Probar endpoints

```bash
# Resumen del portfolio
curl -H "Authorization: Bearer $TOKEN" \
     https://rodricor.pythonanywhere.com/api/summary

# Lista de trades
curl -H "Authorization: Bearer $TOKEN" \
     https://rodricor.pythonanywhere.com/api/sheets/blotter

# Reporte HTML (abrir en browser con header — usá una extensión, o curl + redirect)
curl -H "Authorization: Bearer $TOKEN" \
     https://rodricor.pythonanywhere.com/api/report/html > /tmp/report.html
open /tmp/report.html
```

## 11. Subir un nuevo Excel desde local

Si editaste tu Excel localmente (en Excel/LibreOffice) y querés sincronizar:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@inputs/wealth_management_rodricor.xlsx" \
  https://rodricor.pythonanywhere.com/api/upload/excel
```

El server hace backup automático del anterior antes de sobreescribir.

## 12. Subir CSVs de precios (loaders desde tu PC)

Como los loaders no corren en PA free tier, los corrés en tu PC y después:

```bash
for csv in data/precios_historico.csv data/fx_historico.csv \
           data/precios_cafci.csv data/precios_cripto.csv data/precios_us.csv; do
  curl -X POST -H "Authorization: Bearer $TOKEN" \
       -F "file=@$csv" \
       https://rodricor.pythonanywhere.com/api/upload/prices
done
```

## 13. Restaurar desde backup

Cada vez que el API hace un write, antes guarda copia del Excel en `data/excel_backups/`. Para listar:

```bash
curl -H "Authorization: Bearer $TOKEN" \
     https://rodricor.pythonanywhere.com/api/backups
```

Para restaurar uno específico, abrí Bash console en PA:

```bash
cd ~/wealth_management_rodricor
ls data/excel_backups/                          # listar backups
cp data/excel_backups/wealth_management_rodricor.backup-2026-05-04T15-30-00.xlsx \
   inputs/wealth_management_rodricor.xlsx
# Y reload el web app o:
curl -X POST -H "Authorization: Bearer $TOKEN" \
     https://rodricor.pythonanywhere.com/api/refresh
```

## 14. Logs

En **Web → Log files** vas a ver:
- `error.log`: tracebacks de Python.
- `server.log`: requests crudos.

Ante un 500, mirá el error.log primero.

## Checklist final

- [ ] Repo clonado en `/home/rodricor/wealth_management_rodricor`
- [ ] Virtualenv `wm_env` con `pip install -r requirements.txt` corrido
- [ ] WSGI file editado con `WM_API_TOKEN` random
- [ ] Excel master subido a `inputs/`
- [ ] Web app reloaded
- [ ] `/api/health` responde 200
- [ ] `/api/refresh` corrió OK al menos una vez
- [ ] Token guardado seguro en tu manager de passwords

## Troubleshooting

**"Internal Server Error" en cualquier endpoint**
→ Mirá `error.log` en Web → Log files.

**`ModuleNotFoundError: flask`**
→ El virtualenv no está bien linkeado. Verificá la ruta exacta en Web → Virtualenv.

**"WM_API_TOKEN no configurado"**
→ El env var no está siendo leído. Editá el WSGI file y reloaded.

**Endpoints lentos**
→ El reimport del Excel toma 1-2s. Si tu Excel crece a >1000 filas, considerar paid tier.

**El loader de precios no funciona**
→ Esperado. Corré loaders en tu PC y subí CSVs (paso 12).
