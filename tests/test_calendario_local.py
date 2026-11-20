#!/usr/bin/env python3
"""
Tests de calendario_actualizado() (modulos/plazos/calendario_local.py): el calendario
del plugin que se actualiza solo. Sin red (descarga falsa con las copias reales de
tests/fixtures/plazos/) y con un reloj inyectado. `python tests/test_calendario_local.py`
"""

import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.plazos import exportacion, sync_local
from modulos.plazos import sync_stjer as s
from modulos.plazos.calculo import calcular
from modulos.plazos.calendario_local import calendario_actualizado, REINTENTO_HORAS
from modulos.plazos.reglas import Entrada

FIX = Path(__file__).parent / 'fixtures' / 'plazos'
HTML_2026 = (FIX / 'pagina_inhabiles_2026.html').read_text(encoding='utf-8')
RSS = (FIX / 'noticias_inhabiles.xml').read_text(encoding='utf-8')
FERIA = '<html><body><p>La feria judicial de invierno tendrá lugar del 6 al 17 de julio de 2026.</p></body></html>'
AHORA = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


class _Http404(Exception):
    def __init__(self):
        super().__init__('404')
        self.response = type('R', (), {'status_code': 404})()


def _sitio(caido=False):
    """Descarga falsa del sitio oficial; cuenta los pedidos. `caido`: no hay internet."""
    def descargar(url):
        descargar.pedidos += 1
        if caido:
            raise OSError('sin conexión')
        if url == s.URL_PAGINA:
            return HTML_2026
        if url == s.URL_FERIA:
            return FERIA
        if url == s.URL_FEED or 'paged=1' in url:
            return RSS
        raise _Http404()
    descargar.pedidos = 0
    return descargar


def _guardar_archivo(ruta, generado, pagina='ok'):
    """Deja un calendario viejo con una sola entrada reconocible (la feria de julio)."""
    datos = exportacion.armar_exportacion(
        [Entrada(date(2026, 7, 6), date(2026, 7, 17), 'feria_julio', 'Feria judicial de julio')],
        {'pagina': {'fecha': '2026-01-01T00:00:00Z', 'resultado': pagina}}, generado=generado)
    exportacion.guardar_atomico(datos, ruta)


def _llamar(ruta, descargar, ahora=AHORA):
    return calendario_actualizado(ruta, descargar=descargar, ahora=ahora, pausa=0)


def test_sin_archivo_lo_baja_solo():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        d = _sitio()
        cal, est = _llamar(ruta, d)
        check("sin archivo: lo crea leyendo el sitio y lo usa", est['origen'] == 'actualizado' and Path(ruta).exists()
              and d.pedidos > 0, est)
        check("sin avisos cuando todo salió bien", est['aviso'] is None, est['aviso'])
        check("el calendario trae la feria de julio", cal.tiene_feria_julio(2026))
        r = calcular(cal, tipo_notificacion='cedula', fecha=date(2026, 7, 1), dias=10)
        check("y el cálculo la descuenta (vence el 29/7)", r.vencimiento == date(2026, 7, 29), r.vencimiento)


def test_archivo_fresco_no_toca_internet():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        _guardar_archivo(ruta, AHORA - timedelta(days=3))
        d = _sitio()
        cal, est = _llamar(ruta, d)
        check("con 3 días de antigüedad usa el archivo y no pide nada", est['origen'] == 'archivo' and d.pedidos == 0
              and est['dias'] == 3 and est['aviso'] is None, est)
        _guardar_archivo(ruta, AHORA - timedelta(days=15))
        cal, est = _llamar(ruta, d)
        check("con exactamente 15 días todavía no actualiza", d.pedidos == 0 and est['origen'] == 'archivo')


def test_archivo_vencido_se_actualiza():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        _guardar_archivo(ruta, AHORA - timedelta(days=16))
        d = _sitio()
        cal, est = _llamar(ruta, d)
        check("con 16 días actualiza", est['origen'] == 'actualizado' and d.pedidos > 0 and est['dias'] == 0, est)
        check("el archivo quedó renovado", exportacion.dias_de_antiguedad(exportacion.cargar_archivo(ruta), AHORA) == 0)
        d2 = _sitio()
        _llamar(ruta, d2)
        check("y la siguiente llamada ya no pide nada", d2.pedidos == 0)


def test_si_la_ultima_lectura_de_la_pagina_fallo_reintenta_aunque_sea_reciente():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        _guardar_archivo(ruta, AHORA - timedelta(days=1), pagina='rechazado')
        d = _sitio()
        cal, est = _llamar(ruta, d)
        check("un archivo reciente pero sin la página de feriados se vuelve a intentar", d.pedidos > 0
              and est['origen'] == 'actualizado', est)


def test_sin_internet_conserva_la_copia_y_no_insiste():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        _guardar_archivo(ruta, AHORA - timedelta(days=20))
        antes = Path(ruta).read_text(encoding='utf-8')
        d = _sitio(caido=True)
        cal, est = _llamar(ruta, d)
        check("sin internet sigue con la última copia", est['origen'] == 'archivo' and est['dias'] == 20, est)
        check("y avisa que no pudo actualizar y de cuántos días es la copia",
              est['aviso'] and 'No se pudo actualizar' in est['aviso'] and '20 días' in est['aviso'], est['aviso'])
        check("no pisa el archivo", Path(ruta).read_text(encoding='utf-8') == antes)
        pedidos = d.pedidos
        cal, est = _llamar(ruta, d, ahora=AHORA + timedelta(hours=REINTENTO_HORAS - 1))
        check("no vuelve a intentar antes de las 6 horas (no demora cada arranque)", d.pedidos == pedidos, d.pedidos)
        check("pero sigue avisando que el calendario está viejo", est['aviso'] and '20 días' in est['aviso'], est['aviso'])
        cal, est = _llamar(ruta, d, ahora=AHORA + timedelta(hours=REINTENTO_HORAS + 1))
        check("pasadas las 6 horas vuelve a intentar", d.pedidos > pedidos)
        d_ok = _sitio()
        cal, est = _llamar(ruta, d_ok, ahora=AHORA + timedelta(hours=2 * REINTENTO_HORAS + 2))
        check("cuando vuelve internet se recupera sola", est['origen'] == 'actualizado' and est['aviso'] is None, est)


def test_sin_archivo_y_sin_internet_solo_reglas():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        cal, est = _llamar(ruta, _sitio(caido=True))
        check("sin nada: calcula solo con las reglas fijas", est['origen'] == 'solo_reglas' and est['dias'] is None, est)
        check("y avisa con claridad qué falta", est['aviso'] and 'feria de julio' in est['aviso']
              and 'feriados de cada ciudad' in est['aviso'], est['aviso'])
        check("no se crea ningún archivo a medias", not Path(ruta).exists())
        check("el calendario sigue funcionando con las reglas", not cal.es_habil(date(2026, 12, 25)))


def test_archivo_roto_se_reemplaza():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        Path(ruta).write_text('{esto no es json', encoding='utf-8')
        cal, est = _llamar(ruta, _sitio())
        check("un archivo dañado se trata como inexistente y se repara", est['origen'] == 'actualizado'
              and exportacion.cargar_archivo(ruta)['formato'] == exportacion.FORMATO)
        Path(ruta).write_text(json.dumps({'formato': 99, 'entradas': []}), encoding='utf-8')
        cal, est = _llamar(ruta, _sitio(), ahora=AHORA + timedelta(hours=REINTENTO_HORAS + 1))
        check("un archivo de un formato que no entiende también se reemplaza", est['origen'] == 'actualizado')


def test_falta_una_libreria():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        _guardar_archivo(ruta, AHORA - timedelta(days=30))
        real = sync_local._sync

        def sin_bs4():
            raise exportacion.ErrorExportacion('Falta una librería (bs4). Instalala con:  pip install beautifulsoup4 requests')
        sync_local._sync = sin_bs4
        try:
            cal, est = _llamar(ruta, _sitio())
        finally:
            sync_local._sync = real
        check("si falta una librería avisa cómo instalarla y sigue con la copia",
              est['origen'] == 'archivo' and 'pip install' in est['aviso'], est['aviso'])


def test_falla_parcial_avisa():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = str(Path(tmp) / 'cal.json')
        base = _sitio()

        def sin_rss(url):
            if url == s.URL_FEED:
                raise OSError('caído')
            return base(url)
        cal, est = _llamar(ruta, sin_rss)
        check("si falla solo una fuente guarda lo que pudo y lo avisa", est['origen'] == 'actualizado'
              and est['aviso'] and 'en parte' in est['aviso'], est['aviso'])


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DEL CALENDARIO QUE SE ACTUALIZA SOLO")
    print("=" * 70)
    test_sin_archivo_lo_baja_solo()
    test_archivo_fresco_no_toca_internet()
    test_archivo_vencido_se_actualiza()
    test_si_la_ultima_lectura_de_la_pagina_fallo_reintenta_aunque_sea_reciente()
    test_sin_internet_conserva_la_copia_y_no_insiste()
    test_sin_archivo_y_sin_internet_solo_reglas()
    test_archivo_roto_se_reemplaza()
    test_falta_una_libreria()
    test_falla_parcial_avisa()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
