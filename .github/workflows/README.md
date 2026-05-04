# GitHub Actions — automatizaciones

Workflows que mantienen el server actualizado sin que tengas que correr nada
manualmente desde la PC.

## Workflows incluidos

### `sync-prices.yml` — corre loaders y sube CSVs (5x/semana)

Corre los loaders de precios (FX, BYMA, CAFCI, cripto, yfinance) en un runner
de GitHub y sube los CSVs resultantes al server vía API.

- **Schedule**: lunes a viernes 22:30 UTC (19:30 hora AR — después del cierre BYMA).
- **Manual**: tab **Actions** en GitHub → "Sync prices" → "Run workflow".

### `daily-snapshot.yml` — fuerza refresh y snapshot diario del PN

Pega `/api/refresh` y luego `/api/report/html` para que se genere el snapshot
del día. Asegura una equity curve continua sin huecos.

- **Schedule**: todos los días 23:00 UTC (20:00 AR).
- **Manual**: ídem.

## Setup (una sola vez)

### 1. Configurar GitHub Secrets

En GitHub → Settings → Secrets and variables → Actions → "New repository secret":

| Secret | Valor | Descripción |
|---|---|---|
| `WM_API_TOKEN` | tu token de 64 hex | Token del API que generaste con `secrets.token_hex(32)` |
| `WM_API_URL` | `https://rodricor.pythonanywhere.com` | URL base del API |
| `OMS_USER` | tu usuario | Para byma_loader.py |
| `OMS_PASS` | tu password | Para byma_loader.py |
| `BYMA_API_URL` | URL del OMS | Default: `https://api.cocos.xoms.com.ar/` |
| `CAFCI_TOKEN` | `Bearer eyJ...` | Para cafci_loader.py |

### 2. Habilitar Actions

Si tu repo es privado, GitHub Actions **gratis** te da 2000 minutos/mes (más que
suficiente para estos workflows que corren ~10 minutos/día).

Tab Actions → "I understand my workflows, go ahead and enable them".

### 3. Probar manualmente

Antes del primer schedule automático, corré uno manualmente:

1. Tab **Actions**
2. Workflow **"Sync prices to PythonAnywhere"** (sidebar izquierdo)
3. Botón **"Run workflow"** → "Run workflow" verde
4. Refrescá la página, mirá el run en curso → click → revisá el log

Si todo OK, el primer scheduled run pasa solo.

## Costos

Free tier de GitHub Actions:
- Repos públicos: **ilimitado**
- Repos privados: 2000 minutos/mes (cada job pesa ~3-10 min, así que tranquilo
  con 2 jobs/día)

PythonAnywhere free no cobra por requests entrantes — los workflows funcionan
sin tocar nada.

## Troubleshooting

**Workflow falla con "401 Unauthorized"**
→ El `WM_API_TOKEN` en secrets no matchea el del WSGI de PA. Regenerá y
   actualizá ambos lados.

**Workflow falla en `byma_loader.py`**
→ Tu cuenta OMS expiró el token o cambió la URL. Probalo localmente primero.

**Workflows no corren en el schedule**
→ GitHub a veces retrasa schedules en repos sin actividad reciente. Hacé un
   commit dummy o forzalo manualmente.

**Quiero deshabilitar uno**
→ Tab Actions → click el workflow → "..." arriba a la derecha → "Disable workflow".
