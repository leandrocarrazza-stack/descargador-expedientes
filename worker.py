#!/usr/bin/env python3
"""
Worker de Celery personalizado para FASE 3.

Este script inicia Celery con el contexto correcto de Flask.

IMPORTANTE: Windows no soporta el pool "prefork" (default).
Usar --pool=solo o --pool=threads en Windows.

Ejemplos:
    # Desarrollo en Windows (síncrono, una tarea a la vez)
    python worker.py --pool=solo

    # Producción en Windows (asincrónico con threads, para I/O-bound)
    python worker.py --pool=threads --concurrency=10

    # Linux/Docker (prefork, múltiples procesos)
    python worker.py --pool=prefork --concurrency=5
"""

import os
import sys
import platform
from pathlib import Path

# Agregar proyecto a sys.path
sys.path.insert(0, str(Path(__file__).parent))

# Inicializar Flask PRIMERO
from servidor import app
from modulos.logger import crear_logger

# Luego inicializar Celery con Flask
from modulos.celery_app import celery_app, init_celery_with_app

logger = crear_logger(__name__)

# Configurar Celery con Flask
init_celery_with_app(app)

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Inicia Celery worker')
    parser.add_argument(
        '--pool',
        default=None,
        help='Pool a usar: prefork (Linux), solo (Windows síncrono), threads (Windows asincrónico)'
    )
    parser.add_argument(
        '--concurrency',
        type=int,
        default=None,
        help='Número de workers concurrentes'
    )
    parser.add_argument(
        '--autoscale',
        default=None,
        help='Autoscaling: max,min (ej: 10,3)'
    )

    args = parser.parse_args()

    # ═════════════════════════════════════════════════════════════════════════
    #  DETECTAR SISTEMA Y ELEGIR POOL POR DEFECTO
    # ═════════════════════════════════════════════════════════════════════════

    sistema = platform.system()
    es_windows = sys.platform == 'win32'

    # Si no se especifica pool, elegir automáticamente
    if args.pool is None:
        if es_windows:
            args.pool = 'solo'  # Solo funciona en Windows
            logger.warning(
                f"⚠️  Sistema Windows detectado. Usando pool='solo' (síncrono).\n"
                f"Para producción con concurrencia, usar: python worker.py --pool=threads --concurrency=10"
            )
        else:
            args.pool = 'prefork'  # Default en Linux
            logger.info(f"✅ Sistema {sistema} detectado. Usando pool='prefork' (multiprocessing)")

    # ═════════════════════════════════════════════════════════════════════════
    #  CONSTRUIR ARGUMENTOS DEL WORKER
    # ═════════════════════════════════════════════════════════════════════════

    worker_args = [
        'worker',
        f'--pool={args.pool}',
        '--loglevel=info',
    ]

    # Agregar concurrencia si se especifica
    if args.concurrency:
        worker_args.append(f'--concurrency={args.concurrency}')
    elif args.pool == 'prefork':
        # Default prefork: 5 procesos
        worker_args.append('--concurrency=5')
    elif args.pool == 'threads':
        # Default threads: 10 threads
        worker_args.append('--concurrency=10')
    # solo pool: no necesita concurrency (síncrono)

    # Agregar autoscaling si se especifica
    if args.autoscale:
        worker_args.append(f'--autoscale={args.autoscale}')
    elif args.pool == 'prefork':
        # Default prefork: autoscale 10-3
        worker_args.append('--autoscale=10,3')

    logger.info(f"🚀 Iniciando Celery worker con argumentos: {worker_args}")

    # ═════════════════════════════════════════════════════════════════════════
    #  INICIAR WORKER
    # ═════════════════════════════════════════════════════════════════════════

    celery_app.worker_main(worker_args)
