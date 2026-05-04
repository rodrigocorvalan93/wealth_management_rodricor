# Multi-tenant — guía de migración + onboarding del amigo

La app ahora soporta **múltiples usuarios** sobre el mismo deploy de
PythonAnywhere. Cada user tiene su propio Excel master, su propia DB y sus
backups separados. Los CSVs de precios se comparten (un solo set, todos los
users se benefician).

## Arquitectura

```
inputs/
  rodricor/wealth_management.xlsx       # tu master
  amigo/wealth_management.xlsx           # master del amigo
data/
  fx_historico.csv                       # SHARED — un solo set
  precios_*.csv                          #          de precios
  tickers_union.txt                      # auto-generado
  rodricor/wealth.db                     # tu DB
  rodricor/excel_backups/
  amigo/wealth.db
  amigo/excel_backups/
```

## Migrar tu setup actual

### 1. En tu Bash console de PA:

```bash
cd ~/wealth_management_rodricor
git fetch origin claude/check-repo-version-YGx91
git pull
python migrate_to_multitenant.py --user-id rodricor
```

Esto mueve:
- `inputs/wealth_management_rodricor.xlsx` → `inputs/rodricor/wealth_management.xlsx`
- `data/wealth.db` → `data/rodricor/wealth.db`
- `data/excel_backups/*.xlsx` → `data/rodricor/excel_backups/`

(Los CSVs de precios se quedan en `data/`).

### 2. Editá tu WSGI file

Reemplazá la línea `os.environ['WM_API_TOKEN'] = '...'` por:

```python
import json
os.environ['WM_USERS_JSON'] = json.dumps({
    "rodricor": {
        "token": "TU_TOKEN_DE_64_CHARS",
        "is_admin": True,
        "display_name": "Rodrigo"
    }
})
os.environ['WM_ADMIN_USER'] = 'rodricor'
```

> Si dejás `WM_API_TOKEN` solo (sin `WM_USERS_JSON`), la app sigue funcionando
> en modo single-tenant (back-compat). El path se actualiza solo a la
> estructura `inputs/default/` después de la primera request.

### 3. Reload del web app

Web → Reload. Tu PWA debería seguir funcionando exactamente igual que antes,
pero con la nueva estructura interna.

## Crear un usuario nuevo (amigo)

### Opción A: desde la PWA (recomendado)

1. Login como admin (con tu token)
2. Settings → Admin → "Gestión de usuarios"
3. Click "+ Crear usuario"
4. Llenar:
   - **user_id**: handle (ej `marcos`, lowercase, sin espacios)
   - **Nombre display**: "Marcos Pérez"
   - **¿Es admin?**: No (default)
5. Click "Crear usuario"
6. Te aparece un alert con:
   - URL para compartir: `https://rodricor.pythonanywhere.com`
   - Token random de 64 chars
   - **Snippet a copiar al WSGI** (importante para persistir)
7. Copiá el snippet del alert y pegalo en tu WSGI file (reemplazando el
   `WM_USERS_JSON` viejo). Reload del web app.
8. Mandale al amigo por WhatsApp directo: URL + token.

### Opción B: editar WSGI a mano

Tu WSGI:

```python
os.environ['WM_USERS_JSON'] = json.dumps({
    "rodricor": {"token": "...", "is_admin": True},
    "amigo": {"token": "GENERAR_OTRO_TOKEN"}
})
```

Generá el token del amigo:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

En PA Bash console, creá el folder + master inicial:

```bash
cd ~/wealth_management_rodricor
mkdir -p inputs/amigo data/amigo/excel_backups
python build_master.py inputs/amigo/wealth_management.xlsx
python add_carga_inicial_sheet.py inputs/amigo/wealth_management.xlsx
```

Reload del web app. Mandale al amigo URL + token.

## Onboarding del amigo

Cuando el amigo entra a `https://rodricor.pythonanywhere.com` por primera vez:

1. Pega su token en la pantalla de login
2. Ve la PWA con dashboard vacío
3. **Setup wizard** automático lo guía:
   - Bajar el Excel master (botón en wizard)
   - Completar en su PC al menos:
     - Hoja `cuentas` (sus cuentas)
     - Hoja `especies` (sus tickers)
     - Hoja `_carga_inicial` (sus saldos al día de hoy):
       ```
       Fecha       | Cuenta | Activo | Qty  | Unit Price | Price Currency
       2026-05-04  | cocos  | AL30D  | 1000 | 65.5       | USB
       ```
   - Subir el Excel completado
4. La app procesa `_carga_inicial` automáticamente y le bootstrappea el
   portfolio con asientos de apertura.
5. A partir de ahí usa la PWA igual que vos: cargar trades, gastos, ver
   reportes, etc.

## Switch user (admin only)

Como admin, podés ver datos de otros users en read-only:

1. Settings → Admin → tap el user que querés ver
2. Click "👁 ver"
3. Te aparece un banner amarillo "Modo Switch User" en /admin
4. Toda la PWA muestra los datos del target user
5. POST/PUT/DELETE están **bloqueados** (no podés modificar datos del amigo)
6. Para volver: tap "← Volver a tu user" en el banner o en Settings

## Loaders union (auto)

Cuando corras `python sync.py` desde tu PC:

1. Se genera `data/tickers_union.txt` automáticamente, escaneando todos los
   masters de todos los users
2. Los loaders fetchean **todos** los tickers (los tuyos + los del amigo)
3. Los CSVs salen en `data/` (compartido)
4. Subís todo al server con un solo `python sync.py`
5. Cada user al hacer GET en su PWA ve solo los precios de los tickers que
   tiene en SU `especies`

Ningún cambio de UX para vos. El amigo se beneficia gratis.

## Limitaciones / TODO

- **Persistencia de users**: el endpoint POST /api/admin/users actualiza
  `WM_USERS_JSON` en memoria, pero **se pierde al reload**. Hay que copiar
  el snippet al WSGI manualmente. Mejora futura: persistir a un archivo
  `users.json` que el WSGI lee al boot.
- **No hay self-signup**: solo el admin puede crear users. Es lo correcto
  para un deploy pequeño.
- **Switch user es read-only**: no podés "ayudar" al amigo cargando trades
  desde tu sesión. Tiene que loguearse él (o le pedís su token).

## Troubleshooting

**El amigo entra y ve mis datos**
→ Te equivocaste de token. Cada user tiene SU token único, asegurate de
   mandarle el de él, no el tuyo.

**"Token inválido o ausente" después de crear user via PWA**
→ El user existe en memoria del server pero no en el WSGI. Reload del web
   app va a perder el user. Solución: copiá el snippet del alert al WSGI.

**Los CSVs de precios no se actualizan para el amigo**
→ Son shared. Si vos corriste sync.py, el amigo automáticamente los ve. Si
   no, ningún user los ve. Corré `python sync.py` desde tu PC.

**Quiero que el amigo pueda correr sus propios loaders**
→ Mandale el repo + secrets.txt con su WM_API_TOKEN apuntando al mismo
   server. Después corre `python sync.py` desde su PC. Los CSVs los
   updateará en `data/` shared.
