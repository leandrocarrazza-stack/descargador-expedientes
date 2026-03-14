#!/usr/bin/env python3
"""
test_pipeline_directo.py
========================

Test del pipeline sin Flask para ver errores detallados.
"""

import sys
import logging
from pathlib import Path

# Configurar logging detallado
logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s - %(levelname)s - %(message)s'
)

from modulos.pipeline import PipelineDescargador

def main():
    print("\n" + "="*70)
    print("TEST DIRECTO DEL PIPELINE")
    print("="*70 + "\n")

    numero_expediente = "21/24"

    try:
        pipeline = PipelineDescargador()
        print(f"Ejecutando pipeline para: {numero_expediente}")
        print("-" * 70 + "\n")

        resultado = pipeline.ejecutar(numero_expediente)

        print("\n" + "-" * 70)
        print("RESULTADO:")
        print(f"  Exito: {resultado.exito}")
        print(f"  Error: {resultado.error}")
        print(f"  Tipo error: {resultado.tipo_error}")
        if resultado.pdf_final:
            print(f"  PDF final: {resultado.pdf_final}")

        return resultado.exito

    except Exception as e:
        print(f"\n[ERROR] Excepcion: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    exito = main()
    sys.exit(0 if exito else 1)
