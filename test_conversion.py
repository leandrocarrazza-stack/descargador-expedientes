#!/usr/bin/env python3
"""
Test de la conversión "ya es PDF, movido" en modulos/conversion.py.

Investigando un reporte de descarga lenta se encontró que convertir_rtf_a_pdf()
tenía un time.sleep(0.5) incondicional para CADA archivo que ya venía como PDF
(la mayoría, en un expediente típico), más un shutil.copy(x, x) que fallaba
con SameFileError el 100% de las veces y sólo "andaba" gracias al fallback de
rename(x, x) — un no-op disfrazado de operación real. En un expediente de 116
archivos donde ~94 ya eran PDF, eso eran ~47 segundos tirados a la basura.

Este test no toca la red ni Selenium: corre en cualquier lado con
`python test_conversion.py`.
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from modulos.conversion import ConversorRTF

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def test_archivo_ya_pdf_no_espera_ni_copia():
    print("\n[1] Archivo ya-PDF: sin sleep, sin copy(x, x)")

    carpeta = Path(tempfile.mkdtemp())
    archivo = carpeta / "0001_pag01.pdf"
    archivo.write_bytes(b"%PDF-1.4 contenido de prueba")
    mtime_original = archivo.stat().st_mtime_ns

    conversor = ConversorRTF()

    with patch("modulos.conversion.shutil.copy") as copy_espiado:
        inicio = time.monotonic()
        resultado = conversor.convertir_rtf_a_pdf(archivo)
        duracion = time.monotonic() - inicio

        check("no llama a shutil.copy (ruta_pdf == ruta_rtf, no hay nada que copiar)",
              copy_espiado.call_count == 0,
              f"se llamó {copy_espiado.call_count} vez(veces)")

    check("devuelve la misma ruta, sin tocar el archivo", resultado == archivo)
    check("termina en milisegundos, no hay time.sleep(0.5) de por medio",
          duracion < 0.1, f"tardó {duracion*1000:.1f}ms")
    check("el archivo no fue tocado (mismo mtime: no se copió ni renombró)",
          archivo.stat().st_mtime_ns == mtime_original)
    check("el contenido sigue intacto", archivo.read_bytes() == b"%PDF-1.4 contenido de prueba")

    shutil.rmtree(carpeta, ignore_errors=True)


def test_cien_archivos_ya_pdf_es_rapido():
    """
    Reproduce a escala chica el caso real: un lote de archivos que ya vienen
    en PDF. Antes del fix, esto solo (sin contar la conversión RTF real)
    tardaba N * 0.5s = 50s para 100 archivos. Ahora debe ser prácticamente
    instantáneo.
    """
    print("\n[2] Un lote de 100 archivos 'ya PDF' no acumula medio segundo cada uno")

    carpeta = Path(tempfile.mkdtemp())
    archivos = []
    for i in range(100):
        f = carpeta / f"{i:04d}_pag01.pdf"
        f.write_bytes(b"%PDF-1.4 x")
        archivos.append(f)

    conversor = ConversorRTF()

    inicio = time.monotonic()
    for archivo in archivos:
        conversor.convertir_rtf_a_pdf(archivo)
    duracion = time.monotonic() - inicio

    check("100 archivos 'ya PDF' se procesan en menos de 1 segundo en total "
          "(antes del fix: ~50s sólo de sleeps)",
          duracion < 1.0, f"tardó {duracion:.2f}s")

    shutil.rmtree(carpeta, ignore_errors=True)


def test_archivo_rtf_sigue_intentando_convertir():
    """
    No debe tocar el camino de los RTF reales: si LibreOffice no está
    disponible en este entorno, tiene que fallar de forma controlada (return
    None), no explotar.
    """
    print("\n[3] Un .rtf de verdad sigue yendo por el camino de conversión")

    carpeta = Path(tempfile.mkdtemp())
    archivo = carpeta / "0002_pag01.rtf"
    archivo.write_bytes(b"{\\rtf1 contenido de prueba}")

    conversor = ConversorRTF()
    resultado = conversor.convertir_rtf_a_pdf(archivo)

    if conversor.disponible:
        check("con LibreOffice disponible, intenta convertir de verdad",
              True, f"resultado={resultado}")
    else:
        check("sin LibreOffice disponible, falla de forma controlada (None, no excepción)",
              resultado is None)

    shutil.rmtree(carpeta, ignore_errors=True)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE CONVERSIÓN RTF/PDF")
    print("=" * 70)

    test_archivo_ya_pdf_no_espera_ni_copia()
    test_cien_archivos_ya_pdf_es_rapido()
    test_archivo_rtf_sigue_intentando_convertir()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
