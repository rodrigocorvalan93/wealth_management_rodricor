# -*- coding: utf-8 -*-
"""
api/wsgi.py

Entry point WSGI para PythonAnywhere (y otros servers WSGI).

En PythonAnywhere → Web → "WSGI configuration file", apuntá ese archivo a
algo como:

    import sys, os
    project = '/home/rodricor/wealth_management_rodricor'
    if project not in sys.path:
        sys.path.insert(0, project)

    # Variables de entorno (alternativa: setearlas en Web → Environment)
    os.environ.setdefault('WM_API_TOKEN', 'PONÉ_TU_TOKEN_AQUÍ')
    os.environ.setdefault('WM_BASE_DIR', project)

    from api.wsgi import application

USO LOCAL (para test):
    WM_API_TOKEN=test python -m api.wsgi   # arranca werkzeug en :5000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.app import app as application  # noqa: E402

# Alias para compat
app = application


if __name__ == "__main__":
    if not os.environ.get("WM_API_TOKEN"):
        os.environ["WM_API_TOKEN"] = "test-local-token"
        print("[wsgi] WM_API_TOKEN no seteado — usando 'test-local-token'")
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
                    debug=True)
