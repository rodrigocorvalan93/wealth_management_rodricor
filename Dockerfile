# syntax=docker/dockerfile:1.6
#
# Dockerfile multi-stage para WM Wealth Management.
# Imagen final ~150 MB. Run con:
#   docker build -t wm .
#   docker run -p 8000:8000 -v wm_data:/app/data -v wm_inputs:/app/inputs \
#     -e WM_BOOTSTRAP_SUPERADMIN_EMAIL=tu@email.com \
#     -e WM_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
#     wm

FROM python:3.11-slim AS deps
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libs that pandas / cryptography compile-time need (slim base
# is missing libffi, openssl headers in some platforms; if no wheels
# match for ARM, this lets pip fall back to source build).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential libssl-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# ---------------------------------------------------------------- runtime
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WM_BASE_DIR=/app

# Copiar deps ya instaladas (mucho más rápido que reinstalar en runtime)
COPY --from=deps /usr/local/lib/python3.11/site-packages \
                  /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# user no-root
RUN useradd -m -u 1000 wm
WORKDIR /app

COPY --chown=wm:wm . /app

# Carpetas montables como volumes para persistir entre deploys
RUN mkdir -p /app/data /app/inputs && chown -R wm:wm /app
USER wm

EXPOSE 8000

# Health check para Render / Fly / etc
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
                    sys.exit(0 if urllib.request.urlopen('http://localhost:${PORT}/api/health', timeout=3).status==200 else 1)"

# 2 workers, 4 threads cada uno = 8 concurrent. Suficiente para hobby /
# pequeños deploys. Subilo cuando crezca el tráfico.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 --access-logfile - api.wsgi:application"]
