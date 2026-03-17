#!/usr/bin/env python3
"""
TEST DE DESCARGA COMPLETA
=========================

Prueba el flujo completo de descarga:
1. Login en Mesa Virtual
2. Buscar expediente
3. Descargar archivos
4. Generar PDF unificado
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modulos.login import crear_cliente_sesion
from modulos.navegacion import crear_buscador
from modulos.descarga import crear_descargador
from modulos.unificacion import crear_unificador
from config import MESA_VIRTUAL_URL, TEMP_DIR, OUTPUT_DIR
import time

def test_descarga_expediente(numero_expediente="12881"):
    """
    Prueba el flujo completo de descarga.

    Args:
        numero_expediente: Número del expediente a descargar
    """

    print("=" * 70)
    print(f"  TEST: DESCARGA COMPLETA DE EXPEDIENTE {numero_expediente}")
    print("=" * 70)

    cliente = None
    pdf_unificado = None

    try:
        # PASO 1: Login
        print(f"\n[PASO 1] Conectando a Mesa Virtual...")
        cliente = crear_cliente_sesion(url_mesa_virtual=MESA_VIRTUAL_URL)
        print("  [OK] Sesión lista")

        # PASO 2: Buscar expediente (si hay múltiples, intentará automáticamente el segundo)
        print(f"\n[PASO 2] Buscando expediente {numero_expediente}...")
        buscador = crear_buscador(cliente)
        # Sin parámetro indice_expediente: intenta automáticamente con el segundo si hay múltiples
        expediente = buscador.buscar(numero_expediente)

        if not expediente:
            print(f"  [ERROR] No se encontró expediente {numero_expediente}")
            return False

        print(f"  [OK] Expediente encontrado")
        print(f"      - Numero: {expediente.get('numero')}")
        print(f"      - Caratula: {str(expediente.get('caratula', ''))[:60]}")

        # PASO 3: Extraer movimientos y contar archivos
        print(f"\n[PASO 3] Analizando movimientos...")
        movimientos = expediente.get('movimientos', [])
        print(f"  [INFO] {len(movimientos)} movimiento(s)")

        total_archivos = 0
        for mov in movimientos:
            archivos = mov.get('archivos', [])
            total_archivos += len(archivos)

        print(f"  [INFO] Total de archivos: {total_archivos}")

        if total_archivos == 0:
            print(f"  [WARNING] El expediente no tiene archivos para descargar")
            return False

        # PASO 4: Descargar archivos
        print(f"\n[PASO 4] Descargando {total_archivos} archivo(s)...")
        descargador = crear_descargador(cliente)

        archivos_descargados = descargador.descargar_todo(expediente)

        if not archivos_descargados:
            print(f"  [ERROR] No se descargó ningún archivo")
            return False

        print(f"  [OK] {len(archivos_descargados)} archivo(s) descargado(s)")

        # PASO 5: Unificar en PDF
        print(f"\n[PASO 5] Generando PDF unificado...")
        unificador = crear_unificador()

        numero_exp = expediente.get('numero', 'DESCONOCIDO')
        caratula = expediente.get('caratula', 'Sin caratula')[:50]

        pdf_unificado = unificador.unificar(
            numero_expediente=numero_exp,
            caratula=caratula,
            archivos=archivos_descargados
        )

        if not pdf_unificado or not pdf_unificado.exists():
            print(f"  [ERROR] No se generó el PDF unificado")
            return False

        print(f"  [OK] PDF generado correctamente")
        print(f"      - Ruta: {pdf_unificado}")
        print(f"      - Tamaño: {pdf_unificado.stat().st_size / 1024:.2f} KB")

        # RESUMEN
        print("\n" + "=" * 70)
        print("  [SUCCESS] DESCARGA COMPLETADA EXITOSAMENTE")
        print("=" * 70)
        print(f"\nArchivo final: {pdf_unificado}")
        print(f"Expediente: {numero_exp} - {caratula}")
        print(f"Archivos procesados: {len(archivos_descargados)}")
        print("=" * 70 + "\n")

        return True

    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Limpiar
        if cliente:
            try:
                cliente.quit()
            except:
                pass

if __name__ == "__main__":
    # Probar con diferentes expedientes hasta encontrar uno con archivos
    expedientes_a_probar = ["12881", "14141", "10000"]

    for exp in expedientes_a_probar:
        print(f"\nIntentando con expediente: {exp}")
        if test_descarga_expediente(exp):
            print(f"[SUCCESS] Descarga exitosa con expediente {exp}")
            break
        else:
            print(f"[INFO] Expediente {exp} no tiene archivos, probando el siguiente...")
            time.sleep(2)
