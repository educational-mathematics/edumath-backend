# ===========================
#   DOCKERFILE - EduMath
# ===========================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Dependencias del sistema (para psycopg2 y Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/edumath

# Instalar dependencias
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar código de la app
COPY app ./app
COPY static ./static

# Copiar scripts de inicialización
COPY scripts/ ./scripts/

# Copiar entrypoint
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8000
ENV GUNICORN_WORKERS=2
ENV GUNICORN_BIND=0.0.0.0:8000

CMD ["./entrypoint.sh"]