# Deploy a producción

3 caminos según presupuesto + control. **Recomiendo Render** (más simple).

| Plataforma | Costo | Persistencia | HTTPS | Esfuerzo |
|---|---|---|---|---|
| **Render.com** ⭐ | $7/mes (Starter) | sí (disco) | auto | 15 min |
| **Fly.io** | ~$2/mes | sí (volumen) | auto | 30 min |
| **PythonAnywhere** | gratis o $5/mes | sí | sí | 20 min |

> **Nota**: La app guarda passwords + datos financieros — **siempre usá HTTPS**.
> Render y Fly te lo dan automáticamente; en PA viene incluido en el dominio.
> Si vas a un VPS (Hetzner / DigitalOcean), montá Caddy o Nginx + Let's Encrypt.

---

## Antes de cualquier deploy: pre-requisitos

1. **Repo en GitHub** (o GitLab). Render/Fly hacen auto-deploy desde ahí.
2. **Email para superadmin** — el que te va a hacer dueño del deploy.
3. **App password de Gmail** (opcional, para SMTP). Crear en
   https://myaccount.google.com/apppasswords (necesitás 2FA activado).
4. **Encryption key** generada — Render lo hace automático, en otros
   plataformas:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

---

## Opción 1 — Render.com (recomendado)

### 1. Setup inicial (una vez)

1. Hacé un account en https://render.com (free).
2. Conectá tu repo de GitHub en **Account → Connect GitHub**.

### 2. Deploy con `render.yaml`

El archivo `render.yaml` ya está en el repo. Render lo detecta automáticamente.

1. **Render dashboard → New → Blueprint**.
2. Seleccioná tu repo `wealth_management_rodricor`.
3. Branch: `main` (o la que querés deployar).
4. Render lee el `render.yaml` y muestra los servicios + secrets requeridos:
   - **`WM_BOOTSTRAP_SUPERADMIN_EMAIL`** → tu email (el que va a quedar como superadmin).
   - **`WM_APP_URL`** → la URL pública del servicio. Render te la asigna después
     del primer deploy (algo como `https://wm-wealth-management.onrender.com`).
     La podés dejar vacía al principio y editarla después.
   - **SMTP vars** (`WM_SMTP_HOST`, `WM_SMTP_USER`, etc.) — opcional. Si no
     las setteás, los emails de signup/reset caen al outbox del server.
5. Click **Apply**. Render builda la imagen Docker, monta el disco, arranca
   el servicio. Tarda ~3 min.

### 3. Primer signup

1. Abrí la URL del servicio (Render te la muestra arriba del log).
2. **"Crear una"** → registrate con el mismo email del bootstrap.
3. Como `WM_AUTO_VERIFY_FIRST_SUPERADMIN=1`, te logueás directo.
4. **Apenas estés adentro**: andá a Render dashboard y vacíá la var
   `WM_AUTO_VERIFY_FIRST_SUPERADMIN` (o ponela en `0`). Si no, cualquier
   futuro signup que matchee tu email se podría auto-verificar (no es un
   problema crítico porque ya hay un superadmin, pero es buena higiene).

### 4. Custom domain (opcional)

Render → tu servicio → **Settings → Custom Domains** → agregá tu dominio.
Render te da un CNAME para apuntar. Después actualizá `WM_APP_URL` con el
nuevo dominio (importante para los links de email).

### 5. Update sin downtime

Cada push a la branch configurada (`main` por default) re-deploya automáticamente.
Para hot-fix:
```bash
git push origin main
# Render detecta el push, builda, y hace blue-green deploy (sin downtime).
```

---

## Opción 2 — Fly.io (más barato)

### 1. Instalar flyctl

```bash
# macOS / Linux
curl -L https://fly.io/install.sh | sh

# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex
```

### 2. Login + launch

```bash
fly auth signup        # o fly auth login
cd wealth_management_rodricor
fly launch --no-deploy --copy-config   # te pregunta region (eze para Argentina)
```

Si te pregunta "Would you like to set up a Postgres DB?" → **No** (usamos sqlite).

### 3. Crear volumen + secrets

```bash
fly volumes create wm_data --size 1 --region eze

fly secrets set \
  WM_BOOTSTRAP_SUPERADMIN_EMAIL=tu@email.com \
  WM_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  WM_AUTO_VERIFY_FIRST_SUPERADMIN=1 \
  WM_APP_URL=https://wm-wealth-management.fly.dev
```

### 4. Deploy

```bash
fly deploy
```

Tarda 2-5 min. Cuando termina:
```bash
fly status
fly open       # abre el browser en la URL
```

### 5. SMTP opcional

```bash
fly secrets set \
  WM_SMTP_HOST=smtp.gmail.com \
  WM_SMTP_PORT=587 \
  WM_SMTP_USER=tu@email.com \
  WM_SMTP_PASS=tu_app_password \
  WM_SMTP_FROM="Wealth Management <noreply@tudominio.com>"
```

### 6. Re-deploys

```bash
git push       # opcional, Fly no auto-deploya
fly deploy     # explícito
```

---

## Opción 3 — PythonAnywhere (free / $5)

> ⚠ Free tier de PA tiene **outbound HTTPS limitado a una whitelist**. Los
> loaders de precios (BYMA, CAFCI, yfinance, Binance, IBKR Flex) **no van
> a funcionar** ahí. Workaround: corrés los loaders en tu PC y los subís
> vía API.
>
> Para producción real comprá el "Hacker" plan ($5/mes) que abre la red.

### 1. Bash console

```bash
cd ~
git clone https://github.com/rodrigocorvalan93/wealth_management_rodricor.git
cd wealth_management_rodricor
mkvirtualenv --python=python3.11 wm_env
pip install -r requirements.txt
```

### 2. Web tab → Add a new web app → Manual config → Python 3.11

Settings:
- **Source**: `/home/<usuario>/wealth_management_rodricor`
- **Working dir**: igual
- **Virtualenv**: `/home/<usuario>/.virtualenvs/wm_env`

### 3. WSGI configuration file

Click el link y reemplazá TODO el contenido por:

```python
import os, sys

project = '/home/<usuario>/wealth_management_rodricor'
if project not in sys.path:
    sys.path.insert(0, project)

# === Auth con email/password ===
os.environ['WM_BOOTSTRAP_SUPERADMIN_EMAIL'] = 'tu@email.com'
os.environ['WM_AUTO_VERIFY_FIRST_SUPERADMIN'] = '1'   # vacíalo después del primer signup
os.environ['WM_ENCRYPTION_KEY'] = 'PEGÁ_AQUÍ_UNA_KEY_FERNET'   # generala con python -c "..."
os.environ['WM_APP_URL'] = 'https://<usuario>.pythonanywhere.com'

# === Paths ===
os.environ['WM_BASE_DIR'] = project
os.environ['WM_ANCHOR'] = 'USD'

# === SMTP (opcional) ===
# os.environ['WM_SMTP_HOST'] = 'smtp.gmail.com'
# os.environ['WM_SMTP_USER'] = 'tu@gmail.com'
# os.environ['WM_SMTP_PASS'] = 'app_password'

from api.wsgi import application
```

Replazá `<usuario>` por tu username de PA.

### 4. Reload + signup

- **Web → Reload**.
- Abrí `https://<usuario>.pythonanywhere.com` → "Crear una" → tu email.

---

## Producción real — checklist de seguridad

Antes de invitar a otros users:

- [ ] **HTTPS forzado** (Render/Fly lo hacen, en VPS configurá HSTS).
- [ ] **`WM_AUTO_VERIFY_FIRST_SUPERADMIN=0`** después del primer signup.
- [ ] **`WM_ENCRYPTION_KEY` generada con Fernet**, NO un valor débil. Si la cambiás
      a futuro, todas las credenciales encriptadas se vuelven ilegibles —
      backupeala antes.
- [ ] **SMTP configurado** (sino los users no reciben verify/reset emails).
- [ ] **`WM_DISABLE_RATELIMIT` NO seteado** (en prod queremos rate limiting activo).
- [ ] **Backups del disco** — Render no hace snapshot automático del disco
      en plan Starter; configurá un cron que copie `data/` a S3 cada 24h.
      En Fly: `fly volumes snapshots create wm_data` periódicamente.
- [ ] **Logs**: revisá `audit.log` per-user de vez en cuando para detectar
      patterns sospechosos (logins de IPs raras, etc).
- [ ] **Custom domain** (no `*.onrender.com`) si lo vas a compartir con
      colegas — más confianza visual.

---

## Troubleshooting

### "ModuleNotFoundError: cryptography" en deploy

Asegurate que `requirements.txt` tiene `cryptography>=41.0` y que
re-buildeaste la imagen.

### "WM_BOOTSTRAP_SUPERADMIN_EMAIL no funciona"

El primer signup que matchee ese email se promueve. Si ya hay un
superadmin previo, NO se promueve nadie más automático. Para promover
manual:
```sql
-- abrir auth.db
UPDATE auth_users SET is_superadmin=1, is_admin=1 WHERE email='tu@email.com';
```

### Los emails no llegan

Si SMTP está configurado y igual no llegan, mirá los logs del server.
Errores típicos: app password mal copiada, 2FA no activado en Gmail.
Si SMTP no está configurado, los emails caen a `data/_outbox/*.eml`
(podés bajarlos via shell en Render/Fly y mandarlos a mano).

### En Render, los datos se borran después de cada deploy

Estás en plan **Free** (sin disco persistente). Pasá a **Starter** ($7/mes)
o migrá a Fly.io. Sin disco, cada deploy crea un container nuevo y los
xlsx + auth.db se pierden.

### "rate limit excedido"

En prod normal, 240 reads/min y 60 writes/min son muy generosos. Si
estás hitting el límite es porque hay un loop o algo ataca tu API.
Mirá `audit.log` y `auth_audit` table en `auth.db`.
