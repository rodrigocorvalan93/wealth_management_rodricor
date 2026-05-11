# -*- coding: utf-8 -*-
"""
api/jobs.py

Sistema simple de background jobs per-user persistido a disco.

Motivación: algunos fetch (IBKR Flex polling) tardan 30-90 segundos, lo cual
excede el timeout del proxy / gunicorn worker (60s default). Resultado: el
cliente recibe 502 sin info útil.

Solución: el endpoint POST devuelve job_id inmediatamente, lanza un thread
que hace el trabajo, y el cliente pollea GET /api/import/jobs/<job_id>.

Persistencia: cada job es un JSON en
    <user_data_dir>/jobs/<job_id>.json

Esto sobrevive a múltiples workers de gunicorn (cada worker es un proceso
con memoria propia, así que un dict in-memory no serviría). Los workers
comparten el disco.

Layout del JSON:
    {
      "job_id": "abc123",
      "kind": "import_preview",
      "status": "pending" | "done" | "error",
      "created_at": ISO,
      "finished_at": ISO | null,
      "result": dict | null,
      "error": str | null
    }

Cleanup: get_job() borra automáticamente jobs > 1h de antigüedad.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


_MAX_JOB_AGE_SECONDS = 3600   # 1 hora
# El job de import de IBKR puede tardar ~4 min con los retries de 1001:
# 1001 backoffs [20, 60, 120] + _get_report polling (max 72s) + requests.
# 8 min cubre el peor caso con margen para no marcar como stuck a algo
# que sigue funcionando.
_STUCK_AFTER_SECONDS = 480


def _jobs_dir(user_id: str) -> Path:
    from .state import get_user_settings
    s = get_user_settings(user_id)
    d = s.user_data_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_path(user_id: str, job_id: str) -> Path:
    # Validamos que job_id sea hex (uuid4 truncado) para evitar path traversal
    if not all(c in "0123456789abcdef" for c in job_id) or not job_id:
        raise ValueError(f"job_id inválido: {job_id!r}")
    return _jobs_dir(user_id) / f"{job_id}.json"


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(user_id: str, kind: str, fn: Callable[[], Any]) -> str:
    """Crea un job, arranca un thread daemon que ejecuta `fn` y devuelve job_id.

    `fn` no recibe argumentos — capturalo con closure. Su return value (debe
    ser JSON-serializable) queda en `result`. Si lanza, el mensaje queda en
    `error`.

    El thread es daemon: si el worker de gunicorn se reinicia, el job se
    pierde (queda "pending" hasta cleanup). El cliente se entera porque
    pasa el threshold de _STUCK_AFTER_SECONDS.
    """
    job_id = uuid.uuid4().hex[:16]
    path = _job_path(user_id, job_id)
    state = {
        "job_id": job_id,
        "kind": kind,
        "status": "pending",
        "created_at": _now_iso(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    _write_atomic(path, state)

    def runner():
        try:
            res = fn()
            state["status"] = "done"
            state["result"] = res
        except Exception as e:
            state["status"] = "error"
            state["error"] = f"{type(e).__name__}: {e}"
            # Loguear stack para debug server-side
            try:
                print(f"[jobs] {job_id} {kind} fallo:\n{traceback.format_exc()}",
                      flush=True)
            except Exception:
                pass
        finally:
            state["finished_at"] = _now_iso()
            try:
                _write_atomic(path, state)
            except OSError:
                pass

    t = threading.Thread(target=runner, daemon=True, name=f"job-{job_id}")
    t.start()
    return job_id


def get_job(user_id: str, job_id: str) -> Optional[dict]:
    """Lee el estado del job. Devuelve None si no existe.

    Side effect: si el job está pending pero el archivo lleva > 5 min sin
    updates, lo marca como error con mensaje de timeout (el worker que lo
    inició probablemente murió).

    También dispara cleanup de jobs > 1h.
    """
    _cleanup_old_jobs(user_id)
    try:
        path = _job_path(user_id, job_id)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    # Detectar jobs stuck (worker murió mid-flight)
    if state.get("status") == "pending":
        try:
            age = time.time() - path.stat().st_mtime
            if age > _STUCK_AFTER_SECONDS:
                state["status"] = "error"
                state["error"] = (
                    f"Job sin progreso por {int(age)}s — el worker probablemente "
                    f"murió. Reintentá la operación."
                )
                state["finished_at"] = _now_iso()
                try:
                    _write_atomic(path, state)
                except OSError:
                    pass
        except OSError:
            pass

    return state


def _cleanup_old_jobs(user_id: str) -> None:
    """Borra archivos de jobs más viejos que _MAX_JOB_AGE_SECONDS."""
    try:
        d = _jobs_dir(user_id)
    except Exception:
        return
    now = time.time()
    for f in d.glob("*.json"):
        try:
            if now - f.stat().st_mtime > _MAX_JOB_AGE_SECONDS:
                f.unlink()
        except OSError:
            pass
    # Tmp leftovers
    for f in d.glob("*.tmp"):
        try:
            if now - f.stat().st_mtime > 60:
                f.unlink()
        except OSError:
            pass
