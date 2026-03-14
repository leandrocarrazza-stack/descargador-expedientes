#!/usr/bin/env python3
"""
test_simple.py - Test minimalista sin debug verbose
"""

import logging
import sys

# Desactivar logging detallado
logging.disable(logging.CRITICAL)

from modulos.pipeline import PipelineDescargador

try:
    print("Iniciando pipeline para expediente 21/24...")
    pipeline = PipelineDescargador()
    resultado = pipeline.ejecutar("21/24")

    print(f"\nResultado: exito={resultado.exito}")
    if resultado.error:
        print(f"Error: {resultado.error[:100]}")
    if resultado.pdf_final:
        print(f"PDF generado: {resultado.pdf_final}")

except Exception as e:
    print(f"Error: {str(e)[:200]}")
    sys.exit(1)
