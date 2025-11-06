set -e

echo "[entrypoint] Starting EduMath..."

# 1️ Escribir credenciales de Google si se pasan en base64
if [[ -n "$GOOGLE_JSON_BASE64" ]]; then
  mkdir -p /opt/edumath/secrets
  echo "$GOOGLE_JSON_BASE64" | base64 -d > /opt/edumath/secrets/credentials.json
  export GOOGLE_APPLICATION_CREDENTIALS="/opt/edumath/secrets/credentials.json"
  echo "[entrypoint] Wrote GOOGLE_APPLICATION_CREDENTIALS file."
fi

# 2️ Crear directorios de trabajo (generados por la app)
mkdir -p /opt/edumath/app/static/tts
mkdir -p /opt/edumath/app/static/gen
mkdir -p /opt/edumath/app/static/generated

# 3️ Ejecutar seeds (solo la primera vez)
STAMP="/opt/edumath/.seed_done"
if [[ "$RUN_SEEDS" == "1" && ! -f "$STAMP" ]]; then
  echo "[entrypoint] Running initial seeds..."
  python scripts/seed_topics.py || true
  python scripts/seed_badges.py || true
  touch "$STAMP"
  echo "[entrypoint] Seeds completed."
fi

# 4️ Lanzar servidor
echo "[entrypoint] Launching Gunicorn..."
exec gunicorn app.main:app \
  --workers ${GUNICORN_WORKERS:-2} \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind ${GUNICORN_BIND:-0.0.0.0:8000} \
  --timeout 120