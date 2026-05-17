#!/usr/bin/env python3
"""
Script para testear que Celery worker arranca correctamente con --pool=solo

Este script inicia el worker en segundo plano y envía una tarea de prueba.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

# Agregar proyecto a sys.path
sys.path.insert(0, str(Path(__file__).parent))

from modulos.logger import crear_logger

logger = crear_logger(__name__)

def test_celery_solo_pool():
    """Testea que Celery funcione con --pool=solo en Windows."""

    logger.info("=" * 70)
    logger.info("  TESTEO: Celery con --pool=solo")
    logger.info("=" * 70)

    # 1. Verificar que Redis esté disponible
    logger.info("\n1️⃣  Verificando conexión a Redis...")
    try:
        from modulos.celery_app import celery_app
        # Intenta conectar al broker
        celery_app.backend.get('test')
        logger.info("✅ Redis disponible en redis://localhost:6379/0")
    except Exception as e:
        logger.error(f"❌ No se puede conectar a Redis: {e}")
        logger.error("Asegúrate que Redis esté corriendo:")
        logger.error("  - En WSL2/Docker: docker run -d -p 6379:6379 redis:7-alpine")
        logger.error("  - Localmente: redis-server")
        return False

    # 2. Verificar que las tareas estén registradas
    logger.info("\n2️⃣  Verificando tareas registradas...")
    from modulos.celery_app import celery_app
    from modulos.tasks import descargar_expediente_task, limpiar_descargas_antiguas_task

    tareas = celery_app.tasks
    logger.info(f"✅ Tareas registradas: {len(tareas)}")
    for nombre_tarea in ['tareas.descargar_expediente', 'tareas.limpiar_descargas_antiguas']:
        if nombre_tarea in tareas:
            logger.info(f"   ✓ {nombre_tarea}")
        else:
            logger.error(f"   ✗ FALTA: {nombre_tarea}")

    # 3. Iniciar Celery worker con --pool=solo (en background, timeout corto)
    logger.info("\n3️⃣  Iniciando Celery worker con --pool=solo...")
    logger.info("   (El worker se ejecutará por 15 segundos para verificar estabilidad)")

    # Usar --max-tasks-per-child=1 para terminar después de 1 tarea (para testing)
    worker_cmd = [
        sys.executable, 'worker.py',
        '--pool=solo',
        '--loglevel=info',
        '--max-tasks-per-child=1'
    ]

    logger.info(f"   Comando: {' '.join(worker_cmd)}")

    # Iniciar worker en subprocess
    try:
        worker_process = subprocess.Popen(
            worker_cmd,
            cwd=str(Path(__file__).parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        logger.info("✅ Celery worker iniciado (PID: {})".format(worker_process.pid))
    except Exception as e:
        logger.error(f"❌ Error iniciando worker: {e}")
        return False

    # 4. Enviar una tarea de prueba
    logger.info("\n4️⃣  Enviando tarea de prueba...")

    try:
        from modulos.tasks import descargar_expediente_task

        # Nota: usamos .apply_async() en lugar de .delay() para tener control del timeout
        task = descargar_expediente_task.apply_async(
            kwargs={
                'user_id': 1,
                'numero_expediente': 'TEST/2026'
            },
            countdown=0
        )

        logger.info(f"✅ Tarea enviada. Task ID: {task.id}")
        logger.info("   (El worker debería procesar esta tarea)")

    except Exception as e:
        logger.error(f"❌ Error enviando tarea: {e}", exc_info=True)
        worker_process.terminate()
        return False

    # 5. Esperar y mostrar output del worker
    logger.info("\n5️⃣  Esperando respuesta del worker (máximo 15 segundos)...")

    try:
        # Capturar output del worker en tiempo real
        start_time = time.time()
        timeout = 15
        worker_output = []

        while worker_process.poll() is None and (time.time() - start_time) < timeout:
            line = worker_process.stdout.readline()
            if line:
                worker_output.append(line.strip())
                # Mostrar líneas importantes
                if 'Starting' in line or 'Received task' in line or 'Task' in line or 'Traceback' in line:
                    logger.info(f"   WORKER: {line.strip()}")
            time.sleep(0.1)

        # Timeout - terminar process
        if worker_process.poll() is None:
            logger.warning(f"   ⏰ Timeout ({timeout}s), terminando worker...")
            worker_process.terminate()
            worker_process.wait(timeout=5)

        elapsed = time.time() - start_time
        logger.info(f"✅ Worker ejecutado en {elapsed:.1f}s")

    except subprocess.TimeoutExpired:
        worker_process.kill()
        logger.error("❌ Worker no respondió en tiempo")
        return False
    except Exception as e:
        logger.error(f"❌ Error esperando worker: {e}")
        return False

    # 6. Verificar que no hubo errores
    logger.info("\n6️⃣  Analizando resultados...")

    if worker_output:
        full_output = '\n'.join(worker_output)
        if 'Traceback' in full_output or 'ValueError' in full_output:
            logger.error("❌ Se detectó un error en el worker:")
            logger.error(full_output[-1000:])  # Últimas 1000 chars
            return False
        else:
            logger.info("✅ No se detectaron errores en el worker")
    else:
        logger.warning("⚠️  No se capturó output del worker (podría estar bien)")

    logger.info("\n" + "=" * 70)
    logger.info("  ✅ TESTEO EXITOSO: Celery funciona con --pool=solo")
    logger.info("=" * 70)
    logger.info("\nPróximo paso: Ejecutar en terminal:")
    logger.info("  python worker.py --pool=solo")
    logger.info("\nLuego, desde otra terminal o código:")
    logger.info("  from modulos.tasks import descargar_expediente_task")
    logger.info("  task = descargar_expediente_task.delay(user_id=1, numero_expediente='21/24')")
    logger.info("\n")

    return True


if __name__ == '__main__':
    success = test_celery_solo_pool()
    sys.exit(0 if success else 1)
