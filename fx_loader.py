# -*- coding: utf-8 -*-
"""
fx_loader.py

Loader de cotizaciones FX (MEP, CCL, mayorista) → CSV planilla v3.1.

FUENTES:
  - dolarapi.com         → cotización del día (todas las casas)
  - argentinadatos.com   → histórico (un solo endpoint, todas las casas)
  - bcra.gob.ar          → mayorista oficial A3500 (solo cross-check del último día)

CASAS REPORTADAS (mapeo a tu convención de moneda):
    bolsa            → USB           (MEP)
    contadoconliqui  → USD           (CCL — ancla offshore)
    mayorista        → USD_OFICIAL   (mayorista A3500)

PRECIO REPORTADO:
    Mid = (compra + venta) / 2

CROSS-CHECK BCRA (solo modo del día):
    Compara dolarapi.mayorista vs BCRA.tipoCotizacion para USD.
    Tolerancia: 10bps (0.10%). Si difiere más, imprime warning.

OUTPUT (formato planilla v3.1):
    fx_historico.csv  →  Fecha, Moneda, Rate, Cotiza vs, Fuente

Si el archivo destino existe, ANEXA filas nuevas (no pisa).
Si para una (Fecha, Moneda) ya hay fila, la actualiza con el nuevo dato.

USO:
    # del día (modo default)
    python fx_loader.py

    # últimos 30 días
    python fx_loader.py --dias 30

    # bootstrap desde fecha específica
    python fx_loader.py --desde 2024-01-01

    # solo print, no escribir CSV
    python fx_loader.py --dry-run

    # output a otra carpeta
    python fx_loader.py --output-dir ./mi_data

Sin auth. Sin secrets.txt requerido. Solo necesita acceso a internet.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests


# =============================================================================
# Config
# =============================================================================

DOLARAPI_URL = "https://dolarapi.com/v1/dolares"
ARGENTINADATOS_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares"
BCRA_URL = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/USD"

DEFAULT_TIMEOUT = 15  # segundos

# User-Agent: dolarapi bloquea clientes sin UA (devuelve 403)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# Tolerancia para cross-check de mayorista: 10bps = 0.10%
CROSSCHECK_TOLERANCE_BPS = 10

# Mapping casa → moneda planilla v3.1
CASA_TO_MONEDA: Dict[str, str] = {
    "bolsa":           "USB",
    "contadoconliqui": "USD",
    "mayorista":       "USD_OFICIAL",
}

# Casas que efectivamente reportamos (orden estable para output)
CASAS_TARGET: List[str] = ["bolsa", "contadoconliqui", "mayorista"]


# =============================================================================
# HTTP helpers
# =============================================================================

def _get_json(url: str, verify_ssl: bool = True) -> Any:
    """GET genérico que tira RuntimeError con detalle si falla."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT,
                         verify=verify_ssl)
    except requests.RequestException as e:
        raise RuntimeError(f"GET {url} falló: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"GET {url}: HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise RuntimeError(f"GET {url}: JSON inválido: {r.text[:200]}") from e


# =============================================================================
# Helpers numéricos
# =============================================================================

def mid(compra: Any, venta: Any) -> float:
    """Promedio de compra y venta. NaN si alguno falla."""
    try:
        c = float(compra)
        v = float(venta)
    except (TypeError, ValueError):
        return float("nan")
    return (c + v) / 2.0


def parse_iso_date(s: Any) -> Optional[str]:
    """Extrae la fecha YYYY-MM-DD de un timestamp ISO (con o sin tiempo/TZ).

    Ejemplos:
      '2026-05-02T11:56:00.000Z' → '2026-05-02'
      '2026-05-02'               → '2026-05-02'
      None / 'xx'                → None
    """
    if not isinstance(s, str) or not s:
        return None
    head = s[:10]
    try:
        date.fromisoformat(head)
        return head
    except ValueError:
        return None


# =============================================================================
# Snapshot
# =============================================================================

@dataclass
class FXRow:
    """Fila lista para grabar al CSV en formato planilla v3.1."""
    fecha: str          # YYYY-MM-DD
    moneda: str         # USB | USD | USD_OFICIAL
    rate: float         # mid en ARS
    cotiza_vs: str      # 'ARS' siempre
    fuente: str         # texto descriptivo

    @property
    def is_valid(self) -> bool:
        return (
            bool(self.fecha)
            and bool(self.moneda)
            and self.rate is not None
            and not math.isnan(self.rate)
            and self.rate > 0
        )


# =============================================================================
# Fuente 1: dolarapi (del día)
# =============================================================================

def fetch_dolarapi_today() -> List[FXRow]:
    """Devuelve filas FX del día actual desde dolarapi (1 request).

    Solo retorna las casas en CASAS_TARGET. Cada una con fuente 'dolarapi mid'.
    """
    data = _get_json(DOLARAPI_URL)
    if not isinstance(data, list):
        raise RuntimeError(f"dolarapi: response no es una lista: {str(data)[:200]}")

    by_casa: Dict[str, Dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("casa") in CASAS_TARGET:
            by_casa[item["casa"]] = item

    rows: List[FXRow] = []
    for casa in CASAS_TARGET:
        d = by_casa.get(casa)
        if d is None:
            print(f"[dolarapi] WARN: casa {casa!r} no presente en response",
                  file=sys.stderr)
            continue
        fecha = parse_iso_date(d.get("fechaActualizacion"))
        rate = mid(d.get("compra"), d.get("venta"))
        if fecha is None or math.isnan(rate) or rate <= 0:
            print(f"[dolarapi] WARN: casa {casa!r} con datos inválidos: "
                  f"fecha={d.get('fechaActualizacion')!r} compra={d.get('compra')} "
                  f"venta={d.get('venta')}", file=sys.stderr)
            continue
        rows.append(FXRow(
            fecha=fecha,
            moneda=CASA_TO_MONEDA[casa],
            rate=round(rate, 6),
            cotiza_vs="ARS",
            fuente="dolarapi mid",
        ))
    return rows


# =============================================================================
# Fuente 2: argentinadatos (histórico, todas las fechas en 1 request)
# =============================================================================

def fetch_argentinadatos_historico(
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
) -> List[FXRow]:
    """Devuelve filas FX históricas filtradas por rango.

    El endpoint trae TODA la historia. Filtramos en memoria por
    (desde, hasta) y por casas en CASAS_TARGET. Inclusive ambos extremos.
    """
    data = _get_json(ARGENTINADATOS_URL)
    if not isinstance(data, list):
        raise RuntimeError(
            f"argentinadatos: response no es una lista: {str(data)[:200]}"
        )

    rows: List[FXRow] = []
    n_filtered_casa = 0
    n_filtered_date = 0

    for item in data:
        if not isinstance(item, dict):
            continue
        casa = item.get("casa")
        if casa not in CASAS_TARGET:
            n_filtered_casa += 1
            continue
        fecha_str = parse_iso_date(item.get("fecha"))
        if fecha_str is None:
            continue
        fecha_obj = date.fromisoformat(fecha_str)
        if desde and fecha_obj < desde:
            n_filtered_date += 1
            continue
        if hasta and fecha_obj > hasta:
            n_filtered_date += 1
            continue
        rate = mid(item.get("compra"), item.get("venta"))
        if math.isnan(rate) or rate <= 0:
            continue
        rows.append(FXRow(
            fecha=fecha_str,
            moneda=CASA_TO_MONEDA[casa],
            rate=round(rate, 6),
            cotiza_vs="ARS",
            fuente="argentinadatos mid",
        ))

    print(f"[argentinadatos] {len(data)} filas raw → "
          f"{len(rows)} usables ({n_filtered_casa} fuera de target, "
          f"{n_filtered_date} fuera de rango)")
    return rows


# =============================================================================
# Fuente 3: BCRA (cross-check del mayorista, último día)
# =============================================================================

def fetch_bcra_mayorista() -> Optional[Tuple[str, float]]:
    """Devuelve (fecha, rate) del mayorista A3500 según BCRA.

    None si la API falla. Solo usado como cross-check.
    El cert SSL del BCRA es problemático → verify_ssl=False.
    """
    try:
        data = _get_json(BCRA_URL, verify_ssl=False)
    except RuntimeError as e:
        print(f"[bcra] WARN: no se pudo obtener cotización: {e}",
              file=sys.stderr)
        return None
    try:
        results = data.get("results", [])
        if not results:
            return None
        last = results[0]
        fecha = last.get("fecha")
        for det in last.get("detalle", []):
            if det.get("codigoMoneda") == "USD":
                rate = float(det.get("tipoCotizacion"))
                if rate > 0:
                    return (fecha, rate)
    except Exception as e:
        print(f"[bcra] WARN: parseando response: {e}", file=sys.stderr)
    return None


def crosscheck_mayorista(
    dolarapi_rows: List[FXRow],
    bcra_data: Optional[Tuple[str, float]],
) -> None:
    """Compara mayorista de dolarapi vs BCRA. Solo loggea, no modifica nada."""
    if bcra_data is None:
        print(f"[crosscheck] BCRA no disponible — saltando")
        return
    bcra_fecha, bcra_rate = bcra_data
    dolarapi_mayor = next(
        (r for r in dolarapi_rows if r.moneda == "USD_OFICIAL"), None
    )
    if dolarapi_mayor is None:
        print(f"[crosscheck] dolarapi sin mayorista — saltando")
        return
    diff_pct = abs(dolarapi_mayor.rate - bcra_rate) / bcra_rate * 100
    diff_bps = diff_pct * 100
    status = "OK" if diff_bps <= CROSSCHECK_TOLERANCE_BPS else "WARN"
    msg = (f"[crosscheck] mayorista: dolarapi={dolarapi_mayor.rate:,.4f} "
           f"({dolarapi_mayor.fecha}) vs BCRA={bcra_rate:,.4f} ({bcra_fecha}) "
           f"→ diff={diff_bps:.1f}bps  [{status}]")
    print(msg)
    if status == "WARN":
        print(f"[crosscheck] WARN: diferencia mayor a "
              f"{CROSSCHECK_TOLERANCE_BPS}bps. Verificá las fuentes.",
              file=sys.stderr)


# =============================================================================
# CSV upsert (idéntico al de byma_loader / cafci_loader)
# =============================================================================

def upsert_csv(
    path: Path,
    new_rows: List[Dict[str, Any]],
    key_cols: List[str],
    column_order: List[str],
) -> Tuple[int, int]:
    """Anexa filas y reemplaza las que matcheen por key_cols. Devuelve (n_new, n_updated)."""
    if not new_rows:
        return 0, 0

    df_new = pd.DataFrame(new_rows)

    if path.is_file():
        try:
            df_old = pd.read_csv(path)
        except Exception as e:
            print(f"[upsert] error leyendo {path}: {e}. Lo recreo.",
                  file=sys.stderr)
            df_old = pd.DataFrame(columns=column_order)
    else:
        df_old = pd.DataFrame(columns=column_order)

    if not df_old.empty and all(k in df_old.columns for k in key_cols):
        old_keys = df_old[key_cols].astype(str).agg("||".join, axis=1)
        new_keys = df_new[key_cols].astype(str).agg("||".join, axis=1)
        n_updated = int(old_keys.isin(new_keys).sum())
    else:
        n_updated = 0

    df_merged = pd.concat([df_old, df_new], ignore_index=True)
    df_merged = df_merged.drop_duplicates(subset=key_cols, keep="last")

    cols_existing = [c for c in column_order if c in df_merged.columns]
    cols_extra = [c for c in df_merged.columns if c not in column_order]
    df_merged = df_merged[cols_existing + cols_extra]

    sort_cols = [c for c in ("Fecha", "Moneda") if c in df_merged.columns]
    if sort_cols:
        df_merged = df_merged.sort_values(sort_cols, kind="stable")

    path.parent.mkdir(parents=True, exist_ok=True)
    df_merged.to_csv(path, index=False, encoding="utf-8")
    n_new = len(df_new) - n_updated
    return n_new, n_updated


# =============================================================================
# Run
# =============================================================================

def run(
    output_dir: Path,
    dias: Optional[int] = None,
    desde: Optional[date] = None,
    dry_run: bool = False,
) -> int:
    """Punto de entrada principal. Devuelve exit code (0 OK, 1 error)."""

    if dias is None and desde is None:
        modo = "today"
    else:
        modo = "historico"

    rows: List[FXRow] = []

    try:
        if modo == "today":
            print(f"[fx] modo: del día (dolarapi)")
            rows = fetch_dolarapi_today()
            print(f"[fx] dolarapi: {len(rows)} filas")
            bcra_data = fetch_bcra_mayorista()
            crosscheck_mayorista(rows, bcra_data)

        else:
            today = date.today()
            if desde is None and dias is not None:
                desde = today - timedelta(days=dias)
            print(f"[fx] modo: histórico desde {desde} hasta {today} "
                  f"(argentinadatos)")
            rows = fetch_argentinadatos_historico(desde=desde, hasta=today)

    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    # Mostrar resumen
    print()
    if rows:
        rows_sorted = sorted(rows, key=lambda r: (r.fecha, r.moneda))
        if len(rows_sorted) <= 20:
            shown = rows_sorted
        else:
            shown = rows_sorted[:5] + rows_sorted[-5:]
            print(f"  (mostrando primeras 5 y últimas 5 de {len(rows_sorted)} filas)")
        print(f"  {'FECHA':<12} {'MONEDA':<13} {'RATE':>14}  FUENTE")
        print(f"  {'-'*12} {'-'*13} {'-'*14}  {'-'*30}")
        for r in shown:
            print(f"  {r.fecha:<12} {r.moneda:<13} {r.rate:>14,.4f}  {r.fuente}")
        print()

    if not rows:
        print("[done] sin datos para grabar")
        return 0

    if dry_run:
        print(f"[dry-run] no se escribió CSV ({len(rows)} filas habrían sido grabadas)")
        return 0

    csv_rows = [
        {
            "Fecha": r.fecha,
            "Moneda": r.moneda,
            "Rate": r.rate,
            "Cotiza vs": r.cotiza_vs,
            "Fuente": r.fuente,
        }
        for r in rows
    ]

    n_new, n_upd = upsert_csv(
        path=output_dir / "fx_historico.csv",
        new_rows=csv_rows,
        key_cols=["Fecha", "Moneda"],
        column_order=["Fecha", "Moneda", "Rate", "Cotiza vs", "Fuente"],
    )
    print(f"[csv] {len(csv_rows)} filas → {n_new} nuevos, {n_upd} actualizados")
    print(f"[done]")
    return 0


# =============================================================================
# CLI
# =============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Loader FX (MEP, CCL, mayorista) → CSV planilla v3.1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--dias", type=int, default=None,
        help="Histórico: últimos N días (ej --dias 30)",
    )
    p.add_argument(
        "--desde", type=str, default=None,
        help="Histórico: fecha inicio YYYY-MM-DD (alternativa a --dias)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=Path.cwd() / "data",
        help="Carpeta destino del CSV (default: ./data)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="No escribir CSV, solo mostrar resultado",
    )

    args = p.parse_args(argv)

    if args.dias is not None and args.desde is not None:
        print("[error] --dias y --desde son mutuamente excluyentes",
              file=sys.stderr)
        return 1

    if args.dias is not None and args.dias < 1:
        print(f"[error] --dias debe ser ≥ 1 (recibí {args.dias})",
              file=sys.stderr)
        return 1

    desde: Optional[date] = None
    if args.desde:
        try:
            desde = date.fromisoformat(args.desde)
        except ValueError:
            print(f"[error] fecha inválida: {args.desde} (esperado YYYY-MM-DD)",
                  file=sys.stderr)
            return 1

    return run(
        output_dir=args.output_dir,
        dias=args.dias,
        desde=desde,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
