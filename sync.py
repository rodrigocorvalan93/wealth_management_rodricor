# -*- coding: utf-8 -*-
"""
sync.py — un solo comando hace el ciclo completo:

  1. Corre todos los loaders de precios (FX, BYMA, CAFCI, cripto, yfinance)
  2. Sube los CSVs generados al server
  3. Sube el Excel master al server
  4. Trigger refresh del engine

USO:
    python sync.py                     # cycle completo (loaders + uploads)
    python sync.py --skip-loaders      # solo subir lo que ya hay en data/
    python sync.py --skip-excel        # no subir el Excel master
    python sync.py --only-excel        # solo subir Excel + refresh
    python sync.py --only-prices       # solo subir CSVs de precios
    python sync.py --download          # bajar reporte HTML al final

CONFIG:
    Lee WM_API_TOKEN y WM_API_URL desde:
      1. Variables de entorno
      2. secrets.txt (formato KEY=value, una por línea)

    Defaults:
      WM_API_URL = https://rodricor.pythonanywhere.com
      WM_API_TOKEN = (debe estar en env o secrets.txt)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("[sync] requests no instalado — pip install requests")
    sys.exit(1)


HERE = Path(__file__).resolve().parent

PRICE_CSVS = [
    HERE / "data" / "fx_historico.csv",
    HERE / "data" / "precios_historico.csv",
    HERE / "data" / "precios_cafci.csv",
    HERE / "data" / "precios_cripto.csv",
    HERE / "data" / "precios_us.csv",
]

DEFAULT_API_URL = "https://rodricor.pythonanywhere.com"


def find_user_xlsxs() -> list[tuple[str, Path]]:
    """Devuelve lista de (user_id, xlsx_path) de todos los users en disk.

    Layout multi-tenant: inputs/<user_id>/wealth_management.xlsx
    Back-compat: inputs/wealth_management_rodricor.xlsx → user 'default'
    """
    out = []
    inputs = HERE / "inputs"
    if not inputs.is_dir():
        return []
    # Multi-tenant
    for d in sorted(inputs.iterdir()):
        if not d.is_dir():
            continue
        for fname in ("wealth_management.xlsx", "wealth_management_rodricor.xlsx"):
            f = d / fname
            if f.is_file():
                out.append((d.name, f))
                break
    # Back-compat: legacy single-master
    if not out:
        for fname in ("wealth_management.xlsx", "wealth_management_rodricor.xlsx"):
            f = inputs / fname
            if f.is_file():
                out.append(("default", f))
                break
    return out


def regenerate_tickers_union():
    """Corre tickers_union.py para generar data/tickers_union.txt."""
    step("Generating data/tickers_union.txt (union de tickers de todos los users)")
    try:
        r = subprocess.run(["python", "tickers_union.py"],
                           cwd=HERE, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            print(f"   ✓ {r.stdout.strip().splitlines()[-1] if r.stdout else 'ok'}")
            return True
        print(f"   ✗ Falló: {r.stderr[-300:] if r.stderr else 'sin output'}")
        return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False


def get_loaders():
    """Devuelve los comandos de loaders, usando tickers_union.txt si existe."""
    union_file = HERE / "data" / "tickers_union.txt"
    legacy_tickers_file = HERE / "mis_tickers.txt"
    # Priorizar tickers_union.txt; back-compat con mis_tickers.txt
    if union_file.is_file():
        byma_tickers = ["--tickers-file", str(union_file)]
    elif legacy_tickers_file.is_file():
        byma_tickers = ["--tickers-file", str(legacy_tickers_file)]
    else:
        byma_tickers = []
    return [
        ("fx",       ["python", "fx_loader.py"]),
        ("byma",     ["python", "byma_loader.py", *byma_tickers]),
        ("cafci",    ["python", "cafci_loader.py"]),
        ("cripto",   ["python", "cripto_loader.py"]),
        ("yfinance", ["python", "yfinance_loader.py"]),
    ]


def load_secrets():
    """Lee secrets.txt si existe (KEY=value por línea)."""
    f = HERE / "secrets.txt"
    out = {}
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def get_config():
    secrets = load_secrets()
    token = (os.environ.get("WM_API_TOKEN")
             or secrets.get("WM_API_TOKEN", ""))
    url = (os.environ.get("WM_API_URL")
           or secrets.get("WM_API_URL", DEFAULT_API_URL)).rstrip("/")
    if not token or token.startswith("poné_"):
        print("[sync] ❌ WM_API_TOKEN no configurado.")
        print("       Setealo en secrets.txt o como variable de entorno:")
        print("       Windows: $env:WM_API_TOKEN = 'tu_token'")
        print("       Linux:   export WM_API_TOKEN='tu_token'")
        sys.exit(1)
    return {"token": token, "url": url}


def banner(text, char="="):
    print()
    print(char * 70)
    print(f"  {text}")
    print(char * 70)


def step(text):
    print(f"\n→ {text}")


def run_loaders():
    """Corre los loaders. Continúa aunque alguno falle individualmente."""
    banner("PASO 1/4 — Loaders de precios (con tickers_union)")

    # Primero generar tickers_union.txt para que byma fetchee union de todos
    regenerate_tickers_union()

    results = {}
    for name, cmd in get_loaders():
        step(f"Running {name}: {' '.join(cmd)}")
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=180)
            dt = time.time() - t0
            if r.returncode == 0:
                print(f"   ✓ {name} OK ({dt:.1f}s)")
                results[name] = "ok"
            else:
                print(f"   ✗ {name} FALLÓ (exit {r.returncode}, {dt:.1f}s)")
                if r.stderr:
                    print(f"   stderr: {r.stderr[-500:]}")
                results[name] = "fail"
        except subprocess.TimeoutExpired:
            print(f"   ⏱ {name} timeout (>3 min) — skipping")
            results[name] = "timeout"
        except FileNotFoundError as e:
            print(f"   ✗ {name} no encontrado: {e}")
            results[name] = "missing"
    return results


def upload_csvs(cfg):
    banner("PASO 2/4 — Subir CSVs de precios al server")
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    url = f"{cfg['url']}/api/upload/prices"
    n_ok = 0
    for csv in PRICE_CSVS:
        if not csv.is_file():
            print(f"   ⊘ {csv.name} no existe (skip)")
            continue
        size = csv.stat().st_size
        step(f"Subiendo {csv.name} ({size:,} bytes)")
        try:
            with open(csv, "rb") as f:
                r = requests.post(url, headers=headers,
                                  files={"file": (csv.name, f, "text/csv")},
                                  timeout=60)
            if r.status_code == 200:
                stats = r.json().get("import_stats", {})
                fx = stats.get("fx_rates", "?")
                pr = stats.get("prices", "?")
                print(f"   ✓ OK · fx={fx} · prices={pr}")
                n_ok += 1
            else:
                print(f"   ✗ HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"   ✗ Error de red: {e}")
    print(f"\n   {n_ok}/{len(PRICE_CSVS)} CSVs subidos")
    return n_ok


def upload_excel(cfg, xlsx_path):
    banner("PASO 3/4 — Subir Excel master al server")
    if not xlsx_path.is_file():
        print(f"   ✗ No existe {xlsx_path}")
        return False
    size = xlsx_path.stat().st_size
    step(f"Subiendo {xlsx_path.name} ({size:,} bytes)")
    try:
        with open(xlsx_path, "rb") as f:
            r = requests.post(
                f"{cfg['url']}/api/upload/excel",
                headers={"Authorization": f"Bearer {cfg['token']}"},
                files={"file": (xlsx_path.name, f,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                timeout=120,
            )
        if r.status_code == 200:
            data = r.json()
            stats = data.get("import_stats", {})
            print(f"   ✓ OK ({data.get('size_bytes', '?'):,} bytes)")
            print(f"   import_stats:")
            for k, v in sorted(stats.items()):
                print(f"     {k:<28} {v}")
            return True
        else:
            print(f"   ✗ HTTP {r.status_code}: {r.text[:300]}")
            return False
    except requests.RequestException as e:
        print(f"   ✗ Error de red: {e}")
        return False


def trigger_refresh(cfg):
    banner("PASO 4/4 — Refresh DB final")
    try:
        r = requests.post(
            f"{cfg['url']}/api/refresh",
            headers={"Authorization": f"Bearer {cfg['token']}"},
            timeout=60,
        )
        if r.status_code == 200:
            print(f"   ✓ Refresh OK")
            return True
        else:
            print(f"   ✗ HTTP {r.status_code}: {r.text[:300]}")
            return False
    except requests.RequestException as e:
        print(f"   ✗ Error de red: {e}")
        return False


def download_report(cfg, fecha=None):
    """Baja el reporte HTML del server y lo abre en el browser."""
    banner("Descargando reporte HTML")
    params = {"anchor": "USD"}
    if fecha:
        params["fecha"] = fecha
    try:
        r = requests.get(
            f"{cfg['url']}/api/report/html",
            headers={"Authorization": f"Bearer {cfg['token']}"},
            params=params,
            timeout=60,
        )
        if r.status_code != 200:
            print(f"   ✗ HTTP {r.status_code}: {r.text[:200]}")
            return None
        out = HERE / "portfolio.html"
        out.write_bytes(r.content)
        print(f"   ✓ Guardado en {out}")
        # Intentar abrir en browser
        try:
            import webbrowser
            webbrowser.open(out.as_uri())
        except Exception:
            pass
        return out
    except requests.RequestException as e:
        print(f"   ✗ Error de red: {e}")
        return None


def health_check(cfg):
    """Pequeño health check antes de empezar."""
    try:
        r = requests.get(f"{cfg['url']}/api/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            print(f"[sync] Server OK · auth={data.get('auth_configured')} "
                  f"· xlsx={data.get('xlsx_present')} · db={data.get('db_present')}")
            return True
        print(f"[sync] Server respondió HTTP {r.status_code}")
        return False
    except requests.RequestException as e:
        print(f"[sync] No se pudo contactar el server: {e}")
        return False


def main():
    p = argparse.ArgumentParser(description="Sync local → PythonAnywhere")
    p.add_argument("--skip-loaders", action="store_true",
                   help="No correr loaders (usar CSVs ya generados)")
    p.add_argument("--skip-excel", action="store_true",
                   help="No subir el Excel master")
    p.add_argument("--only-excel", action="store_true",
                   help="Solo subir Excel master + refresh")
    p.add_argument("--only-prices", action="store_true",
                   help="Solo subir CSVs de precios")
    p.add_argument("--download", action="store_true",
                   help="Bajar reporte HTML al final y abrirlo")
    p.add_argument("--fecha", type=str, default=None,
                   help="Fecha del reporte (default: hoy)")
    p.add_argument("--xlsx", type=Path, default=None,
                   help="Path al Excel master (default: auto-detecta de "
                        "inputs/<user>/wealth_management.xlsx para el user "
                        "del token, o legacy inputs/wealth_management_rodricor.xlsx)")
    args = p.parse_args()

    # Auto-detect xlsx path si no se pasó
    if args.xlsx is None:
        candidates = []
        # Multi-tenant: inputs/<user>/wealth_management.xlsx
        for u_dir in (HERE / "inputs").iterdir() if (HERE / "inputs").is_dir() else []:
            if u_dir.is_dir():
                for fname in ("wealth_management.xlsx",
                              "wealth_management_rodricor.xlsx"):
                    f = u_dir / fname
                    if f.is_file():
                        candidates.append(f)
                        break
        # Legacy
        for fname in ("wealth_management.xlsx",
                       "wealth_management_rodricor.xlsx"):
            f = HERE / "inputs" / fname
            if f.is_file():
                candidates.append(f)
                break
        if candidates:
            args.xlsx = candidates[0]
            print(f"[sync] Auto-detect xlsx: {args.xlsx}")
        else:
            args.xlsx = HERE / "inputs" / "wealth_management.xlsx"

    cfg = get_config()
    print(f"[sync] Server: {cfg['url']}")
    if not health_check(cfg):
        print("[sync] Verificá la conexión y el token.")
        sys.exit(1)

    if args.only_excel:
        ok = upload_excel(cfg, args.xlsx)
        if ok and args.download:
            download_report(cfg, args.fecha)
        sys.exit(0 if ok else 1)

    if args.only_prices:
        n = upload_csvs(cfg)
        if n > 0 and args.download:
            download_report(cfg, args.fecha)
        sys.exit(0 if n > 0 else 1)

    # Cycle completo
    if not args.skip_loaders:
        run_loaders()

    upload_csvs(cfg)

    if not args.skip_excel:
        upload_excel(cfg, args.xlsx)

    trigger_refresh(cfg)

    if args.download:
        download_report(cfg, args.fecha)

    banner("✓ SYNC COMPLETO", "=")


if __name__ == "__main__":
    main()
