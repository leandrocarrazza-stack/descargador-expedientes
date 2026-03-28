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
  2.  Búsqueda del expediente
  3.  Descarga de todos los archivos
  4.  Conversión de RTF a PDF
  5.  Unificación en un solo PDF

Requisitos:
  - Sesión guardada en caché (o hacer login en Mesa Virtual)
  - Librerías: requests, pymupdf
  - LibreOffice instalado (opcional, para convertir RTF)
"""

import sys

# Importar config y módulos
import config
from modulos import (
    # Pipeline orquestador (Fase 2)
    PipelineDescargador,
    # Logging (Fase 1)
    crear_logger,
    configurar_logging_global,
    # Excepciones (Fase 1)
    MesaVirtualError,
    OpsionesMultiplesExpediente,
)


def limpiar_carpeta_temp(carpeta_temp):
    """Elimina la carpeta de archivos temporales."""
    if carpeta_temp.exists():
        try:
            shutil.rmtree(carpeta_temp)
            print(" Archivos temporales eliminados")
        except Exception as e:
            print(f"  No se pudo limpiar temp: {e}")


def mostrar_encabezado():
    """Muestra el encabezado de bienvenida."""
    print("\n" + "═" * 70)
    print("  DESCARGADOR DE EXPEDIENTES — Mesa Virtual STJER")
    print("═" * 70)
    logger.info("═" * 70)
    logger.info("  DESCARGADOR DE EXPEDIENTES — Mesa Virtual STJER")
    logger.info("═" * 70)


def solicitar_numero_expediente() -> str:
    """Pide al usuario el número de expediente."""
    print("\n📋 Ingresa los datos del expediente\n")
    numero = input("  Número de expediente (ej: 22066/14 o 5289): ").strip()

    if not numero:
        logger.error("Usuario no ingresó número de expediente")
        print("\n❌ Debes ingresar un número de expediente.")
        sys.exit(1)

    logger.info(f"Usuario solicita expediente: {numero}")
    return numero


def seleccionar_expediente_de_opciones(opciones):
    """
    Muestra múltiples opciones de expedientes y pide al usuario que seleccione una.

    Args:
        opciones: Lista de diccionarios con datos de expedientes

    Returns:
        dict: El expediente seleccionado
    """
    print("\n🔍 Se encontraron múltiples expedientes:\n")

    for i, exp in enumerate(opciones, 1):
        caratula = exp.get("caratula", "Sin descripción")[:70]
        numero = exp.get("numero", "N/A")
        tribunal = exp.get("tribunal", "Tribunal no especificado")[:50]
        print(f"   [{i}] {caratula}")
        print(f"       Número: {numero} | Tribunal: {tribunal}\n")

    while True:
        try:
            opcion = input(f"   Elige una opción (1-{len(opciones)}): ").strip()
            numero_opcion = int(opcion)
            if 1 <= numero_opcion <= len(opciones):
                expediente_elegido = opciones[numero_opcion - 1]
                logger.info(f"Usuario seleccionó opción {numero_opcion}: {expediente_elegido.get('numero')}")
                return expediente_elegido
            print(f"   ❌ Número inválido. Intenta entre 1 y {len(opciones)}.")
        except ValueError:
            print("   ❌ Debes ingresar un número válido.")


def mostrar_resultado_exito(resultado):
    """Muestra resultado exitoso."""
    print(f"\n✅ ¡Listo! PDF descargado en:")
    print(f"\n   {resultado.pdf_final}\n")
    print("=" * 70 + "\n")

    # Mostrar estadísticas
    stats = resultado.estadisticas
    logger.info(f"Resultado exitoso:")
    logger.info(f"  - Movimientos: {stats.get('movimientos', '?')}")
    logger.info(f"  - Archivos descargados: {stats.get('archivos_descargados', '?')}")
    logger.info(f"  - Archivos convertidos: {stats.get('archivos_convertidos', '?')}")
    logger.info(f"  - Tiempo total: {stats.get('tiempo_segundos', '?')}s")


def mostrar_resultado_error(resultado):
    """Muestra resultado fallido."""
    print(f"\n❌ Error durante la ejecución:")
    print(f"\n   {resultado.error}\n")
    print("=" * 70 + "\n")

    logger.error(f"Pipeline fallido: {resultado.error}")


def main():
    """
    Función principal simplificada usando PipelineDescargador.

    Flujo:
      1. Validar configuración
      2. Solicitar número de expediente
      3. Crear y ejecutar pipeline
      4. Mostrar resultado
    """
    # Configurar logging al inicio
    configurar_logging_global(
        nivel_archivo=config.LOG_LEVEL_ARCHIVO,
        nivel_consola=config.LOG_LEVEL_CONSOLA,
        logs_dir=config.LOGS_DIR,
    )

    # Mostrar encabezado
    mostrar_encabezado()

    # ═════════════════════════════════════════════════════════════════════
    # 1. Validar configuración
    # ═════════════════════════════════════════════════════════════════════

    if not config.validar_config():
        logger.error("Configuración inválida. Por favor, configura config.py.")
        print("\n❌ Por favor, configura config.py antes de continuar.")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 2. Solicitar número de expediente
    # ═════════════════════════════════════════════════════════════════════

    print("\n Ingresa los datos del expediente\n")

    numero_expediente = input("  Número de expediente (ej: 22066/14 o 5289): ").strip()

    if not numero_expediente:
        print("\n❌ Debes ingresar un número de expediente.")
        sys.exit(1)

    # ═════════════════════════════════════════════════════════════════════
    # 3. Crear y ejecutar pipeline
    # ═════════════════════════════════════════════════════════════════════

    print("\n" + "═" * 70)
    print("  PROCESANDO EXPEDIENTE")
    print("═" * 70 + "\n")
    print(" Verificando sesión...", end=" ", flush=True)

    try:
        resultado = pipeline.ejecutar(
            numero_expediente,
            limpiar_temp=config.LIMPIAR_TEMP,
            expediente_preseleccionado=expediente_seleccionado,
        )
    except OpsionesMultiplesExpediente as e:
        # Manejar caso de múltiples opciones
        logger.info(f"Múltiples opciones encontradas: {len(e.opciones)}")
        expediente_seleccionado = seleccionar_expediente_de_opciones(e.opciones)

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

    print(f"\n Carpeta temporal: {carpeta_exp}")

    # ═════════════════════════════════════════════════════════════════════
    # 5. Descargar archivos
    # ═════════════════════════════════════════════════════════════════════

    try:
        descargador = crear_descargador(
            cliente,
            carpeta_temp=carpeta_exp,
        )

    # ═════════════════════════════════════════════════════════════════════
    # 4. Mostrar resultado
    # ═════════════════════════════════════════════════════════════════════

    try:
        conversor = crear_conversor()
        archivos_procesados = conversor.convertir_multiples(archivos_descargados)
    except Exception as e:
        print(f"\n  Error en conversión (continuando sin RTF): {e}")
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
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Ejecución cancelada por el usuario")
        print("\n\n⏸️  Cancelado por el usuario.")
        sys.exit(0)
    except MesaVirtualError as e:
        logger.error(f"Error del dominio: {e}")
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error inesperado no capturado: {e}", exc_info=True)
        print(f"\n❌ Error inesperado: {e}")
        if config.DEBUG:
            import traceback

            traceback.print_exc()
        sys.exit(1)
