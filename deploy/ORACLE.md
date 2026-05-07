# Deploy a Oracle Always Free — gratis 24/7 con specs locos

Oracle Cloud te regala 4 vCPU ARM + 24 GB RAM + 200 GB de disco
**gratis para siempre**. Es por lejos la oferta más generosa del
mercado, pero tiene un par de gotchas:

- ARM (`aarch64`), no x86. Tu Dockerfile ya soporta ambas, así que OK.
- Oracle ha terminado cuentas inactivas o "sospechosas" sin aviso.
  Hacé backups del disco periódicos.
- A veces el "Always Free" tier no tiene capacidad disponible en tu
  region. Hay que probar varias veces.
- El setup tiene 2-3 pasos confusos en la UI de Oracle (no es como
  Render).

Si tolerás esto, es la mejor opción **gratis**. Si no, andá a Hetzner
($5/mes) o Fly.io free.

---

## 1. Crear cuenta + instancia

1. Sign up en https://cloud.oracle.com (necesita tarjeta para verificar
   identidad — NO te cobra; los recursos free están claramente marcados).
2. Una vez adentro: **Compute → Instances → Create Instance**.
3. Configuración crítica:
   - **Image**: Canonical Ubuntu 22.04
   - **Shape**: cambiá de "VM.Standard.E2.1.Micro" a **VM.Standard.A1.Flex**
     - Esto es la ARM gratis con specs grandes
     - OCPUs: 4
     - Memory: 24 GB
   - **Networking**: dejá la VCN default
   - **SSH keys**: pegá tu llave pública (`~/.ssh/id_ed25519.pub`).
     Si no tenés, generá una con `ssh-keygen -t ed25519` en tu PC.
4. Click **Create**. Si te tira "Out of host capacity" probá:
   - Otra availability domain (AD-1, AD-2, AD-3)
   - Otra región (US East, EU Frankfurt suelen tener stock)
   - Volvé a intentar en 30 minutos

5. Cuando esté **Running**, anotá la **Public IP**.

## 2. Abrir puertos en Oracle Cloud (importante!)

Oracle por default bloquea **TODO** menos SSH. Tenés que abrir 80/443
manualmente:

1. **Networking → Virtual Cloud Networks** → tu VCN → **Security Lists** →
   "Default Security List for ..."
2. **Add Ingress Rules**:

   | Source | IP Protocol | Source Port | Destination Port |
   |---|---|---|---|
   | 0.0.0.0/0 | TCP | All | 80 |
   | 0.0.0.0/0 | TCP | All | 443 |

3. Save.

(También hay un firewall iptables dentro de Ubuntu que vamos a abrir
en el script.)

## 3. SSH al server

```bash
ssh ubuntu@<TU_IP_PUBLICA>
```

## 4. Apuntar tu DNS

Si tenés dominio (Namecheap, Cloudflare, etc):

```
A record   wm.tudominio.com   →   <TU_IP_PUBLICA>
```

Esperá ~5 min para que propague.

Si no tenés dominio, podés usar `<TU_IP>.nip.io` que resuelve a tu IP
automáticamente. Ej: `1.2.3.4.nip.io` apunta a `1.2.3.4`. **Pero
Let's Encrypt no firma certs para nip.io**, así que vas a tener que
acceder por HTTP (browser warning) o conseguir un dominio.

Lo más barato: comprá un dominio en Namecheap (~$10/año) o usá un
subdominio gratis de https://duckdns.org.

## 5. Correr el installer

Adentro del server (vía SSH):

```bash
curl -fsSL https://raw.githubusercontent.com/rodrigocorvalan93/wealth_management_rodricor/main/deploy/hetzner-install.sh \
  | sudo bash -s -- wm.tudominio.com tu@email.com
```

(El script de Hetzner sirve igual para Oracle — son ambos Ubuntu.)

Tarda ~5 min:
- Instala Docker
- Clona el repo
- Levanta la app + Caddy con HTTPS automático
- Configura systemd para auto-start en reboot
- Configura ufw (firewall)

Cuando termina te dice la URL. Abrí `https://wm.tudominio.com` →
"Crear una" → registrate con tu email → ya sos superadmin.

## 6. Backups (importante con Oracle)

Como Oracle puede terminar tu cuenta sin aviso, configurá backups
**fuera de Oracle**:

```bash
# En el server: instalá rclone (sync a Google Drive / Dropbox / S3)
curl https://rclone.org/install.sh | sudo bash
rclone config         # seguir el wizard, conectar Google Drive

# Cron diario que sincroniza /opt/wm a Drive
sudo tee /etc/cron.daily/wm-backup <<'EOF'
#!/bin/bash
docker compose -f /opt/wm/docker-compose.prod.yml exec -T app \
  tar czf - /app/data /app/inputs > /tmp/wm-backup.tar.gz
rclone copy /tmp/wm-backup.tar.gz mygdrive:wm-backups/
rm /tmp/wm-backup.tar.gz
EOF
sudo chmod +x /etc/cron.daily/wm-backup
```

## 7. Update sin downtime

```bash
ssh ubuntu@<IP>
cd /opt/wm
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Caddy mantiene la conexión y switchea al container nuevo cuando esté
listo. Downtime real ≈ 1-2 segundos.

## 8. Monitoreo simple

Free: registrá tu URL en https://uptimerobot.com (50 monitors free
con check cada 5 min). Te avisa por email si la app se cae.

## Costo total real

- Server: **$0/mes** (Always Free)
- Dominio (opcional): ~$10/año = $0.83/mes
- Backup a Google Drive: gratis (15 GB free)
- **Total: $0-1/mes**

Para una app que en otros lugares te costaría $7-15/mes, esto es
imbatible. La única letra chica es la duda de "¿Oracle me la termina
de la nada?". Mucha gente lleva 2-3 años sin drama.
