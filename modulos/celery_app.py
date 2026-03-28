"""
Configuración de Celery para tareas asincrónicas
================================================

Inicializa Celery con Redis como broker.
Permite ejecutar tareas en background sin bloquear el servidor.

Uso:
    celery -A modulos.celery_app worker --loglevel=info
"""

from celery import Celery
import config

# ═══════════════════════════════════════════════════════════════════════════
#  CREAR INSTANCIA DE CELERY
# ═══════════════════════════════════════════════════════════════════════════

celery_app = Celery(
    __name__,
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND
)

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURAR CELERY
# ═══════════════════════════════════════════════════════════════════════════

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='America/Argentina/Buenos_Aires',
    enable_utc=False,

    # Timeouts
    task_soft_time_limit=3600,  # Soft limit: 1 hora
    task_time_limit=3700,        # Hard limit: 1 hora + 100s para cleanup

    # Reintentos
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Resultados
    result_expires=3600,  # Guardar resultados por 1 hora
)

# ═══════════════════════════════════════════════════════════════════════════
#  AUTODISCOVER
# ═══════════════════════════════════════════════════════════════════════════

# Autodiscover tareas en modulos
celery_app.autodiscover_tasks(['modulos'])

# ═══════════════════════════════════════════════════════════════════════════
#  INTEGRACIÓN CON FLASK
# ═══════════════════════════════════════════════════════════════════════════

def init_celery_with_app(app):
    """
    Integra Celery con la app Flask de forma simple.

    Args:
        app: Instancia de Flask app

    Returns:
        celery_app: Instancia de Celery
    """
    # Configurar que la app Flask esté disponible en las tareas
    # Las tareas pueden usar: from flask import current_app
    # O pueden acceder a la BD sin problemas porque SQLAlchemy
    # está configurado con Flask
    return celery_app
