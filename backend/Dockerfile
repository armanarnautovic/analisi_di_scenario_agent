FROM ghcr.io/astral-sh/uv:python3.11-alpine

ENV ENV_MODE=production
WORKDIR /app

# 🔧 tool base per pip build + bash (facoltativo se usi /bin/sh)
RUN apk add --no-cache \
    bash \
    build-base \
    libffi-dev \
    openssl-dev \
    git \
    curl \
    tmux

# deps python
COPY pyproject.toml uv.lock ./
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --quiet

# codice app
COPY . .

# 🔧 crea la workspace condivisa per LocalProcess
RUN mkdir -p /workspace && chmod -R 777 /workspace

# (facoltativo, ma consigliato) utente non-root
# RUN adduser -D -u 1000 appuser && chown -R appuser:appuser /app /workspace
# USER appuser

# Tuning gunicorn (se questa immagine serve anche al backend API)
ENV WORKERS=7
ENV THREADS=2
ENV WORKER_CONNECTIONS=2000
ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["sh", "-c", "uv run gunicorn api:app \
  --workers $WORKERS \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 1800 \
  --graceful-timeout 600 \
  --keep-alive 1800 \
  --max-requests 0 \
  --max-requests-jitter 0 \
  --forwarded-allow-ips '*' \
  --worker-connections $WORKER_CONNECTIONS \
  --worker-tmp-dir /dev/shm \
  --preload \
  --log-level info \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  --enable-stdio-inheritance \
  --threads $THREADS"]