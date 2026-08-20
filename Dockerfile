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
# long-polling (/descargas/estado) bloquea cada request hasta ~25s esperando ese
# hilo (ver rutas/descargas.py: se acorta a propósito para quedar por debajo del
# límite real del proxy de Render, ~60s, así un job de varios minutos se resuelve
# en varios requests cortos en vez de uno sostenido). Con el worker "sync" por
# defecto, incluso esos ~25s dejarían al ÚNICO worker sin poder aceptar NINGUNA
# otra conexión (ni siquiera un healthcheck) mientras dura cada long-poll.
# "gthread" permite que el mismo worker atienda varios requests en paralelo
# (uno por hilo) mientras uno queda bloqueado en el long-poll.
# Timeout 330s: techo de seguridad para cualquier request individual (ya no está
# calibrado contra el long-poll, que ahora es corto) — suficientemente holgado
# para no matar al worker en medio de trabajo legítimo lento.
# Threads en 6 (se probó 12, bajado de nuevo): con la cola de concurrencia
# (modulos/concurrencia.py) puede haber varios jobs "activos" a la vez —cada
# uno reteniendo un hilo en el long-poll de /descargas/estado (~25s)— más
# /descargas/progreso (liviano), el healthcheck de Render y la navegación
# normal. Los jobs en cola NO long-pollean (ver
# templates/descargar_expediente.html), así que el consumo real de hilos
# escala con MAX_NAVEGADORES + 1 (el que está en conversión), no con el
# tamaño de la cola: con MAX_NAVEGADORES=1 son 2 hilos activos + margen para
# healthcheck/navegación normal. 12 era 3x ese cálculo y en un plan de 512 MB
# cada hilo de más es memoria que no le queda a Chrome — la instancia se vio
# reiniciándose sola cada ~12-13 min por OOM con ese valor.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--worker-class", "gthread", \
     "--threads", "6", \
     "--timeout", "330", \
     "--access-logfile", "-", \
     "--log-level", "info", \
     "servidor:app"]
