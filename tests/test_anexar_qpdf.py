#!/usr/bin/env python3
"""
Tests de anexar_con_qpdf() (lote 5): merge del PDF anterior + el delta de
una actualización incremental. Si qpdf no está instalado en este entorno,
se salta con aviso (se sigue probando el fallback con PyPDF2 en memoria,
que no depende de ningún binario externo). `python test_anexar_qpdf.py`.
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from PyPDF2 import PdfWriter, PdfReader
import modulos.unificacion as unificacion_mod
from modulos.unificacion import anexar_con_qpdf, ErrorUnificacion

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _crear_pdf(ruta: Path, paginas: int):
    w = PdfWriter()
    for _ in range(paginas):
        w.add_blank_page(width=200, height=200)
    with open(ruta, 'wb') as f:
        w.write(f)


def test_anexar_con_qpdf_real():
    if not shutil.which('qpdf'):
        print("\n[SKIP] qpdf no está instalado en este entorno — se salta este test"
              " (el Dockerfile de producción sí lo instala)")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base, delta, salida = tmp / 'base.pdf', tmp / 'delta.pdf', tmp / 'salida.pdf'
        _crear_pdf(base, 2)
        _crear_pdf(delta, 3)

        resultado = anexar_con_qpdf(base, delta, salida)
        check("devuelve la ruta de salida", resultado == salida)

        paginas = len(PdfReader(str(resultado)).pages)
        check("el PDF resultante tiene base+delta páginas (2+3=5)", paginas == 5, paginas)


def test_fallback_sin_qpdf_pdf_chico():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base, delta, salida = tmp / 'base.pdf', tmp / 'delta.pdf', tmp / 'salida.pdf'
        _crear_pdf(base, 1)
        _crear_pdf(delta, 1)

        with mock.patch.object(unificacion_mod.shutil, 'which', return_value=None):
            resultado = anexar_con_qpdf(base, delta, salida)

        paginas = len(PdfReader(str(resultado)).pages)
        check("fallback sin qpdf (PDF chico) igual funciona", paginas == 2, paginas)


def test_fallback_sin_qpdf_rechaza_pdf_grande():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base, delta, salida = tmp / 'base.pdf', tmp / 'delta.pdf', tmp / 'salida.pdf'
        _crear_pdf(base, 1)
        _crear_pdf(delta, 1)

        with mock.patch.object(unificacion_mod.shutil, 'which', return_value=None), \
             mock.patch.object(Path, 'stat') as mock_stat:
            mock_stat.return_value.st_size = 20 * 1024 * 1024  # 20 MB > límite de 15 MB
            lanzo = False
            try:
                anexar_con_qpdf(base, delta, salida)
            except ErrorUnificacion:
                lanzo = True
        check("rechaza con ErrorUnificacion en vez de arriesgar RAM", lanzo)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE anexar_con_qpdf (LOTE 5)")
    print("=" * 70)

    test_anexar_con_qpdf_real()
    test_fallback_sin_qpdf_pdf_chico()
    test_fallback_sin_qpdf_rechaza_pdf_grande()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
