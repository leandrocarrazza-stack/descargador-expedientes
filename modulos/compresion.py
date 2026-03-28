# modulos/compresion.py
"""
Compresión de PDFs — DESACTIVADO POR DEFECTO
=============================================

Este módulo reduce el tamaño de PDFs usando Ghostscript.
Está desactivado porque la compresión consume mucha CPU y RAM,
y en servidores básicos (Render free/starter, Railway) puede causar
timeouts o errores Out-Of-Memory.

CÓMO ACTIVAR:
  1. Instalar Ghostscript en el servidor:
     - Docker:  apt-get install ghostscript
     - Local:   choco install ghostscript  (Windows)
  2. En .env, agregar:  COMPRIMIR_PDF=true
  3. (Opcional) Ajustar calidad: COMPRESION_CALIDAD=ebook

NIVELES DE CALIDAD (menos calidad = menos tamaño):
  - screen:   72 dpi  — muy comprimido, para pantalla
  - ebook:    150 dpi — balance calidad/tamaño (RECOMENDADO)
  - printer:  300 dpi — alta calidad, compresión moderada
  - prepress: 300 dpi — máxima calidad, compresión mínima
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Leer configuración desde variables de entorno
COMPRIMIR_PDF = os.environ.get('COMPRIMIR_PDF', 'false').lower() == 'true'
COMPRESION_CALIDAD = os.environ.get('COMPRESION_CALIDAD', 'ebook')


def _detectar_ghostscript() -> Optional[str]:
    """
    Busca Ghostscript en el sistema (gs en Linux, gswin64c en Windows).

    Returns:
        Ruta del ejecutable de Ghostscript, o None si no está instalado
    """
    # Nombres posibles del ejecutable
    nombres = ['gs', 'gswin64c', 'gswin32c']

    for nombre in nombres:
        try:
            resultado = subprocess.run(
                [nombre, '--version'],
                capture_output=True, text=True, timeout=5
            )
            if resultado.returncode == 0:
                version = resultado.stdout.strip()
                logger.info(f"Ghostscript detectado: {nombre} v{version}")
                return nombre
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return None


def comprimir_pdf(ruta_entrada: Path, ruta_salida: Optional[Path] = None,
                  calidad: Optional[str] = None) -> Optional[Path]:
    """
    Comprime un PDF usando Ghostscript.

    IMPORTANTE: Esta función solo hace algo si COMPRIMIR_PDF=true en .env.
    Si está desactivado, devuelve la ruta original sin modificar nada.

    Args:
        ruta_entrada: Path del PDF a comprimir
        ruta_salida: Path del PDF comprimido (por defecto sobreescribe el original)
        calidad: Nivel de compresión ('screen', 'ebook', 'printer', 'prepress')

    Returns:
        Path del PDF (comprimido o el original si la compresión está desactivada)
    """
    ruta_entrada = Path(ruta_entrada)

    # Si la compresión está desactivada, devolver el archivo tal cual
    if not COMPRIMIR_PDF:
        logger.debug("Compresión desactivada (COMPRIMIR_PDF=false)")
        return ruta_entrada

    # Detectar Ghostscript
    gs = _detectar_ghostscript()
    if not gs:
        logger.warning("Ghostscript no instalado — devolviendo PDF sin comprimir")
        return ruta_entrada

    calidad = calidad or COMPRESION_CALIDAD
    if ruta_salida is None:
        # Comprimir in-place: escribir a un archivo temporal y reemplazar
        ruta_salida = ruta_entrada.with_suffix('.compressed.pdf')

    tamaño_original = ruta_entrada.stat().st_size

    # Comando de Ghostscript para comprimir
    comando = [
        gs,
        '-sDEVICE=pdfwrite',               # Reescribir como PDF
        f'-dPDFSETTINGS=/{calidad}',        # Nivel de compresión
        '-dNOPAUSE',                         # No pausar entre páginas
        '-dBATCH',                           # Salir al terminar
        '-dQUIET',                           # Sin output verboso
        '-dCompatibilityLevel=1.4',          # PDF 1.4 (compatible)
        f'-sOutputFile={ruta_salida}',       # Archivo de salida
        str(ruta_entrada)                    # Archivo de entrada
    ]

    try:
        resultado = subprocess.run(
            comando, capture_output=True, text=True, timeout=120
        )

        if resultado.returncode != 0:
            logger.error(f"Ghostscript falló: {resultado.stderr[:200]}")
            return ruta_entrada  # Devolver original si falla

        tamaño_comprimido = ruta_salida.stat().st_size
        reduccion = (1 - tamaño_comprimido / tamaño_original) * 100

        # Si la compresión no redujo o el resultado es más grande, usar el original
        if tamaño_comprimido >= tamaño_original:
            logger.info(f"Compresión no efectiva ({reduccion:.0f}%), usando original")
            ruta_salida.unlink()
            return ruta_entrada

        logger.info(
            f"PDF comprimido: {tamaño_original / 1024:.0f}KB → "
            f"{tamaño_comprimido / 1024:.0f}KB ({reduccion:.0f}% menos)"
        )

        # Si comprimimos in-place, reemplazar el original
        if ruta_salida.name.endswith('.compressed.pdf'):
            ruta_entrada.unlink()
            ruta_salida.rename(ruta_entrada)
            return ruta_entrada

        return ruta_salida

    except subprocess.TimeoutExpired:
        logger.error("Compresión abortada: timeout de 120 segundos")
        if ruta_salida.exists():
            ruta_salida.unlink()
        return ruta_entrada

    except Exception as e:
        logger.error(f"Error al comprimir PDF: {e}")
        return ruta_entrada
