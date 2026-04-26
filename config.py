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
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

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
_DEFAULT_SECRET = 'dev-secret-key-change-in-production'
SECRET_KEY = os.getenv('SECRET_KEY', _DEFAULT_SECRET)
if FLASK_ENV == 'production' and SECRET_KEY == _DEFAULT_SECRET:
    raise RuntimeError("CRITICAL: SECRET_KEY env var no configurada en producción.")

# Configuración de sesión
PERMANENT_SESSION_LIFETIME = timedelta(hours=8)  # Sesión válida por 8 horas
SESSION_COOKIE_SECURE = FLASK_ENV == 'production'  # HTTPS solo en producción
SESSION_COOKIE_HTTPONLY = True  # No accesible desde JavaScript
SESSION_COOKIE_SAMESITE = 'Lax'  # Lax permite csrf_token() en navegación directa
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
    'individual': {
        'nombre': 'Individual',
        'creditos': 1,
        'precio_ars': 3000,
        'descripcion': '1 descarga · $3.000 c/u'
    },
    'estudio': {
        'nombre': 'Estudio',
        'creditos': 10,
        'precio_ars': 24000,  # $2.400 c/u, ahorra $6.000
        'descripcion': '10 descargas · $2.400 c/u'
    },
    'matricula': {
        'nombre': 'Matrícula',
        'creditos': 30,
        'precio_ars': 63000,  # $2.100 c/u, ahorra $27.000
        'descripcion': '30 descargas · $2.100 c/u'
    }
}

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE STRIPE (pagos)
# ═══════════════════════════════════════════════════════════════════════════

STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')

# ═══════════════════════════════════════════════════════════════════════════
#  CONTACTO Y SOPORTE
# ═══════════════════════════════════════════════════════════════════════════

CONTACT_EMAIL = os.getenv('CONTACT_EMAIL', '')
CONTACT_WHATSAPP = os.getenv('CONTACT_WHATSAPP', '')

# ═══════════════════════════════════════════════════════════════════════════
#  EMAIL (Flask-Mail / SMTP)
# ═══════════════════════════════════════════════════════════════════════════

MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', MAIL_USERNAME)

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE CELERY (tareas asincrónicas)
# ═══════════════════════════════════════════════════════════════════════════

# Redis URL para Celery broker
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Configuración de Celery
CELERY_POOL = 'threads'  # 'threads', 'solo', o 'processes'
CELERY_CONFIG = {
    'broker_url': CELERY_BROKER_URL,
    'result_backend': CELERY_RESULT_BACKEND,
    'task_serializer': 'json',
    'accept_content': ['json'],
    'result_serializer': 'json',
    'timezone': 'America/Argentina/Buenos_Aires',
    'enable_utc': True,
    'task_track_started': True,
    'task_time_limit': 30 * 60,  # 30 minutos
}

# ═══════════════════════════════════════════════════════════════════════════
#  JURISPRUDENCIA - STJER (Buscador de Fallos)
# ═══════════════════════════════════════════════════════════════════════════

# Directorios de jurisprudencia
JURISPRUDENCIA_DIR = PROJECT_DIR / "data" / "jurisprudencia"
JURISPRUDENCIA_PDFS_DIR = JURISPRUDENCIA_DIR / "pdfs"

# Crear directorios si no existen
JURISPRUDENCIA_PDFS_DIR.mkdir(parents=True, exist_ok=True)

# Tesauro (cargado en startup)
TESAURO_PATH = JURISPRUDENCIA_DIR / "tesauro.json"
TESAURO_COMPACTO_PATH = JURISPRUDENCIA_DIR / "tesauro_compacto.json"

# Disponible en current_app.config['TESAURO'] y current_app.config['TESAURO_COMPACTO']
TESAURO = None
TESAURO_COMPACTO = None

# Gmail OAuth2 (para descargar adjuntos)
GMAIL_TARGET_ACCOUNT = os.getenv('GMAIL_TARGET_ACCOUNT', 'leofard@gmail.com')
GMAIL_SOURCE_EMAIL = os.getenv('GMAIL_SOURCE_EMAIL', 'scamaragualeguaychu@gmail.com')
GMAIL_CLIENT_ID = os.getenv('GMAIL_CLIENT_ID', '')
GMAIL_CLIENT_SECRET = os.getenv('GMAIL_CLIENT_SECRET', '')
GMAIL_OAUTH_REDIRECT_URI = os.getenv(
    'GMAIL_OAUTH_REDIRECT_URI',
    'http://localhost:5000/jurisprudencia/admin/gmail-oauth-callback'
)

# Claude API (para búsqueda conversacional)
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

# APScheduler (descarga mensual)
SCHEDULER_API_ENABLED = False
SCHEDULER_TIMEZONE = 'America/Argentina/Buenos_Aires'
JOBS = []

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
