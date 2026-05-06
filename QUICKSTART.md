# Quickstart — probar la app localmente

## 1. Instalación

```bash
git clone <repo>
cd wealth_management_rodricor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Variables de entorno mínimas (modo dev)

```bash
# Vos como superadmin
export WM_BOOTSTRAP_SUPERADMIN_EMAIL=tu@email.com
# Auto-verificar el primer signup (si aún no tenés SMTP)
export WM_AUTO_VERIFY_FIRST_SUPERADMIN=1

# Encriptación de credenciales del broker (opcional — si no lo seteás
# se autogenera en data/.encryption_key)
export WM_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Disable rate limiting para development
export WM_DISABLE_RATELIMIT=1
```

## 3. Correr el server

```bash
export FLASK_APP=api.app
flask run --port 5000
```

Abrí http://localhost:5000

## 4. Primer login

1. Tocá **"Crear una"** abajo del form de login
2. Email: el mismo de `WM_BOOTSTRAP_SUPERADMIN_EMAIL`
3. Contraseña: 8+ chars con letras + números
4. Como `WM_AUTO_VERIFY_FIRST_SUPERADMIN=1`, te logueás directo sin email funcional

Listo. Vos sos superadmin.

## 5. Email funcional (opcional, para producción)

Gmail con 2FA → crear "App password" en https://myaccount.google.com/apppasswords

```bash
export WM_SMTP_HOST=smtp.gmail.com
export WM_SMTP_PORT=587
export WM_SMTP_USER=tu_email@gmail.com
export WM_SMTP_PASS=la_app_password
export WM_SMTP_FROM='Wealth Management <noreply@tudominio.com>'
export WM_APP_URL=https://wm.tudominio.com
```

Sin SMTP, los emails caen a `data/_outbox/*.eml` (los podés mandar a mano).

## 6. Auto-import desde brokers

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

## 7. Test suite

```bash
WM_DISABLE_RATELIMIT=1 python -m pytest tests/ --ignore=tests/test_loader.py
```

148+ tests pasan. Las 8 fallas en test_pwa.py al correr todo junto son
test pollution preexistente (pasan en isolation).
