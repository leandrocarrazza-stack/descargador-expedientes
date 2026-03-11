#!/usr/bin/env python3
"""
Worker de Celery personalizado para FASE 3.

Este script inicia Celery con el contexto correcto de Flask.
Ejecutar con: python worker.py
"""

import os
import sys
from pathlib import Path

# Agregar proyecto a sys.path
sys.path.insert(0, str(Path(__file__).parent))

# Inicializar Flask PRIMERO
from servidor import app

# Luego inicializar Celery con Flask
from modulos.celery_app import celery_app, init_celery_with_app

# Configurar Celery con Flask
init_celery_with_app(app)

if __name__ == '__main__':
    # Iniciar worker
    celery_app.worker_main([
        'worker',
        '--loglevel=info',
        '--concurrency=5',
        '--autoscale=10,3'
    ])
