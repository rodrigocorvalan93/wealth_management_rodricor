# Quickstart — probar la app localmente

> ⚠ **Linux/macOS** usan `export VAR=valor`.
> **Windows PowerShell** usa `$env:VAR = "valor"`.
> **Windows CMD** usa `set VAR=valor`.

## 1. Instalación

### Linux / macOS

```bash
git clone <repo>
cd wealth_management_rodricor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
git clone <repo>
cd wealth_management_rodricor
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Variables de entorno mínimas (modo dev)

### Linux / macOS

```bash
export WM_BOOTSTRAP_SUPERADMIN_EMAIL=tu@email.com
export WM_AUTO_VERIFY_FIRST_SUPERADMIN=1
export WM_DISABLE_RATELIMIT=1
```

### Windows PowerShell

```powershell
$env:WM_BOOTSTRAP_SUPERADMIN_EMAIL = "tu@email.com"
$env:WM_AUTO_VERIFY_FIRST_SUPERADMIN = "1"
$env:WM_DISABLE_RATELIMIT = "1"
```

> **Importante en Windows**: las env vars se pierden al cerrar la ventana
> de PowerShell. Para que persistan en el siguiente login usá:
> ```powershell
> [Environment]::SetEnvironmentVariable("WM_BOOTSTRAP_SUPERADMIN_EMAIL", "tu@email.com", "User")
> ```
> O simplemente usá el script `dev_run.ps1` (más abajo).

## 3. Correr el server

```bash
# Linux / macOS
flask --app api.app run --port 5000

# Windows PowerShell
flask --app api.app run --port 5000
```

(El flag `--app api.app` reemplaza tener que setear `FLASK_APP` antes.)

Abrí http://localhost:5000

## 4. Primer login

1. Tocá **"Crear una"** abajo del form de login
2. Email: el mismo de `WM_BOOTSTRAP_SUPERADMIN_EMAIL`
3. Contraseña: 8+ chars con letras + números
4. Como `WM_AUTO_VERIFY_FIRST_SUPERADMIN=1`, te logueás directo sin email funcional

Listo. Vos sos superadmin.

## 5. Script todo-en-uno para Windows

Creá un archivo `dev_run.ps1` en la raíz del proyecto con esto:

```powershell
# dev_run.ps1 — bootstrap + run en una sola línea
$env:WM_BOOTSTRAP_SUPERADMIN_EMAIL = "tu@email.com"   # cambiá esto
$env:WM_AUTO_VERIFY_FIRST_SUPERADMIN = "1"
$env:WM_DISABLE_RATELIMIT = "1"
.\venv\Scripts\Activate.ps1
flask --app api.app run --port 5000
```

Después corrés:

```powershell
.\dev_run.ps1
```

## 6. Email funcional (opcional, para producción)

Gmail con 2FA → crear "App password" en https://myaccount.google.com/apppasswords

### Linux / macOS

```bash
export WM_SMTP_HOST=smtp.gmail.com
export WM_SMTP_PORT=587
export WM_SMTP_USER=tu_email@gmail.com
export WM_SMTP_PASS=la_app_password
export WM_SMTP_FROM='Wealth Management <noreply@tudominio.com>'
export WM_APP_URL=https://wm.tudominio.com
```

### Windows PowerShell

```powershell
$env:WM_SMTP_HOST = "smtp.gmail.com"
$env:WM_SMTP_PORT = "587"
$env:WM_SMTP_USER = "tu_email@gmail.com"
$env:WM_SMTP_PASS = "la_app_password"
$env:WM_SMTP_FROM = "Wealth Management <noreply@tudominio.com>"
$env:WM_APP_URL = "https://wm.tudominio.com"
```

Sin SMTP, los emails caen a `data/_outbox/*.eml` (los podés mandar a mano).

## 7. Auto-import desde brokers

Settings → Brokers → **Auto-importar tenencias**. Soporta:

| Broker | Necesita | Notas |
|---|---|---|
| **Cocos / OMS BYMA** | usuario+password del OMS | mismas creds del loader de precios |
| **Binance** | API key + secret | crear con SOLO permiso "Enable Reading" |
| **IBKR** | Flex token + Query ID | configurar Flex Query con sección "Open Positions" |

Cada importer:
1. Pide credenciales en `/credentials`
2. Levanta tus posiciones (read-only) en `/import/<broker>`
3. Te muestra preview con tickers conocidos vs nuevos
4. Vos elegís qué importar y a qué cuenta
5. Escribe en `_carga_inicial` del Excel master, después re-importa

## 8. Test suite

```bash
# Linux / macOS
WM_DISABLE_RATELIMIT=1 python -m pytest tests/ --ignore=tests/test_loader.py

# Windows PowerShell
$env:WM_DISABLE_RATELIMIT = "1"
python -m pytest tests/ --ignore=tests/test_loader.py
```

148+ tests pasan. Las 8 fallas en test_pwa.py al correr todo junto son
test pollution preexistente (pasan en isolation).

