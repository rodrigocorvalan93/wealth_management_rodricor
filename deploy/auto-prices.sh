#!/bin/bash
# auto-prices.sh — Refresca cotizaciones (FX, cripto, equities US) corriendo
# los loaders adentro del container de la app.
#
# Se llama desde cron cada 4h. No requiere auth porque corre como sub-proceso
# del propio container (que ya tiene el environment correcto).
#
# Loaders sin auth (siempre se corren):
#   - fx_loader.py            → CCL/MEP/A3500
#   - yfinance_fx_loader.py   → EUR, BRL, GBP, JPY, ...
#   - cripto_loader.py        → BTC, ETH, SOL, USDT, ...
#   - yfinance_loader.py      → AAPL, MSFT, ETFs, ... (toma tickers de
#                                 cualquier user que tenga EQUITY_US/ETF/REIT
#                                 en su `especies`)
#
# Loaders que requieren auth y se omiten desde cron (correlos manualmente):
#   - byma_loader.py          → necesita user/pass del OMS
#   - cafci_loader.py         → necesita Bearer token del superadmin
#                                (igual el botón de la app lo dispara con un click)
#
# Logs: /var/log/wm-prices.log (rotación implícita por size, ver final).

set -u

REPO_DIR="${REPO_DIR:-/opt/wm}"
COMPOSE_FILE="$REPO_DIR/docker-compose.prod.yml"
LOG_FILE="${LOG_FILE:-/var/log/wm-prices.log}"

cd "$REPO_DIR" || { echo "ERROR: $REPO_DIR no existe"; exit 1; }

ts() { date -Iseconds; }
log() { echo "[$(ts)] $*" >> "$LOG_FILE"; }

run_loader() {
  local label="$1"; shift
  log "→ $label"
  if docker compose -f "$COMPOSE_FILE" exec -T app python "$@" \
        >> "$LOG_FILE" 2>&1; then
    log "  ✓ $label OK"
  else
    log "  ✗ $label FAIL (ver log)"
  fi
}

log "==== auto-prices start ===="

# FX argentino (CCL/MEP/A3500)
run_loader "fx_loader (CCL/MEP/A3500)" /app/fx_loader.py

# FX foráneo (EUR, BRL, etc) vía Yahoo
run_loader "yfinance_fx (EUR/BRL/GBP/...)" /app/yfinance_fx_loader.py

# Cripto vía CoinGecko
run_loader "cripto (CoinGecko)" /app/cripto_loader.py

# Equities US — el script lee especies de los users automáticamente vía
# tickers_union.txt (generado por sync.py). Si no existe, usa los defaults
# del loader.
if [ -f "$REPO_DIR/data/tickers_union.txt" ]; then
  run_loader "yfinance equities US" /app/yfinance_loader.py \
    --tickers-file /app/data/tickers_union.txt
else
  run_loader "yfinance equities US (defaults)" /app/yfinance_loader.py
fi

log "==== auto-prices done ===="

# Rotación simple: si el log pasa de 5 MB, lo trunca a las últimas 1000 líneas.
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
