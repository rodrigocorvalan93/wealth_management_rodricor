#!/usr/bin/env bash
# dev_run.sh — bootstrap + run de la app (Linux / macOS)
#
# Uso:
#   chmod +x dev_run.sh
#   ./dev_run.sh

# === CONFIGURÁ ESTO ===
SUPERADMIN_EMAIL="rodrigocorvalan93@gmail.com"
PORT=5000

# === Activar venv si existe ===
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
else
  echo "WARN: sin venv detectado. Creá uno:"
  echo "  python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
fi

# === Env vars (dev) ===
export WM_BOOTSTRAP_SUPERADMIN_EMAIL="$SUPERADMIN_EMAIL"
export WM_AUTO_VERIFY_FIRST_SUPERADMIN=1
export WM_DISABLE_RATELIMIT=1
export WM_APP_URL="http://localhost:${PORT}"

echo
echo "=============================================="
echo "  WM Wealth Management — modo dev"
echo "=============================================="
echo "  Superadmin email: $SUPERADMIN_EMAIL"
echo "  URL: http://localhost:$PORT"
echo
echo "  Pasos:"
echo "    1) Abrir http://localhost:$PORT"
echo "    2) Tocar 'Crear una' (link de signup)"
echo "    3) Registrarte con $SUPERADMIN_EMAIL"
echo "    4) Ya quedás como SUPERADMIN auto-verificado."
echo
echo "  Stop con Ctrl+C."
echo

flask --app api.app run --port "$PORT"
