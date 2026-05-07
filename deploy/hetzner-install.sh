#!/usr/bin/env bash
# deploy/hetzner-install.sh — one-shot bootstrap de la app en un VPS Ubuntu.
#
# Pensado para Hetzner CX22 (Ubuntu 22.04/24.04) pero anda igual en
# DigitalOcean / Linode / Vultr / cualquier Ubuntu fresco.
#
# Lo que hace:
#   1) Instala Docker + docker compose v2
#   2) Clona el repo en /opt/wm
#   3) Genera env file con WM_ENCRYPTION_KEY random
#   4) Levanta la app + Caddy (HTTPS automático con Let's Encrypt)
#   5) Configura firewall (solo 22/80/443)
#   6) Setup systemd para auto-restart
#
# USO (ejecutá esto adentro del server, NO en tu PC):
#   curl -fsSL https://raw.githubusercontent.com/<USER>/wealth_management_rodricor/main/deploy/hetzner-install.sh \
#     | sudo bash -s -- DOMAIN EMAIL_SUPER
#
# Ejemplo:
#   sudo bash hetzner-install.sh wm.tudominio.com tu@email.com
#
# Si todavía no tenés dominio: pasale tu IP pública con http://; el setup
# va a saltar el HTTPS automático y vas a ver una warning del browser.
#
set -euo pipefail

DOMAIN="${1:-}"
SUPERADMIN_EMAIL="${2:-}"

if [ -z "$DOMAIN" ] || [ -z "$SUPERADMIN_EMAIL" ]; then
  echo "USO: sudo bash hetzner-install.sh DOMAIN SUPERADMIN_EMAIL"
  echo "Ej:  sudo bash hetzner-install.sh wm.tudominio.com tu@email.com"
  exit 1
fi

if [ "$EUID" -ne 0 ]; then
  echo "Este script necesita root. Corrélo con sudo."
  exit 1
fi

echo "==> Updating apt + instalando deps base"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg ufw git

echo "==> Instalando Docker (oficial)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "==> Configurando firewall (solo SSH/HTTP/HTTPS)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Clonando repo en /opt/wm"
mkdir -p /opt/wm
cd /opt/wm
if [ -d ".git" ]; then
  git pull
else
  # NOTA: editá esta URL al repo correcto si forkeaste
  git clone https://github.com/rodrigocorvalan93/wealth_management_rodricor.git .
fi

echo "==> Generando .env"
ENC_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
          || openssl rand -base64 32 | tr -d '\n=' | tr '+/' '-_' )

cat > /opt/wm/.env <<EOF
# Generado por hetzner-install.sh — NO commitear
WM_BOOTSTRAP_SUPERADMIN_EMAIL=$SUPERADMIN_EMAIL
WM_AUTO_VERIFY_FIRST_SUPERADMIN=1
WM_ENCRYPTION_KEY=$ENC_KEY
WM_BASE_DIR=/app
WM_USERS_FILE=/app/data/users.json
WM_ANCHOR=USD
WM_APP_URL=https://$DOMAIN
PORT=8000

# SMTP — completá esto cuando tengas Gmail app password listo
# WM_SMTP_HOST=smtp.gmail.com
# WM_SMTP_PORT=587
# WM_SMTP_USER=tu@gmail.com
# WM_SMTP_PASS=app_password
# WM_SMTP_FROM=Wealth Management <noreply@$DOMAIN>
EOF
chmod 600 /opt/wm/.env

echo "==> Escribiendo docker-compose.prod.yml"
cat > /opt/wm/docker-compose.prod.yml <<COMPOSE
services:
  app:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - wm_data:/app/data
      - wm_inputs:/app/inputs
    expose:
      - "8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - app

volumes:
  wm_data:
  wm_inputs:
  caddy_data:
  caddy_config:
COMPOSE

echo "==> Escribiendo Caddyfile (HTTPS automático con Let's Encrypt)"
cat > /opt/wm/Caddyfile <<CADDY
$DOMAIN {
    encode gzip
    reverse_proxy app:8000

    # Headers de seguridad básicos
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }
}
CADDY

echo "==> Instalando systemd unit para auto-start en reboot"
cat > /etc/systemd/system/wm.service <<UNIT
[Unit]
Description=WM Wealth Management
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/wm
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable wm.service

echo "==> Buildando imagen + levantando containers (puede tardar ~5 min)"
cd /opt/wm
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

echo
echo "============================================================"
echo "  ✓ Deploy completado"
echo "============================================================"
echo "  URL:                 https://$DOMAIN"
echo "  Superadmin email:    $SUPERADMIN_EMAIL"
echo "  Encryption key:      guardada en /opt/wm/.env (BACKUP-EALA!)"
echo
echo "  Siguiente:"
echo "    1) Apuntá tu DNS A record de '$DOMAIN' a la IP de este server"
echo "    2) Esperá ~30s a que Caddy emita el cert de Let's Encrypt"
echo "    3) Abrí https://$DOMAIN → 'Crear una' con $SUPERADMIN_EMAIL"
echo
echo "  Update con git push:"
echo "    cd /opt/wm && git pull && docker compose -f docker-compose.prod.yml up -d --build"
echo
echo "  Logs en vivo:"
echo "    cd /opt/wm && docker compose -f docker-compose.prod.yml logs -f"
echo "============================================================"
