#!/usr/bin/env python3
"""
Worker de Celery para Descargador de Expedientes
=================================================

Script para ejecutar workers que procesan tareas asincrónicas.

Los workers escuchan en la cola de Redis y ejecutan tareas de descarga
en background, sin bloquear el servidor Flask.

Uso:
    # Windows (pool=solo - recomendado)
    python worker.py

    # Windows (pool=threads - alternativa)
    python worker.py --pool=threads --concurrency=4

    # Linux/Mac
    python worker.py

    # Con Celery directamente
    celery -A modulos.celery_app worker --pool=solo -l info

Ambiente:
    - Requiere Redis corriendo (localhost:6379 o env REDIS_URL)
    - Requiere Flask app context para acceder a BD
    - Windows: usa pool=solo por defecto
"""

import os
import sys
import logging
from pathlib import Path

# Agregar proyecto a path
sys.path.insert(0, str(Path(__file__).parent))

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Importar Celery app
from modulos.celery_app import celery_app
import config

# ═══════════════════════════════════════════════════════════════════════════
#  VERIFICACIONES PRE-EJECUCIÓN
# ═══════════════════════════════════════════════════════════════════════════

logger.info("╔═══════════════════════════════════════════════════════════════╗")
logger.info("║  WORKER DE CELERY - DESCARGADOR DE EXPEDIENTES              ║")
logger.info("╚═══════════════════════════════════════════════════════════════╝")
logger.info("")

# Verificar Redis
logger.info(f"[1/3] Verificando Redis...")
logger.info(f"  Broker: {config.CELERY_BROKER_URL}")

try:
    import redis
    from redis.exceptions import ConnectionError

    # Parsear URL de Redis
    if "://" in config.CELERY_BROKER_URL:
        # redis://localhost:6379/0
        broker_url = config.CELERY_BROKER_URL
        if broker_url.startswith("rediss://"):
            # Redis con SSL
            r = redis.from_url(broker_url, ssl_cert_reqs="none")
        else:
            r = redis.from_url(broker_url)
    else:
        r = redis.Redis()

    r.ping()
    logger.info("  ✅ Redis conectado correctamente")
except ConnectionError as e:
    logger.error("  ❌ No se puede conectar a Redis")
    logger.error(f"     {str(e)}")
    logger.error("     Verifica que Redis esté corriendo")
    sys.exit(1)
except Exception as e:
    logger.error(f"  ❌ Error: {str(e)}")
    sys.exit(1)

logger.info("")

# Verificar configuración de Celery
logger.info(f"[2/3] Configuración de Celery...")
logger.info(f"  Pool: {config.CELERY_POOL}")
logger.info(f"  Reintentos: {config.CELERY_MAX_RETRIES}")
logger.info(f"  Timeout: {config.CELERY_TASK_TIME_LIMIT}s")
logger.info("  ✅ Configuración cargada")
logger.info("")

# Verificar tareas registradas
logger.info(f"[3/3] Tareas registradas...")
logger.info(f"  Total: {len(celery_app.tasks)}")
for task_name in sorted(celery_app.tasks.keys()):
    if not task_name.startswith('celery.'):  # Ignorar tareas internas
        logger.info(f"    - {task_name}")
logger.info("  ✅ Tareas cargadas")

logger.info("")
logger.info("╔═══════════════════════════════════════════════════════════════╗")
logger.info("║  ✅ WORKER LISTO - Esperando tareas...                       ║")
logger.info("╚═══════════════════════════════════════════════════════════════╝")
logger.info("")
logger.info("Presiona Ctrl+C para detener el worker")
logger.info("")

# ═══════════════════════════════════════════════════════════════════════════
#  EJECUTAR WORKER
# ═══════════════════════════════════════════════════════════════════════════

try:
    # Configurar argumentos para Windows
    argv = ['worker', '--loglevel=info']

    # Agregar pool según ambiente
    if config.CELERY_POOL == 'solo':
        argv.append('--pool=solo')
    elif config.CELERY_POOL == 'threads':
        argv.extend(['--pool=threads', '--concurrency=4'])

    # Ejecutar
    celery_app.worker_main(argv)

except KeyboardInterrupt:
    logger.info("\n✅ Worker detenido")
    sys.exit(0)
except Exception as e:
    logger.error(f"❌ Error al ejecutar worker: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
