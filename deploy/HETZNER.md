# Deploy a Hetzner Cloud — el mejor balance precio/potencia

**Hetzner CX22**: €4.51/mes (~$5 USD) por:
- 2 vCPU dedicadas
- 4 GB RAM
- 40 GB SSD NVMe
- 20 TB de transferencia/mes
- IPv6 incluido
- Region: Helsinki / Falkenstein (DE) / Ashburn (US East)

Por el mismo precio Render te da 0.5 CPU + 512 MB RAM + 1 GB. Hetzner
te da 4× CPU, 8× RAM, 40× disco. La trampa: tenés que administrar el
server vos (no es tan terrible — el script automatiza casi todo).

---

## 1. Crear la VPS

1. Sign up en https://www.hetzner.com/cloud (gratis hasta que prendas
   máquinas).
2. **Console → New Project → New Server**.
3. Configuración:
   - **Location**: **Ashburn, VA** (mejor latencia para Argentina/LATAM)
     o Falkenstein si querés EU.
   - **Image**: **Ubuntu 24.04**
   - **Type**: **CX22** (€4.51/mes) — 2 vCPU, 4 GB RAM
   - **SSH Key**: pegá tu llave pública (`~/.ssh/id_ed25519.pub`).
     Si no tenés generala con `ssh-keygen -t ed25519`.
   - **Firewalls**: opcional (el script de install configura ufw).
   - **Name**: `wm-prod` o lo que quieras.
4. Click **Create & Buy now**. Tarda ~30s en estar lista.
5. Anotá la **IPv4** del server.

## 2. Apuntar tu DNS

Si ya tenés dominio:
```
A record   wm.tudominio.com   →   <IPv4>
```

Si no: comprá uno barato en https://www.namecheap.com (~$10/año), o
usá un subdominio gratis de https://duckdns.org.

Esperá 1-5 minutos para que propague (chequealo con
`nslookup wm.tudominio.com` o https://dnschecker.org).

## 3. SSH + correr el installer

```bash
ssh root@<IPv4>
```

Adentro del server, una sola línea:

```bash
curl -fsSL https://raw.githubusercontent.com/rodrigocorvalan93/wealth_management_rodricor/main/deploy/hetzner-install.sh \
  | sudo bash -s -- wm.tudominio.com tu@email.com
```

Reemplazá `wm.tudominio.com` y `tu@email.com` por los tuyos.

El script tarda ~5 min:
- Instala Docker + docker-compose
- Configura firewall (solo SSH/HTTP/HTTPS)
- Clona el repo en `/opt/wm`
- Genera `.env` con `WM_ENCRYPTION_KEY` random (la guarda en disco; **backupeala**)
- Levanta la app + Caddy reverse proxy con HTTPS automático (Let's Encrypt)
- Crea systemd unit para auto-start después de reboots

## 4. Primer signup

Cuando el script termine, abrí `https://wm.tudominio.com` → **"Crear una"** →
registrate con el email del bootstrap → quedás superadmin auto-verificado.

**Inmediatamente después**:
1. Login OK con tu email.
2. SSH al server: `ssh root@<IP>`
3. Edit `/opt/wm/.env` y poné `WM_AUTO_VERIFY_FIRST_SUPERADMIN=0`.
4. Restart: `cd /opt/wm && docker compose -f docker-compose.prod.yml restart`.

## 5. SMTP (opcional, recomendado)

Para que verify/reset emails se manden de verdad (no al outbox):

```bash
ssh root@<IP>
nano /opt/wm/.env
# Descomentá las líneas WM_SMTP_* y poné tus valores Gmail
docker compose -f /opt/wm/docker-compose.prod.yml restart
```

## 6. Updates con git push

```bash
ssh root@<IP>
cd /opt/wm
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Con Caddy delante, downtime real ≈ 1-2 segundos. Si querés zero downtime,
investigá blue-green con docker compose (más complicado, no es necesario
para tu caso).

## 7. Backups

Hetzner te ofrece **backups automáticos** del server por +20% del costo
(€0.90/mes para CX22 = total €5.41/mes). Recomendado si tu data importa.

Activación: en la consola de Hetzner → tu server → **Backups** → Enable.

Alternativa gratis: corré el script del bottom de `ORACLE.md` que sube a
Google Drive (15 GB free).

## 8. Logs

```bash
cd /opt/wm
docker compose -f docker-compose.prod.yml logs -f       # todos
docker compose -f docker-compose.prod.yml logs -f app   # solo Flask
docker compose -f docker-compose.prod.yml logs -f caddy # solo proxy/HTTPS
```

## 9. Monitoreo

Free: https://uptimerobot.com con check cada 5 min y email alerts.

## Costos finales

| Item | Costo/mes |
|---|---|
| Hetzner CX22 | $5 |
| Backups automáticos (opcional) | $1 |
| Dominio (anualizado) | $0.83 |
| **Total con backups** | **~$7** |
| **Total sin backups** | **~$6** |

Por casi el mismo precio de Render Starter pero con **8× más RAM y CPU**.
Cuando tu carga crezca, el upgrade en Hetzner es un click (CX32 a €7/mes
= 4 vCPU + 8 GB RAM).
