# -*- coding: utf-8 -*-
"""
byma_loader.py

Loader diario de precios de cierre BYMA — saca una "foto" de los precios del
momento para una lista de tickers, y la guarda en formato planilla v3.1.

CONVENCIÓN DE VALUACIÓN:
  - Plazo 24hs (T+1, estándar BYMA) para todos los tickers
  - Prioridad de precio (foto del momento): LA → CL → ACP
      LA  = Last (última operación cruzada — el más "fresco")
      CL  = Close (cierre formal de la rueda)
      ACP = Auction Close Price (subasta de cierre, si hubo)
  - Cripto NO se incluye acá (otro loader)
  - FX MEP/CCL NO se incluye acá (otro loader)

OUTPUT (formato compatible con planilla v3.1):
  precios_historico.csv  →  Fecha, Ticker, Precio, Moneda, Fuente

Si el archivo destino ya existe, ANEXA filas nuevas (no pisa).
Si para una (Fecha, Ticker) ya hay una fila cargada, la actualiza con el
nuevo dato (último gana — útil si corrés el loader varias veces el mismo día).

USO:
    # un ticker:
    python byma_loader.py --tickers AL30D

    # varios:
    python byma_loader.py --tickers AL30D GD30C TX26 TXMJ9

    # desde archivo (un ticker por línea):
    python byma_loader.py --tickers-file mis_tickers.txt

    # output a otra carpeta:
    python byma_loader.py --tickers AL30D --output-dir ./mi_data

    # solo print, no escribir CSV:
    python byma_loader.py --tickers AL30D --dry-run

REQUIERE en secrets.txt o env vars:
    OMS_USER=...
    OMS_PASS=...

OPCIONAL en secrets.txt:
    BYMA_API_URL=https://api.cocos.xoms.com.ar/    # default: latinsecurities
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests


# =============================================================================
# Secrets (cargado al importar)
# =============================================================================

def load_secrets() -> int:
    """Carga secrets.txt (formato KEY=VALUE) a os.environ.

    Busca en este orden:
      1. ./secrets.txt (cwd)
      2. <script_dir>/secrets.txt
    No sobrescribe vars que ya estén en el environment.
    """
    candidates = [
        Path.cwd() / "secrets.txt",
        Path(__file__).parent / "secrets.txt",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            n_loaded = 0
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
                        n_loaded += 1
            print(f"[secrets] cargado {path.name} ({n_loaded} vars nuevas)")
            return n_loaded
        except Exception as e:
            print(f"[secrets] error leyendo {path}: {e}", file=sys.stderr)
    return 0


load_secrets()


# =============================================================================
# Config
# =============================================================================

# URL base. Default al server histórico (latinsecurities). Se puede sobrescribir
# vía env var BYMA_API_URL o vía secrets.txt.
_BASE_URL_DEFAULT = "https://api.latinsecurities.matrizoms.com.ar/"
BASE_URL = os.environ.get("BYMA_API_URL", _BASE_URL_DEFAULT)
if not BASE_URL.endswith("/"):
    BASE_URL = BASE_URL + "/"

DEFAULT_ENTRIES = "LA,CL,ACP,TV,NV"  # liviano: precios + volumen
DEFAULT_DEPTH = 1
MAX_WORKERS = 9
PLAZO = "24hs"  # convención fija — todo se valúa en T+1


# =============================================================================
# Sesión OMS
# =============================================================================

def login_oms(username: str, password: str) -> requests.Session:
    """Login en el OMS."""
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20, pool_maxsize=20, max_retries=2,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    r = s.post(
        f"{BASE_URL}j_spring_security_check",
        data={"j_username": username, "j_password": password},
        timeout=10,
    )
    r.raise_for_status()
    print(f"[oms] sesión iniciada como {username}  (BASE_URL={BASE_URL})")
    return s


# =============================================================================
# Marketdata fetch
# =============================================================================

def fetch_one(
    session: requests.Session,
    symbol: str,
    market_id: str = "ROFX",
    entries: str = DEFAULT_ENTRIES,
    depth: int = DEFAULT_DEPTH,
) -> Optional[Dict[str, Any]]:
    """Pide marketdata para 1 símbolo. None si error."""
    sym_enc = requests.utils.quote(symbol)
    url = (f"{BASE_URL}rest/marketdata/get?marketId={market_id}"
           f"&symbol={sym_enc}&entries={entries}&depth={depth}")
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") == "ERROR":
            return None
        md = data.get("marketData", {}) or {}
        md["symbol"] = symbol
        return md
    except Exception as e:
        print(f"[fetch] {symbol}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def fetch_bulk(
    session: requests.Session,
    symbols: List[str],
    market_id: str = "ROFX",
) -> Dict[str, Dict[str, Any]]:
    """Fetch paralelo. Devuelve {symbol: marketdata_dict} para los OK."""
    if not symbols:
        return {}
    results: Dict[str, Dict[str, Any]] = {}
    workers = min(MAX_WORKERS, len(symbols))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_one, session, s, market_id): s
            for s in symbols
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                data = fut.result()
                if data:
                    results[sym] = data
            except Exception as e:
                print(f"[fetch_bulk] {sym}: {e}", file=sys.stderr)
    return results


# =============================================================================
# Extracción de precio + volumen
# =============================================================================

def _first(x: Any) -> Any:
    if isinstance(x, list):
        return x[0] if x else None
    return x


def _extract_price(entry: Any) -> float:
    """Extrae 'price' de un entry LA/CL/ACP. NaN si no se puede."""
    e = _first(entry)
    if isinstance(e, dict):
        for k in ("price", "px", "value"):
            if k in e and e[k] is not None:
                try:
                    return float(e[k])
                except (TypeError, ValueError):
                    continue
    if isinstance(e, (int, float)) and not isinstance(e, bool):
        return float(e)
    return float("nan")


def _extract_size(entry: Any) -> float:
    """Extrae 'size'/'volume' de un entry."""
    e = _first(entry)
    if isinstance(e, dict):
        for k in ("size", "qty", "quantity", "volume", "nominal", "amount"):
            if k in e and e[k] is not None:
                try:
                    return float(e[k])
                except (TypeError, ValueError):
                    continue
    if isinstance(e, (int, float)) and not isinstance(e, bool):
        return float(e)
    return float("nan")


@dataclass
class TickerSnapshot:
    """Foto de un ticker para una fecha."""
    ticker: str
    fecha: date
    precio: float          # LA → CL → ACP fallback
    fuente: str            # 'LA' | 'CL' | 'ACP' | ''
    volumen: float         # TV → NV fallback (NaN si no hay)

    @property
    def is_valid(self) -> bool:
        return np.isfinite(self.precio) and self.precio > 0


def parse_snapshot(
    md: Dict[str, Any],
    ticker: str,
    fecha: date,
) -> TickerSnapshot:
    """Aplica fallback LA → CL → ACP y extrae volumen TV → NV.

    Foto del momento: priorizamos LA (último trade) por encima de CL (cierre
    formal) y ACP (subasta de cierre), porque LA es el dato más fresco que
    refleja "el precio ahora mismo". Útil tanto en mercado abierto como
    post-cierre.
    """
    la_price = _extract_price(md.get("LA"))
    cl_price = _extract_price(md.get("CL"))
    acp_price = _extract_price(md.get("ACP"))

    if np.isfinite(la_price) and la_price > 0:
        precio, fuente = la_price, "LA"
    elif np.isfinite(cl_price) and cl_price > 0:
        precio, fuente = cl_price, "CL"
    elif np.isfinite(acp_price) and acp_price > 0:
        precio, fuente = acp_price, "ACP"
    else:
        precio, fuente = float("nan"), ""

    tv = _extract_size(md.get("TV"))
    nv = _extract_size(md.get("NV"))
    if np.isfinite(tv) and tv > 0:
        volumen = tv
    elif np.isfinite(nv) and nv > 0:
        volumen = nv
    else:
        volumen = float("nan")

    return TickerSnapshot(
        ticker=ticker, fecha=fecha,
        precio=precio, fuente=fuente, volumen=volumen,
    )


# =============================================================================
# Símbolos BYMA
# =============================================================================

def _to_byma_ticker(ticker: str) -> str:
    """Strippea sufijos de mercado del ticker para llamar a BYMA.

    Convención: en `especies` los CEDEARs (mercado AR) llevan sufijo _AR
    para diferenciarlos del ticker US (que va con _US a yfinance). BYMA's
    API usa el ticker base sin sufijo (AAPL, no AAPL_AR).

    Ejemplos:
        AAPL_AR  → AAPL
        AL30D    → AL30D    (bono, no tiene sufijo)
        GGAL.BA  → GGAL     (formato Yahoo, BYMA usa GGAL)
        GGAL     → GGAL
    """
    t = ticker.upper()
    if t.endswith("_AR"):
        return t[:-3]
    if t.endswith(".BA"):
        return t[:-3]
    return t


def _symbol(ticker: str) -> str:
    """Construye el símbolo BYMA en plazo 24hs."""
    return f"MERV - XMEV - {_to_byma_ticker(ticker)} - {PLAZO}"


def fetch_snapshots(
    session: requests.Session,
    tickers: List[str],
    fecha: date,
) -> Dict[str, TickerSnapshot]:
    """Pide snapshots para todos los tickers. Devuelve {ticker: snapshot}.
    Tickers que no respondieron no aparecen en el dict.
    """
    if not tickers:
        return {}
    symbols = [_symbol(t) for t in tickers]
    sym_to_ticker = dict(zip(symbols, tickers))
    raw = fetch_bulk(session, symbols)
    out: Dict[str, TickerSnapshot] = {}
    for sym, md in raw.items():
        ticker = sym_to_ticker.get(sym)
        if not ticker:
            continue
        snap = parse_snapshot(md, ticker, fecha)
        if snap.is_valid:
            out[ticker] = snap
    return out


# =============================================================================
# CSV upsert (anexar/actualizar)
# =============================================================================

def upsert_csv(
    path: Path,
    new_rows: List[Dict[str, Any]],
    key_cols: List[str],
    column_order: List[str],
) -> Tuple[int, int]:
    """Anexa filas nuevas y reemplaza las que matcheen por key_cols.
    Devuelve (n_new, n_updated)."""
    if not new_rows:
        return 0, 0

    df_new = pd.DataFrame(new_rows)

    if path.is_file():
        try:
            df_old = pd.read_csv(path)
        except Exception as e:
            print(f"[upsert] error leyendo {path}: {e}. Lo recreo.", file=sys.stderr)
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

    if "Fecha" in df_merged.columns:
        df_merged = df_merged.sort_values("Fecha", kind="stable")

    path.parent.mkdir(parents=True, exist_ok=True)
    df_merged.to_csv(path, index=False, encoding="utf-8")
    n_new = len(df_new) - n_updated
    return n_new, n_updated


# =============================================================================
# Mapeo ticker → moneda nativa (para el output del CSV)
# =============================================================================

def infer_moneda_nativa(ticker: str) -> str:
    """Infiere la moneda nativa del ticker.

    Convención BYMA:
      - termina en 'D' → USB (MEP), ej AL30D, GD30D, BPC7D
      - termina en 'C' → USD (cable), ej AL30C, GD30C
      - cualquier otro → ARS

    En la planilla v3.1 cada ticker tiene su moneda nativa declarada en la
    hoja Especies — el usuario puede sobrescribir si carga manualmente.
    """
    t = ticker.strip().upper()
    if t.endswith("D"):
        return "USB"
    if t.endswith("C"):
        return "USD"
    return "ARS"


# =============================================================================
# Run
# =============================================================================

def run(
    tickers: List[str],
    output_dir: Path,
    fecha: Optional[date] = None,
    dry_run: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> int:
    """Punto de entrada principal. Devuelve exit code (0 OK, 1 error)."""
    if fecha is None:
        fecha = date.today()

    if not tickers:
        print("[error] lista de tickers vacía", file=sys.stderr)
        return 1

    username = username or os.getenv("OMS_USER")
    password = password or os.getenv("OMS_PASS")
    if not username or not password:
        print("[error] faltan OMS_USER / OMS_PASS (env vars o secrets.txt)",
              file=sys.stderr)
        return 1

    try:
        session = login_oms(username, password)
    except Exception as e:
        print(f"[error] login falló: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"[run] fecha={fecha.isoformat()} | plazo={PLAZO} | "
          f"tickers a pedir={len(tickers)}")

    snapshots = fetch_snapshots(session, tickers, fecha)

    # Construir filas
    precio_rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for t in tickers:
        snap = snapshots.get(t)
        if snap is None or not snap.is_valid:
            missing.append(t)
            continue
        precio_rows.append({
            "Fecha": snap.fecha.isoformat(),
            "Ticker": snap.ticker,
            "Precio": round(snap.precio, 6),
            "Moneda": infer_moneda_nativa(snap.ticker),
            "Fuente": f"BYMA {snap.fuente} {PLAZO}",
        })

    # Mostrar resumen
    print()
    if precio_rows:
        print(f"  {'TICKER':<10} {'PRECIO':>14} {'MON':<5} {'FUENTE':<20}")
        print(f"  {'-'*10} {'-'*14} {'-'*5} {'-'*20}")
        for row in precio_rows:
            print(f"  {row['Ticker']:<10} {row['Precio']:>14,.6f} "
                  f"{row['Moneda']:<5} {row['Fuente']:<20}")
        print()
    if missing:
        print(f"[sin datos] {len(missing)}: {', '.join(missing)}")
        print()

    if not precio_rows:
        print("[done] ningún ticker tuvo precio válido")
        return 0

    if dry_run:
        print("[dry-run] no se escribió CSV")
        return 0

    n_new, n_upd = upsert_csv(
        path=output_dir / "precios_historico.csv",
        new_rows=precio_rows,
        key_cols=["Fecha", "Ticker"],
        column_order=["Fecha", "Ticker", "Precio", "Moneda", "Fuente"],
    )
    print(f"[csv] {len(precio_rows)} OK → {n_new} nuevos, {n_upd} actualizados")
    print(f"[done]")
    return 0


# =============================================================================
# CLI
# =============================================================================

def parse_tickers_file(path: Path) -> List[str]:
    """Lee un archivo con un ticker por línea. Ignora vacíos y comentarios (#)."""
    if not path.is_file():
        raise FileNotFoundError(path)
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line.split()[0])
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Loader de precios BYMA (foto del momento) → CSV planilla v3.1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--tickers", nargs="+", default=[],
        help="Lista de tickers BYMA (ej AL30D GD30C TX26)",
    )
    p.add_argument(
        "--tickers-file", type=Path,
        help="Archivo con un ticker por línea (alternativa a --tickers)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=Path.cwd() / "data",
        help="Carpeta destino del CSV (default: ./data)",
    )
    p.add_argument(
        "--fecha", type=str, default=None,
        help="Fecha del snapshot (YYYY-MM-DD). Default: hoy.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="No escribir CSV, solo mostrar resultado",
    )

    args = p.parse_args(argv)

    if not os.environ.get("OMS_USER"):
        load_secrets()

    # Reunir tickers
    tickers: List[str] = list(args.tickers)
    if args.tickers_file:
        try:
            tickers.extend(parse_tickers_file(args.tickers_file))
        except FileNotFoundError:
            print(f"[error] archivo no encontrado: {args.tickers_file}",
                  file=sys.stderr)
            return 1
    tickers = sorted(set(t.strip() for t in tickers if t.strip()))

    if not tickers:
        print("[error] no se pasaron tickers. Usá --tickers o --tickers-file",
              file=sys.stderr)
        return 1

    # Parsear fecha
    fecha: Optional[date] = None
    if args.fecha:
        try:
            fecha = date.fromisoformat(args.fecha)
        except ValueError:
            print(f"[error] fecha inválida: {args.fecha} (esperado YYYY-MM-DD)",
                  file=sys.stderr)
            return 1

    return run(
        tickers=tickers,
        output_dir=args.output_dir,
        fecha=fecha,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
