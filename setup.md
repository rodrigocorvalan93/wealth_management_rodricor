# Setup

## Requisitos

- Python 3.10+
- Acceso a internet (loaders de precios)
- Cuenta OMS (para `byma_loader.py`)
- Token CAFCI (para `cafci_loader.py`)

## Instalación

```bash
# 1. Clonar repo
git clone https://github.com/rodrigocorvalan93/wm_engine.git
cd wm_engine

# 2. Crear virtualenv (opcional pero recomendado)
python -m venv venv
# Linux/Mac:
source venv/bin/activate
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

# 3. Instalar dependencias
pip install -r requirements.txt
```

## Secrets

Copiá el template y completá:

```bash
cp secrets.example.txt secrets.txt
```

Editá `secrets.txt` con:

```
OMS_USER=tu_usuario_oms
OMS_PASS=tu_password_oms
CAFCI_TOKEN=Bearer eyJ...
BYMA_API_URL=https://api.cocos.xoms.com.ar/
```

`secrets.txt` está en `.gitignore` — nunca se sube al repo.

## Estructura de carpetas

Después del setup, tu proyecto debería verse así:

```
wm_engine/
├─ secrets.txt              # tus credenciales (no se commitea)
├─ inputs/                  # tu Excel master (no se commitea)
│   └─ wealth_management.xlsx
├─ data/                    # CSVs de precios + sqlite (no se commitea)
│   ├─ fx_historico.csv
│   ├─ precios_historico.csv
│   ├─ precios_cafci.csv
│   ├─ precios_cripto.csv
│   ├─ precios_us.csv
│   └─ wealth.db
├─ reports/                 # reportes generados (no se commitea)
│   ├─ 2026-05-03_portfolio.xlsx
│   └─ 2026-05-03_portfolio.html
├─ engine/                  # código del motor
├─ cli/                     # comandos CLI
├─ docs/                    # documentación
└─ tests/                   # tests offline
```

## Generar el Excel master

```bash
# Genera template con 15 hojas
python build_master.py inputs/wealth_management.xlsx

# Agrega hoja temporal _carga_inicial para cargar saldos
python add_carga_inicial_sheet.py inputs/wealth_management.xlsx
```

## Validar instalación

```bash
# Tests offline (no usan internet)
python tests/test_engine.py
python tests/test_byma_loader.py
python tests/test_cafci_loader.py
python tests/test_fx_loader.py
```

Si todos pasan, está OK.
