"""
Configuración del Descargador de Expedientes
=============================================

Ambiente: desarrollo, testing, producción
Credenciales: cargadas desde .env (NUNCA hardcodeadas)

IMPORTANTE:
- Variables sensibles en .env: SECRET_KEY, DATABASE_URL, STRIPE_*, ANTHROPIC_API_KEY
- Este archivo NO contiene credenciales
- En producción: todas las variables env deben estar configuradas
"""

import os
import logging
from pathlib import Path
from datetime import timedelta

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN BASE
# ═══════════════════════════════════════════════════════════════════════════

# Carpeta base del proyecto
PROJECT_DIR = Path(__file__).parent

# Ambiente: development, testing, production
FLASK_ENV = os.getenv('FLASK_ENV', 'development')
DEBUG = FLASK_ENV == 'development'

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE FLASK
# ═══════════════════════════════════════════════════════════════════════════

# Secret key para sesiones y CSRF (CRÍTICO: cambiar en producción)
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Configuración de sesión
PERMANENT_SESSION_LIFETIME = timedelta(days=7)  # Sesión válida por 7 días
SESSION_COOKIE_SECURE = FLASK_ENV == 'production'  # HTTPS solo en producción
SESSION_COOKIE_HTTPONLY = True  # No accesible desde JavaScript
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
SESSION_REFRESH_EACH_REQUEST = True  # Refrescar expiry en cada request

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════

# URL de conexión a BD
# Desarrollo: SQLite (local)
# Producción: PostgreSQL (Render)
if FLASK_ENV == 'testing':
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'  # BD en memoria para testing
elif FLASK_ENV == 'production':
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL env var not set in production")
    # Render usa postgres:// pero SQLAlchemy 1.4+ requiere postgresql://
    SQLALCHEMY_DATABASE_URI = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
else:
    # Desarrollo: SQLite local
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{PROJECT_DIR / "app.db"}'

# Configuración SQLAlchemy
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ECHO = DEBUG  # Log de queries en modo debug

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE DIRECTORIOS
# ═══════════════════════════════════════════════════════════════════════════

# Crear directorios si no existen
for directorio in [PROJECT_DIR / "temp", PROJECT_DIR / "output", PROJECT_DIR / "logs"]:
    directorio.mkdir(exist_ok=True)

# Carpeta de archivos temporales
TEMP_DIR = PROJECT_DIR / "temp"

# Carpeta de PDFs finales
OUTPUT_DIR = PROJECT_DIR / "output"

# Carpeta de logs
LOGS_DIR = PROJECT_DIR / "logs"

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE MESA VIRTUAL (web scraping)
# ═══════════════════════════════════════════════════════════════════════════

# URL base de la Mesa Virtual
MESA_VIRTUAL_URL = "https://mesavirtual.jusentrerios.gov.ar"

# Endpoint de la API GraphQL
API_GRAPHQL = "https://mesavirtual.jusentrerios.gov.ar/api/graphql"

# Endpoint de descarga de archivos
API_ARCHIVOS = "https://mesavirtual.jusentrerios.gov.ar/api/archivos"

# Timeout para requests HTTP (en segundos)
REQUEST_TIMEOUT = 30

# Reintentos automáticos si falla una descarga
MAX_REINTENTOS = 3

# Limpiar archivos temporales al finalizar
LIMPIAR_TEMP = True

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LOGGING
# ═══════════════════════════════════════════════════════════════════════════

# Nivel de logging para archivo (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL_ARCHIVO = logging.DEBUG

# Nivel de logging para consola (más restrictivo que archivo)
LOG_LEVEL_CONSOLA = logging.INFO

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LIBREOFFICE (conversión RTF  PDF)
# ═══════════════════════════════════════════════════════════════════════════

# Intentar convertir RTF a PDF (requiere LibreOffice instalado)
CONVERTIR_RTF = True

# Timeout para conversión (en segundos)
CONVERSION_TIMEOUT = 60

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE PLANES Y CRÉDITOS
# ═══════════════════════════════════════════════════════════════════════════

# Precio por descarga en ARS
PRECIO_DESCARGA_ARS = 3000

# Planes de compra de créditos (cada crédito = 1 descarga)
PLANES = {
    'basico': {
        'nombre': 'Pack Básico',
        'creditos': 1,
        'precio_ars': 3000,
        'descripcion': '1 descarga'
    },
    'ahorro': {
        'nombre': 'Pack Ahorro',
        'creditos': 5,
        'precio_ars': 12000,  # $3000 c/u, ahorra $3000
        'descripcion': '5 descargas (ahorra $3.000)'
    },
    'profesional': {
        'nombre': 'Pack Profesional',
        'creditos': 20,
        'precio_ars': 42000,  # $3000 c/u, ahorra $18.000
        'descripcion': '20 descargas (ahorra $18.000)'
    }
}

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE STRIPE (pagos)
# ═══════════════════════════════════════════════════════════════════════════

STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE CELERY (tareas asincrónicas)
# ═══════════════════════════════════════════════════════════════════════════

# Redis URL para Celery broker
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# ═══════════════════════════════════════════════════════════════════════════
#  VALIDACIÓN AUTOMÁTICA
# ═══════════════════════════════════════════════════════════════════════════


def validar_config():
    """Valida que la configuración básica sea correcta."""
    # Verificar que las URLs estén configuradas
    if not MESA_VIRTUAL_URL or not API_GRAPHQL or not API_ARCHIVOS:
        print("\n" + "═" * 70)
        print("  ❌ ERROR: URLs NO CONFIGURADAS")
        print("═" * 70 + "\n")
        print("Edita config.py y asegúrate de que las URLs estén presentes.")
        print("\n" + "═" * 70 + "\n")
        return False

    return True


if __name__ == "__main__":
    print("Validando configuración...")
    if validar_config():
        print("✅ Configuración válida")
    else:
        print("❌ Configuración incompleta")
