# Dockerfile para Descargador de Expedientes
# ============================================
#
# Imagen con Python + Chrome (headless) + LibreOffice
# Optimizada para Render/Railway (plan básico, ~512 MB RAM)
#
# Build:  docker build -t descargador .
# Run:    docker run -p 5000:5000 --env-file .env descargador

FROM python:3.11-slim

# ── Variables de entorno para que Python y Chrome funcionen bien en Docker ──
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    FLASK_ENV=production

# ── Instalar dependencias del sistema ──
# Chrome: navegador headless para scraping de Mesa Virtual
# LibreOffice: convierte RTF a PDF
# Ghostscript: compresión de PDFs (opcional, desactivado por defecto)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Herramientas base necesarias para agregar repos externos
    wget \
    gnupg \
    ca-certificates \
    unzip \
    # LibreOffice (solo el writer, no la suite completa → ahorra ~200 MB)
    libreoffice-writer \
    # Ghostscript (para compresión de PDFs, desactivado por defecto)
    ghostscript \
    # PostgreSQL client (para psycopg2)
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Instalar Chrome ──
# Método moderno (Debian 12+): gpg --dearmor en vez del obsoleto apt-key
RUN mkdir -p /etc/apt/keyrings \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ── Directorio de trabajo ──
WORKDIR /app

# ── Instalar dependencias Python ──
# Copiar requirements primero para aprovechar cache de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copiar código fuente ──
COPY . .

# ── Crear directorios necesarios ──
RUN mkdir -p temp output logs data

# ── Puerto de la aplicación ──
EXPOSE 5000

# ── Comando de inicio ──
# 1 worker (RAM limitada), pero con pool de threads (gthread) en vez de sync puro:
# el pipeline de descarga corre en un hilo de background y el endpoint de
# long-polling (/descargas/estado) bloquea su request hasta 300s esperando ese
# hilo. Con el worker "sync" por defecto eso deja al ÚNICO worker sin poder
# aceptar NINGUNA otra conexión (ni siquiera un healthcheck) durante esos 5
# minutos. "gthread" permite que el mismo worker atienda varios requests en
# paralelo (uno por hilo) mientras uno queda bloqueado en el long-poll.
# Timeout (330s) > timeout del long-poll (300s en rutas/descargas.py) a propósito:
# si ambos son iguales, gunicorn puede matar al worker ("WORKER TIMEOUT") justo
# cuando el long-poll está a punto de responder por sí mismo con timeout normal.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--worker-class", "gthread", \
     "--threads", "4", \
     "--timeout", "330", \
     "--access-logfile", "-", \
     "--log-level", "info", \
     "servidor:app"]
