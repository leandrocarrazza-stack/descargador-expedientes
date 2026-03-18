"""
Configuración de Celery para Descargador de Expedientes
========================================================

Celery es un task queue distribuido que permite ejecutar tareas
de larga duración en background sin bloquear el servidor Flask.

Arquitectura:
- Broker (Redis): Cola de tareas
- Worker: Procesos que ejecutan tareas
- Result Backend (Redis): Almacena resultados

Uso:
    # En otra terminal, ejecutar worker:
    python worker.py

    # O con Celery directamente:
    celery -A modulos.celery_app worker --pool=solo (Windows)
    celery -A modulos.celery_app worker (Linux/Mac)
"""

from celery import Celery
import config
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  CREAR INSTANCIA DE CELERY
# ═══════════════════════════════════════════════════════════════════════════

celery_app = Celery(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  CARGAR CONFIGURACIÓN DESDE config.py
# ═══════════════════════════════════════════════════════════════════════════

# Método 1: Usar CELERY_CONFIG (recomendado)
celery_app.config_from_object(config.CELERY_CONFIG)

# Método 2: Configuración individual (por compatibilidad)
celery_app.conf.broker_url = config.CELERY_BROKER_URL
celery_app.conf.result_backend = config.CELERY_RESULT_BACKEND

# ═══════════════════════════════════════════════════════════════════════════
#  AUTO-DISCOVER DE TAREAS
# ═══════════════════════════════════════════════════════════════════════════

# Buscar automáticamente tareas en modulos/tasks.py
# (Se ejecuta cuando se importa este módulo)
celery_app.autodiscover_tasks(['modulos'])

logger.info(f"✅ Celery configurado")
logger.info(f"   Broker: {config.CELERY_BROKER_URL}")
logger.info(f"   Backend: {config.CELERY_RESULT_BACKEND}")
logger.info(f"   Pool: {config.CELERY_POOL}")
