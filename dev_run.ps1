# dev_run.ps1 - bootstrap + run de la app en Windows PowerShell
#
# Uso:
#   .\dev_run.ps1
#
# Si es la primera vez:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
#   .\dev_run.ps1
#
# Cambia el email de abajo al tuyo antes de correrlo por primera vez.
# Ese email queda como SUPERADMIN del deploy.

# ============================================================
# CONFIGURA ESTO
# ============================================================
$superadmin_email = "rodrigocorvalan93@gmail.com"
$port = 5000

# ============================================================
# Activar venv
# ============================================================
$venv_activate = Join-Path $PSScriptRoot "venv\Scripts\Activate.ps1"
if (Test-Path $venv_activate) {
    & $venv_activate
} else {
    Write-Host "WARN: no encontre venv en .\venv. Si todavia no lo creaste:" -ForegroundColor Yellow
    Write-Host "      python -m venv venv" -ForegroundColor Yellow
    Write-Host "      .\venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "      pip install -r requirements.txt" -ForegroundColor Yellow
}

# ============================================================
# Env vars - modo dev
# ============================================================
$env:WM_BOOTSTRAP_SUPERADMIN_EMAIL = $superadmin_email
$env:WM_AUTO_VERIFY_FIRST_SUPERADMIN = "1"
$env:WM_DISABLE_RATELIMIT = "1"
$env:WM_APP_URL = "http://localhost:$port"

# Si queres mandar mails reales (sino caen al outbox data/_outbox/*.eml):
# $env:WM_SMTP_HOST = "smtp.gmail.com"
# $env:WM_SMTP_PORT = "587"
# $env:WM_SMTP_USER = "tu_email@gmail.com"
# $env:WM_SMTP_PASS = "tu_app_password"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  WM Wealth Management - modo dev" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Superadmin email: $superadmin_email" -ForegroundColor Green
Write-Host "  URL: http://localhost:$port" -ForegroundColor Green
Write-Host ""
Write-Host "  Pasos:" -ForegroundColor Yellow
Write-Host "    1. Abrir http://localhost:$port" -ForegroundColor Yellow
Write-Host "    2. Tocar el link de signup - Crear una" -ForegroundColor Yellow
Write-Host "    3. Registrarte con $superadmin_email" -ForegroundColor Yellow
Write-Host "    4. Quedas como SUPERADMIN auto-verificado." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Stop con Ctrl-C." -ForegroundColor Gray
Write-Host ""

# ============================================================
# Run
# ============================================================
flask --app api.app run --port $port
