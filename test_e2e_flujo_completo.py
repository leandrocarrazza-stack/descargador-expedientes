#!/usr/bin/env python3
"""
TEST E2E COMPLETO
=================

Flujo end-to-end completo:
1. Login en Mesa Virtual
2. Buscar expediente
3. Extraer movimientos
4. Descargar archivos
5. Unificar en PDF
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modulos.login import crear_cliente_sesion
from modulos.navegacion import crear_buscador
from modulos.descarga import DescargadorArchivos, crear_descargador
from modulos.unificacion import UnificadorPDF
from config import MESA_VIRTUAL_URL, TEMP_DIR, OUTPUT_DIR


def test_flujo_completo(numero_expediente="14141"):
    """
    Flujo E2E completo de descarga.

    Args:
        numero_expediente: Numero del expediente a descargar
    """

    print("=" * 70)
    print(f"  FLUJO E2E COMPLETO: EXPEDIENTE {numero_expediente}")
    print("=" * 70)

    cliente = None

    try:
        # PASO 1: Login
        print(f"\n[PASO 1/5] Conectando a Mesa Virtual...")
        cliente = crear_cliente_sesion(url_mesa_virtual=MESA_VIRTUAL_URL)
        print("  [OK] Sesion activa")

        # PASO 2: Buscar y extraer movimientos
        print(f"\n[PASO 2/5] Buscando expediente {numero_expediente}...")
        buscador = crear_buscador(cliente)
        expediente = buscador.buscar(numero_expediente)

        if not expediente:
            print("  [ERROR] Expediente no encontrado")
            return False

        numero = expediente.get('numero', 'DESCONOCIDO')
        tribunal = expediente.get('tribunal', 'N/A')
        caratula = expediente.get('caratula', 'Sin caratula')[:60]

        print(f"  [OK] Expediente obtenido")
        print(f"      Numero: {numero}")
        print(f"      Tribunal: {tribunal}")
        print(f"      Caratula: {caratula}")

        # PASO 3: Obtener movimientos
        movimientos = expediente.get('movimientos', [])
        print(f"\n[PASO 3/5] Movimientos extraidos: {len(movimientos)}")

        if not movimientos:
            print("  [WARNING] No hay movimientos/archivos para descargar")
            return False

        for i, mov in enumerate(movimientos[:3], 1):
            fecha = mov.get('fecha', '')
            desc = mov.get('descripcion', '')[:40]
            print(f"      [{i}] {fecha} - {desc}")

        # PASO 4: Descargar archivos
        print(f"\n[PASO 4/5] Descargando archivos...")
        descargador = DescargadorArchivos(cliente, TEMP_DIR)

        # El descargador espera que estemos en la página del expediente
        # Ya estamos ahí, así que podemos descargar directamente
        archivos_descargados = []

        # Para cada movimiento, descargar sus archivos
        for i, movimiento in enumerate(movimientos):
            # Buscar botón de descarga para este movimiento
            # Los botones de descarga están en la columna "Opciones"
            # Esto requiere hacer click en el botón y manejar la descarga
            try:
                # Aquí iría la lógica de descarga específica
                # Por ahora, simulamos que se descargó
                print(f"      [INFO] Procesando movimiento {i + 1}/{len(movimientos)}")
            except Exception as e:
                print(f"      [WARN] Error descargando movimiento {i + 1}: {e}")

        print(f"  [OK] Descarga completada")

        # PASO 5: Unificar en PDF
        print(f"\n[PASO 5/5] Unificando en PDF...")

        if not archivos_descargados:
            print("  [WARNING] No hay archivos descargados para unificar")
            return False

        unificador = UnificadorPDF(TEMP_DIR, OUTPUT_DIR)
        pdf_final = unificador.unificar(numero, archivos_descargados)

        if pdf_final and pdf_final.exists():
            print(f"  [OK] PDF generado: {pdf_final}")
            print(f"       Tamaño: {pdf_final.stat().st_size / 1024:.2f} KB")
        else:
            print(f"  [ERROR] No se generó el PDF")
            return False

        # RESUMEN
        print("\n" + "=" * 70)
        print(f"  [SUCCESS] FLUJO COMPLETADO")
        print("=" * 70)
        print(f"\nExpediente: {numero} - {caratula}")
        print(f"Movimientos: {len(movimientos)}")
        print(f"Archivos descargados: {len(archivos_descargados)}")
        print(f"PDF final: {pdf_final}")
        print("=" * 70 + "\n")

        return True

    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        if cliente:
            try:
                cliente.quit()
            except:
                pass


if __name__ == "__main__":
    exito = test_flujo_completo()
    sys.exit(0 if exito else 1)
