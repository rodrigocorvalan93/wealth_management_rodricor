# -*- coding: utf-8 -*-
"""
historico_byma_loader.py

Loader diario de precios de cierre BYMA + FX MEP/CCL implícitos para la
planilla personal de finanzas v3.1.

CONVENCIÓN DE VALUACIÓN:
  - Bonos AR, acciones, CEDEARs, BOPREALES → todos en plazo 24hs (T+1, estándar BYMA)
  - El FX MEP/CCL se calcula sobre AL30/GD30/GD35 también en 24hs
  - Cripto NO se incluye acá (loader separado a CoinGecko, en otro script)

OUTPUT (formato compatible con la planilla v3.1):
  - precios_historico.csv  →  Fecha, Ticker, Precio, Moneda, Fuente
  - fx_historico.csv       →  Fecha, Moneda, Rate, Cotiza vs, Fuente

Si el archivo destino ya existe, ANEXA las filas nuevas (no pisa).
Si para una fecha+ticker ya hay una fila cargada, la actualiza con el
nuevo dato (último gana — útil si corrés el loader varias veces el mismo día).

USO:
    # un ticker:
    python historico_byma_loader.py --tickers AL30D

    # varios:
    python historico_byma_loader.py --tickers AL30D GD30C TX26 TXMJ9

    # desde archivo (un ticker por línea):
    python historico_byma_loader.py --tickers-file mis_tickers.txt

    # solo el FX (no necesita lista de tickers):
    python historico_byma_loader.py --solo-fx

    # solo precios, sin recalcular FX:
    python historico_byma_loader.py --tickers AL30D --skip-fx

    # output a otra carpeta:
    python historico_byma_loader.py --tickers AL30D --output-dir ./mi_data

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
# Secrets (cargado al importar para que BYMA_API_URL esté disponible)
# =============================================================================

def load_secrets() -> int:
    """Carga secrets.txt (formato KEY=VALUE) a os.environ.

    Busca en este orden:
      1. ./secrets.txt (cwd)
      2. <script_dir>/secrets.txt
    No sobreescribe vars que ya estén en el environment.
    Devuelve cantidad de vars nuevas cargadas (0 si no encontró archivo).
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


# Cargar secrets al importar el módulo, ANTES de definir BASE_URL.
# Esto permite que BYMA_API_URL en secrets.txt sea respetado sin que el
# usuario tenga que exportarlo manualmente como env var del shell.
load_secrets()


# =============================================================================
# Config
# =============================================================================

# URL base. Default al server histórico (latinsecurities). Se puede sobreescribir
# vía env var BYMA_API_URL o vía secrets.txt (ya cargado arriba).
_BASE_URL_DEFAULT = "https://api.latinsecurities.matrizoms.com.ar/"
BASE_URL = os.environ.get("BYMA_API_URL", _BASE_URL_DEFAULT)

# Asegurar trailing slash (así las concatenaciones siguen funcionando)
if not BASE_URL.endswith("/"):
    BASE_URL = BASE_URL + "/"

DEFAULT_ENTRIES = "LA,CL,ACP,TV,NV"  # liviano: precios + volumen
DEFAULT_DEPTH = 1
MAX_WORKERS = 9
PLAZO = "24hs"  # convención fija — todo se valúa en T+1

# Pares para FX implícito. Convención: ticker_ARS / ticker_USB|USD
FX_USB_PAIRS = [
    ("AL30", "AL30D"),
    ("GD30", "GD30D"),
    ("GD35", "GD35D"),
]
FX_USD_PAIRS = [
    ("AL30", "AL30C"),
    ("GD30", "GD30C"),
    ("GD35", "GD35C"),
]


# =============================================================================
# Sesión OMS
# =============================================================================

def login_oms(username: str, password: str) -> requests.Session:
    """Login en OMS. Mismo patrón que bymaapi.login_xoms."""
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
    """Cierre de un ticker para una fecha."""
    ticker: str
    fecha: date
    precio: float          # CL → ACP → LA fallback
    fuente: str            # 'CL' | 'ACP' | 'LA' | ''
    volumen: float         # TV → NV fallback (NaN si no hay)

    @property
    def is_valid(self) -> bool:
        return np.isfinite(self.precio) and self.precio > 0


def parse_snapshot(
    md: Dict[str, Any],
    ticker: str,
    fecha: date,
) -> TickerSnapshot:
    """Aplica fallback CL → ACP → LA y extrae volumen TV → NV."""
    cl_price = _extract_price(md.get("CL"))
    acp_price = _extract_price(md.get("ACP"))
    la_price = _extract_price(md.get("LA"))

    if np.isfinite(cl_price) and cl_price > 0:
        precio, fuente = cl_price, "CL"
    elif np.isfinite(acp_price) and acp_price > 0:
        precio, fuente = acp_price, "ACP"
    elif np.isfinite(la_price) and la_price > 0:
        precio, fuente = la_price, "LA"
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

def _symbol(ticker: str) -> str:
    """Construye el símbolo BYMA en plazo 24hs."""
    return f"MERV - XMEV - {ticker} - {PLAZO}"


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
        # tickers sin precio válido no se loggean acá; se loggean en run()
    return out


# =============================================================================
# FX implícito (MEP / CCL)
# =============================================================================

@dataclass
class FXResult:
    """Resultado del cálculo de FX implícito (MEP o CCL) para una fecha."""
    moneda: str            # 'USB' o 'USD'
    fecha: date
    rate: float            # ARS por unidad
    n_pares: int           # cuántos de los 3 pares se usaron
    pesos_modo: str        # 'volumen' | 'simple' | ''
    detalle: Dict[str, float]  # {par_label: implícito} para audit
    fuente: str            # ej. "AL30/AL30D, GD30/GD30D"

    @property
    def is_valid(self) -> bool:
        return np.isfinite(self.rate) and self.rate > 0 and self.n_pares > 0


def compute_fx(
    snapshots: Dict[str, TickerSnapshot],
    pairs: List[Tuple[str, str]],
    moneda: str,
    fecha: date,
) -> FXResult:
    """Calcula FX implícito (MEP=USB, CCL=USD) por promedio ponderado por volumen.

    Para cada par (ticker_ARS, ticker_FX):
      - Si los dos tickers tienen precio válido, calcula implícito = ARS/FX
      - El volumen del par = promedio entre ARS y FX (más conservador)
    Si los 3 pares tienen volumen → promedio ponderado.
    Si ninguno tiene volumen → promedio simple sobre los disponibles.
    Si solo algunos tienen volumen → ponderado sobre los que tienen, los demás se ignoran.
    """
    implicitos: List[float] = []
    pesos: List[float] = []
    detalle: Dict[str, float] = {}
    pares_usados: List[str] = []

    for t_ars, t_fx in pairs:
        s_ars = snapshots.get(t_ars)
        s_fx = snapshots.get(t_fx)
        if s_ars is None or s_fx is None:
            continue
        if not (s_ars.is_valid and s_fx.is_valid):
            continue
        implicit = s_ars.precio / s_fx.precio
        if not (np.isfinite(implicit) and implicit > 0):
            continue
        # volumen del par = promedio de ambos lados
        vols = [s_ars.volumen, s_fx.volumen]
        vols_finitos = [v for v in vols if np.isfinite(v) and v > 0]
        peso = float(np.mean(vols_finitos)) if vols_finitos else float("nan")

        implicitos.append(implicit)
        pesos.append(peso)
        label = f"{t_ars}/{t_fx}"
        detalle[label] = implicit
        pares_usados.append(label)

    if not implicitos:
        return FXResult(
            moneda=moneda, fecha=fecha, rate=float("nan"),
            n_pares=0, pesos_modo="", detalle={}, fuente="",
        )

    arr_imp = np.array(implicitos, dtype="float64")
    arr_pes = np.array(pesos, dtype="float64")
    pesos_validos = np.isfinite(arr_pes) & (arr_pes > 0)

    if pesos_validos.any():
        # ponderado solo sobre pares que tienen volumen
        rate = float(np.average(arr_imp[pesos_validos], weights=arr_pes[pesos_validos]))
        modo = "volumen"
    else:
        rate = float(np.mean(arr_imp))
        modo = "simple"

    return FXResult(
        moneda=moneda, fecha=fecha, rate=rate,
        n_pares=len(implicitos), pesos_modo=modo,
        detalle=detalle, fuente=", ".join(pares_usados),
    )


# =============================================================================
# CSV upsert (anexar/actualizar)
# =============================================================================

def upsert_csv(
    path: Path,
    new_rows: List[Dict[str, Any]],
    key_cols: List[str],
    column_order: List[str],
) -> Tuple[int, int]:
    """Upsert: si el CSV ya existe, anexa filas nuevas y reemplaza las que
    matcheen por `key_cols`. Si no existe, lo crea. Devuelve (n_new, n_updated)."""
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

    # contar updates antes de mergear
    if not df_old.empty and all(k in df_old.columns for k in key_cols):
        old_keys = df_old[key_cols].astype(str).agg("||".join, axis=1)
        new_keys = df_new[key_cols].astype(str).agg("||".join, axis=1)
        n_updated = int(old_keys.isin(new_keys).sum())
    else:
        n_updated = 0

    df_merged = pd.concat([df_old, df_new], ignore_index=True)

    # último gana: drop_duplicates conserva el último por defecto si keep="last"
    df_merged = df_merged.drop_duplicates(subset=key_cols, keep="last")

    # ordenar columnas (las que falten quedan al final)
    cols_existing = [c for c in column_order if c in df_merged.columns]
    cols_extra = [c for c in df_merged.columns if c not in column_order]
    df_merged = df_merged[cols_existing + cols_extra]

    # ordenar filas por fecha (si existe la columna)
    if "Fecha" in df_merged.columns:
        df_merged = df_merged.sort_values("Fecha", kind="stable")

    # asegurar carpeta
    path.parent.mkdir(parents=True, exist_ok=True)

    df_merged.to_csv(path, index=False, encoding="utf-8")
    n_new = len(df_new) - n_updated
    return n_new, n_updated


# =============================================================================
# Mapeo ticker → moneda nativa (para el output del CSV de precios)
# =============================================================================

def infer_moneda_nativa(ticker: str) -> str:
    """Infiere la moneda nativa del ticker para la columna 'Moneda' del CSV.

    Convención BYMA:
      - termina en 'D' → USB (MEP), ej AL30D, GD30D, BPC7D
      - termina en 'C' → USD (cable), ej AL30C, GD30C
      - cualquier otro → ARS (LECAPs, BONCERes, acciones, CEDEARs en ARS)

    NOTA: esto es un default razonable. En la planilla v3.1 cada ticker
    tiene su moneda nativa declarada en la hoja Especies — el usuario
    puede sobreescribir si carga manualmente. Acá lo inferimos para no
    obligar a pasar un mapping.
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
    skip_fx: bool = False,
    solo_fx: bool = False,
    fecha: Optional[date] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> int:
    """Punto de entrada principal. Devuelve exit code (0 OK, 1 error)."""
    if fecha is None:
        fecha = date.today()

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

    # Tickers que necesitamos: los que pidió el usuario + los del FX
    fx_tickers: List[str] = []
    if not skip_fx:
        for t1, t2 in FX_USB_PAIRS + FX_USD_PAIRS:
            fx_tickers.extend([t1, t2])
        fx_tickers = sorted(set(fx_tickers))

    if solo_fx:
        all_tickers = fx_tickers
    else:
        all_tickers = sorted(set(tickers + (fx_tickers if not skip_fx else [])))

    if not all_tickers:
        print("[warn] no hay tickers para pedir. Pasá --tickers o --solo-fx.",
              file=sys.stderr)
        return 1

    print(f"[run] fecha={fecha.isoformat()} | plazo={PLAZO} | "
          f"tickers a pedir={len(all_tickers)} (incluye {len(fx_tickers)} de FX)")

    snapshots = fetch_snapshots(session, all_tickers, fecha)

    # =========================
    # Precios histórico
    # =========================
    n_precios_new = n_precios_upd = 0
    if not solo_fx:
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

        if precio_rows:
            n_precios_new, n_precios_upd = upsert_csv(
                path=output_dir / "precios_historico.csv",
                new_rows=precio_rows,
                key_cols=["Fecha", "Ticker"],
                column_order=["Fecha", "Ticker", "Precio", "Moneda", "Fuente"],
            )
            print(f"[precios] {len(precio_rows)} OK → "
                  f"{n_precios_new} nuevos, {n_precios_upd} actualizados")
        else:
            print("[precios] ninguno de los tickers solicitados tuvo precio válido")

        if missing:
            print(f"[precios] sin datos: {', '.join(missing)}")

    # =========================
    # FX MEP/CCL implícito
    # =========================
    n_fx_new = n_fx_upd = 0
    if not skip_fx:
        fx_results: List[FXResult] = []
        fx_results.append(compute_fx(snapshots, FX_USB_PAIRS, "USB", fecha))
        fx_results.append(compute_fx(snapshots, FX_USD_PAIRS, "USD", fecha))

        fx_rows: List[Dict[str, Any]] = []
        for fx in fx_results:
            if not fx.is_valid:
                print(f"[fx] {fx.moneda}: NO se pudo calcular "
                      f"(0 pares con datos válidos)")
                continue
            print(f"[fx] {fx.moneda} = {fx.rate:,.4f} ARS  "
                  f"(n_pares={fx.n_pares}, modo={fx.pesos_modo}, "
                  f"fuente={fx.fuente})")
            for label, implicit in fx.detalle.items():
                print(f"        · {label} → {implicit:,.4f}")
            fx_rows.append({
                "Fecha": fx.fecha.isoformat(),
                "Moneda": fx.moneda,
                "Rate": round(fx.rate, 6),
                "Cotiza vs": "ARS",
                "Fuente": f"BYMA implícito ({fx.fuente}) [{fx.pesos_modo}]",
            })

        if fx_rows:
            n_fx_new, n_fx_upd = upsert_csv(
                path=output_dir / "fx_historico.csv",
                new_rows=fx_rows,
                key_cols=["Fecha", "Moneda"],
                column_order=["Fecha", "Moneda", "Rate", "Cotiza vs", "Fuente"],
            )
            print(f"[fx] {len(fx_rows)} OK → "
                  f"{n_fx_new} nuevos, {n_fx_upd} actualizados")

    print(f"[done] precios: {n_precios_new}+{n_precios_upd} | "
          f"fx: {n_fx_new}+{n_fx_upd}")
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
            out.append(line.split()[0])  # primera "palabra" por si hay comentarios
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Loader histórico BYMA + FX MEP/CCL implícito → CSV planilla v3.1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--tickers", nargs="+", default=[],
        help="Lista de tickers BYMA a descargar (ej AL30D GD30C TX26)",
    )
    p.add_argument(
        "--tickers-file", type=Path,
        help="Archivo con un ticker por línea (alternativa a --tickers)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=Path.cwd() / "data",
        help="Carpeta destino de los CSVs (default: ./data)",
    )
    p.add_argument(
        "--skip-fx", action="store_true",
        help="No calcular FX MEP/CCL (solo precios de los tickers pedidos)",
    )
    p.add_argument(
        "--solo-fx", action="store_true",
        help="Solo calcular FX MEP/CCL, ignorar lista de tickers",
    )
    p.add_argument(
        "--fecha", type=str, default=None,
        help="Fecha del snapshot (YYYY-MM-DD). Default: hoy.",
    )

    args = p.parse_args(argv)

    if args.skip_fx and args.solo_fx:
        print("[error] --skip-fx y --solo-fx son mutuamente excluyentes",
              file=sys.stderr)
        return 1

    # secrets.txt ya se cargó al importar el módulo (load_secrets() arriba).
    # Si por algún motivo no existía y ahora lo creaste, lo recargamos:
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

    if not tickers and not args.solo_fx:
        print("[error] no se pasaron tickers. Usá --tickers, --tickers-file o --solo-fx",
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
        skip_fx=args.skip_fx,
        solo_fx=args.solo_fx,
        fecha=fecha,
    )


if __name__ == "__main__":
    sys.exit(main())
