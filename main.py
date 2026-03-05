#!/usr/bin/env python3
"""
DESCARGADOR DE EXPEDIENTES — Mesa Virtual STJER
===============================================

Script principal para descargar expedientes completos de la Mesa Virtual
del Poder Judicial de Entre Ríos.

Uso:
    python main.py

El script te pedirá el número del expediente y se encargará del resto:
  1. ✅ Verifica la sesión (usa cookies guardadas)
  2. 🔍 Búsqueda del expediente
  3. 📥 Descarga de todos los archivos
  4. 🔄 Conversión de RTF a PDF
  5. 📎 Unificación en un solo PDF

Requisitos:
  - Sesión guardada en caché (o hacer login en Mesa Virtual)
  - Librerías: requests, pymupdf
  - LibreOffice instalado (opcional, para convertir RTF)
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime

# Importar config y módulos
import config
from modulos import (
    crear_cliente_sesion,
    crear_buscador,
    crear_descargador,
    crear_conversor,
    crear_unificador,
)


def limpiar_carpeta_temp(carpeta_temp):
    """Elimina la carpeta de archivos temporales."""
    if carpeta_temp.exists():
        try:
            shutil.rmtree(carpeta_temp)
            print("🧹 Archivos temporales eliminados")
        except Exception as e:
            print(f"⚠️  No se pudo limpiar temp: {e}")


def main():
    """Función principal del descargador."""

    print("\n" + "═" * 70)
    print("  DESCARGADOR DE EXPEDIENTES — Mesa Virtual STJER")
    print("═" * 70)

    # ═════════════════════════════════════════════════════════════════════
    # 0. Validar configuración
    # ═════════════════════════════════════════════════════════════════════

    if not config.validar_config():
        print("\n❌ Por favor, configura config.py antes de continuar.")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 1. Solicitar número de expediente
    # ═════════════════════════════════════════════════════════════════════

    print("\n📋 Ingresa los datos del expediente\n")

    numero_expediente = input("  Número de expediente (ej: 22066/14 o 5289): ").strip()

    if not numero_expediente:
        print("\n❌ Debes ingresar un número de expediente.")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 2. Verificar/crear sesión
    # ═════════════════════════════════════════════════════════════════════

    print("\n" + "═" * 70)
    print("  AUTENTICACIÓN")
    print("═" * 70 + "\n")
    print("🔍 Verificando sesión...", end=" ", flush=True)

    try:
        cliente = crear_cliente_sesion(
            api_graphql_url=config.API_GRAPHQL,
            url_mesa_virtual=config.MESA_VIRTUAL_URL
        )
        print("✅\n")
    except Exception as e:
        print(f"\n{e}")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 3. Buscar expediente
    # ═════════════════════════════════════════════════════════════════════

    try:
        buscador = crear_buscador(cliente)
        expediente = buscador.buscar(numero_expediente)

        if not expediente:
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Error en búsqueda: {e}")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 4. Preparar carpetas
    # ═════════════════════════════════════════════════════════════════════

    nro = expediente.get("nro", {})
    exp0 = nro.get("exp0", "x")
    exp1 = nro.get("exp1", "x")
    nombre_carpeta = f"exp_{exp0}_{exp1}"

    carpeta_exp = config.TEMP_DIR / nombre_carpeta
    carpeta_exp.mkdir(parents=True, exist_ok=True)

    print(f"\n📂 Carpeta temporal: {carpeta_exp}")

    # ═════════════════════════════════════════════════════════════════════
    # 5. Descargar archivos
    # ═════════════════════════════════════════════════════════════════════

    try:
        descargador = crear_descargador(
            cliente,
            carpeta_temp=carpeta_exp,
        )

        movimientos = descargador.obtener_movimientos(expediente.get("numero", numero_expediente))
        archivos_descargados = descargador.descargar_archivos(
            expediente.get("numero", numero_expediente),
            movimientos,
        )

        if not archivos_descargados:
            print("\n❌ No se descargó ningún archivo.")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Error en descarga: {e}")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 6. Convertir RTF a PDF
    # ═════════════════════════════════════════════════════════════════════

    try:
        conversor = crear_conversor()
        archivos_procesados = conversor.convertir_multiples(archivos_descargados)
    except Exception as e:
        print(f"\n⚠️  Error en conversión (continuando sin RTF): {e}")
        archivos_procesados = archivos_descargados

    # ═════════════════════════════════════════════════════════════════════
    # 7. Unificar PDFs
    # ═════════════════════════════════════════════════════════════════════

    try:
        unificador = crear_unificador(config.OUTPUT_DIR)
        pdf_final = unificador.unificar(archivos_procesados, nombre_carpeta)

        print(f"\n✅ ¡Listo! PDF descargado en:")
        print(f"\n   {pdf_final}\n")

    except Exception as e:
        print(f"\n❌ Error en unificación: {e}")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 8. Limpiar archivos temporales
    # ═════════════════════════════════════════════════════════════════════

    if config.LIMPIAR_TEMP:
        limpiar_carpeta_temp(carpeta_exp)

    print("=" * 70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏸️  Cancelado por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error inesperado: {e}")
        if config.DEBUG:
            import traceback
            traceback.print_exc()
        sys.exit(1)
