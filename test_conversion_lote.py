#!/usr/bin/env python3
"""
Tests de ConversorRTF.convertir_lote() en modulos/conversion.py.

Investigando la lentitud de la descarga se encontró que la conversión RTF>PDF
hacía un subprocess.run + sleep(1) POR ARCHIVO: en un expediente con muchos
RTF, eso paga el arranque en frío de LibreOffice (~1.5-3s) una y otra vez.
convertir_lote() agrupa varios RTF en UNA sola invocación de soffice.

No usa LibreOffice real (sería lento y frágil depender de su comportamiento
exacto): en su lugar arranca un "soffice" de mentira -un script Python con
shebang, ejecutable directo- que simula la conversión escribiendo los PDFs
esperados en --outdir, y que a propósito se salta cualquier archivo cuyo
nombre contenga "CORROMPIDO", para poder probar el camino de reintento
individual sin depender de que un RTF real falle de una manera particular.

Corre en cualquier lado con `python test_conversion_lote.py`.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from modulos.conversion import ConversorRTF, parece_pdf

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


_STUB_TEMPLATE = '''#!{shebang}
import sys
from pathlib import Path

argv = sys.argv[1:]
outdir = None
entradas = []
i = 0
while i < len(argv):
    a = argv[i]
    if a == '--outdir':
        outdir = Path(argv[i + 1])
        i += 2
        continue
    if a.endswith('.rtf'):
        entradas.append(Path(a))
    i += 1

for entrada in entradas:
    if 'CORROMPIDO' in entrada.name:
        continue  # simula una conversión que falla para ESTE archivo
    destino = outdir / (entrada.stem + '.pdf')
    # Relleno para superar el mínimo de 500 bytes que exige convertir_lote()
    # (el mismo umbral que usa el camino individual para descartar PDFs
    # truncados) -- si no, el propio stub dispararía el camino de reintento.
    destino.write_bytes(b'%PDF-1.4 stub de test\\n' + b'x' * 600 + b'\\n%%EOF')
'''


def _crear_stub_soffice(carpeta: Path) -> Path:
    """Un 'soffice' de mentira: escribe los .pdf esperados, salta los CORROMPIDO."""
    stub = carpeta / "soffice_stub.py"
    stub.write_text(_STUB_TEMPLATE.format(shebang=sys.executable))
    stub.chmod(0o755)
    return stub


def _conversor_con_stub(carpeta: Path, perfil_dir=None) -> ConversorRTF:
    conversor = ConversorRTF(perfil_dir=perfil_dir)
    conversor.libreoffice_path = str(_crear_stub_soffice(carpeta))
    conversor.disponible = True
    return conversor


def _rtf(carpeta: Path, nombre: str) -> Path:
    ruta = carpeta / nombre
    ruta.write_bytes(b"{\\rtf1 contenido de prueba}")
    return ruta


# ═══════════════════════════════════════════════════════════════════════════

def test_convertir_lote_una_sola_invocacion():
    print("\n[1] Un lote de 5 RTF se convierte con UNA sola invocación de soffice")
    carpeta = Path(tempfile.mkdtemp())
    conversor = _conversor_con_stub(carpeta)
    rutas = [_rtf(carpeta, f"{i:04d}_pag01.rtf") for i in range(5)]

    with patch("modulos.conversion.subprocess.run", wraps=subprocess.run) as espia:
        resultado = conversor.convertir_lote(rutas, carpeta)
        check("subprocess.run se llamó una sola vez para todo el lote",
              espia.call_count == 1, f"se llamó {espia.call_count} vez(veces)")

    check("los 5 archivos tienen un PDF válido",
          all(resultado[r] and resultado[r].exists() for r in rutas),
          f"resultado={resultado}")
    check("cada PDF generado tiene el magic byte correcto",
          all(parece_pdf(resultado[r]) for r in rutas))

    shutil.rmtree(carpeta, ignore_errors=True)


def test_convertir_lote_reintenta_el_que_falta():
    print("\n[2] Un archivo que el lote no genera se reintenta individualmente")
    carpeta = Path(tempfile.mkdtemp())
    conversor = _conversor_con_stub(carpeta)
    buenos = [_rtf(carpeta, f"{i:04d}_pag01.rtf") for i in range(3)]
    corrompido = _rtf(carpeta, "0099_CORROMPIDO.rtf")
    rutas = buenos + [corrompido]

    with patch("modulos.conversion.subprocess.run", wraps=subprocess.run) as espia:
        resultado = conversor.convertir_lote(rutas, carpeta)
        check("se llamó 2 veces: 1 el lote + 1 el reintento individual",
              espia.call_count == 2, f"se llamó {espia.call_count} vez(veces)")

    check("los 3 archivos buenos del lote se convirtieron igual",
          all(resultado[r] and resultado[r].exists() for r in buenos))
    check("el archivo realmente corrompido queda en None tras el reintento "
          "(no rompe el resto del lote)",
          resultado[corrompido] is None, f"resultado={resultado[corrompido]}")

    shutil.rmtree(carpeta, ignore_errors=True)


def test_convertir_lote_vacio():
    print("\n[3] Un lote vacío no toca subprocess ni explota")
    carpeta = Path(tempfile.mkdtemp())
    conversor = _conversor_con_stub(carpeta)

    with patch("modulos.conversion.subprocess.run", wraps=subprocess.run) as espia:
        resultado = conversor.convertir_lote([], carpeta)
        check("no se llama a subprocess.run con una lista vacía", espia.call_count == 0)
    check("devuelve un dict vacío", resultado == {})

    shutil.rmtree(carpeta, ignore_errors=True)


def test_convertir_lote_sin_libreoffice():
    print("\n[4] Sin LibreOffice disponible, todos quedan en None sin tocar subprocess")
    carpeta = Path(tempfile.mkdtemp())
    conversor = ConversorRTF()
    conversor.disponible = False
    rutas = [_rtf(carpeta, "0001_pag01.rtf")]

    with patch("modulos.conversion.subprocess.run", wraps=subprocess.run) as espia:
        resultado = conversor.convertir_lote(rutas, carpeta)
        check("no se intenta ejecutar soffice si no está disponible", espia.call_count == 0)
    check("el archivo queda en None", resultado[rutas[0]] is None)

    shutil.rmtree(carpeta, ignore_errors=True)


def test_convertir_lote_usa_perfil_propio():
    print("\n[5] Con perfil_dir, el comando incluye -env:UserInstallation")
    carpeta = Path(tempfile.mkdtemp())
    perfil = carpeta / "lo_perfil"
    conversor = _conversor_con_stub(carpeta, perfil_dir=perfil)
    rutas = [_rtf(carpeta, "0001_pag01.rtf")]

    with patch("modulos.conversion.subprocess.run", wraps=subprocess.run) as espia:
        conversor.convertir_lote(rutas, carpeta)
        comando = espia.call_args[0][0]
        tiene_flag = any(str(a).startswith("-env:UserInstallation=") for a in comando)
        check("el comando lleva -env:UserInstallation con el perfil propio",
              tiene_flag, f"comando={comando}")
    check("se creó la carpeta del perfil", perfil.exists())

    shutil.rmtree(carpeta, ignore_errors=True)


def test_convertir_lote_timeout_no_propaga():
    print("\n[6] Un timeout del lote no lanza una excepción sin atrapar")
    carpeta = Path(tempfile.mkdtemp())
    conversor = _conversor_con_stub(carpeta)
    rtf = _rtf(carpeta, "0001_pag01.rtf")

    with patch("modulos.conversion.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="soffice", timeout=80)):
        try:
            resultado = conversor.convertir_lote([rtf], carpeta)
            ok = True
        except Exception as e:
            ok = False
            print(f"      excepción inesperada: {e}")

    check("convertir_lote no propaga el TimeoutExpired", ok)
    if ok:
        check("el archivo que expiró queda en None (nada que reintentar lo salva)",
              resultado.get(rtf) is None, f"resultado={resultado}")

    shutil.rmtree(carpeta, ignore_errors=True)


def test_parece_pdf():
    print("\n[7] parece_pdf(): por extensión, por magic bytes, y archivo inexistente")
    carpeta = Path(tempfile.mkdtemp())

    pdf_por_extension = carpeta / "a.pdf"
    pdf_por_extension.write_bytes(b"contenido cualquiera")
    check("un .pdf cuenta como PDF aunque el contenido no lo sea (por extensión)",
          parece_pdf(pdf_por_extension) is True)

    pdf_por_contenido = carpeta / "b.bin"
    pdf_por_contenido.write_bytes(b"%PDF-1.4 x")
    check("magic bytes %PDF sin extensión .pdf también cuenta",
          parece_pdf(pdf_por_contenido) is True)

    rtf = carpeta / "c.rtf"
    rtf.write_bytes(b"{\\rtf1 x}")
    check("un .rtf real no cuenta como PDF", parece_pdf(rtf) is False)

    # Extensión .pdf: la función confía en la extensión y ni intenta abrirlo,
    # el mismo comportamiento que tenía el chequeo original en
    # convertir_rtf_a_pdf() (que además nunca llega a llamar acá con un
    # archivo inexistente: valida existencia antes). Para el caso donde SÍ
    # hace falta abrir el archivo (sin extensión .pdf, por magic bytes), un
    # inexistente no debe explotar:
    check("archivo inexistente sin extensión .pdf: no lanza, devuelve False",
          parece_pdf(carpeta / "no-existe.bin") is False)

    shutil.rmtree(carpeta, ignore_errors=True)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE CONVERSIÓN POR LOTES (convertir_lote)")
    print("=" * 70)

    test_convertir_lote_una_sola_invocacion()
    test_convertir_lote_reintenta_el_que_falta()
    test_convertir_lote_vacio()
    test_convertir_lote_sin_libreoffice()
    test_convertir_lote_usa_perfil_propio()
    test_convertir_lote_timeout_no_propaga()
    test_parece_pdf()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
