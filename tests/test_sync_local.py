#!/usr/bin/env python3
"""
Tests de la actualización local del calendario (modulos/plazos/sync_local.py).
Sin red: se inyecta una descarga falsa con las copias reales guardadas en
tests/fixtures/plazos/. `python tests/test_sync_local.py`

Lo más importante: lo que arma el script local es lo mismo que deja el
sincronizador del servidor al leer las mismas fuentes.
"""

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor

from modulos.database import db
from modulos.models import DiaInhabil
from modulos.plazos import exportacion, sync_local
from modulos.plazos import sync_stjer as s
from servidor import crear_app

FIX = Path(__file__).parent / 'fixtures' / 'plazos'
HTML_2026 = (FIX / 'pagina_inhabiles_2026.html').read_text(encoding='utf-8')
RSS = (FIX / 'noticias_inhabiles.xml').read_text(encoding='utf-8')
# Una noticia que el interpretador no puede resolver con certeza (anulación de un inhábil): no debe aplicarse
NOTICIA_AMBIGUA = (
    '<item><title>Queda sin efecto el inhábil judicial del viernes 9 de octubre</title>'
    '<link>https://www.jusentrerios.gov.ar/2026/10/queda-sin-efecto-el-inhabil/</link>'
    '<pubDate>Mon, 05 Oct 2026 12:00:00 +0000</pubDate>'
    '<content:encoded><![CDATA[<p>El Superior Tribunal dejó sin efecto el inhábil judicial dispuesto para el viernes '
    '9 de octubre. Los plazos corren con normalidad.</p>]]></content:encoded></item>')
RSS = RSS.replace('<item>', NOTICIA_AMBIGUA + '<item>', 1)
FERIA = '<html><body><p>La feria judicial de invierno tendrá lugar del 6 al 17 de julio de 2026.</p></body></html>'
HOY = date(2026, 10, 8)

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


class _Http404(Exception):
    """Imita requests.HTTPError con respuesta 404 (WordPress al terminarse las páginas)."""
    def __init__(self):
        super().__init__('404')
        self.response = type('R', (), {'status_code': 404})()


def _sitio(pagina=HTML_2026, feria=FERIA, feed=RSS, caidos=()):
    """Descarga falsa del sitio oficial. `caidos`: nombres de fuentes que fallan."""
    pedidos = []

    def descargar(url):
        pedidos.append(url)
        if url == s.URL_PAGINA:
            if 'pagina' in caidos:
                raise s.ErrorSync('caída')
            return pagina
        if url == s.URL_FERIA:
            if 'feria' in caidos:
                raise s.ErrorSync('caída')
            return feria
        if url == s.URL_FEED:
            if 'rss' in caidos:
                raise s.ErrorSync('caída')
            return feed
        if 'paged=1' in url:
            return feed
        raise _Http404()   # páginas 2 en adelante de la búsqueda
    descargar.pedidos = pedidos
    return descargar


def _firmas(entradas):
    return {sync_local._firma(e) for e in entradas}


def test_arma_el_calendario_desde_las_tres_fuentes():
    datos, informe = sync_local.construir_exportacion(hoy=HOY, descargar=_sitio(), pausa=0)
    cal, problemas = exportacion.calendario_desde_exportacion(datos)
    check("el resultado se puede leer con el lector del plugin", problemas == [], problemas)
    check("las tres fuentes dan ok", {k: v['resultado'] for k, v in informe['fuentes'].items()}
          == {'pagina': 'ok', 'feria': 'ok', 'rss': 'ok'}, informe['fuentes'])
    check("trae la feria de julio con el link a la página oficial",
          any(e.tipo == 'feria_julio' and (e.desde, e.hasta) == (date(2026, 7, 6), date(2026, 7, 17))
              and e.fuente_url == s.URL_FERIA for e in cal.entradas_del_dia(date(2026, 7, 6))))
    check("trae filas de la página de feriados (locales y generales)", len(datos['entradas']) > 50, len(datos['entradas']))
    check("trae las noticias aplicadas, con link oficial",
          any((e.fuente_url or '').startswith('https://www.jusentrerios.gov.ar/20') for e in
              (e for d in (date(2026, 12, 26), date(2026, 9, 15)) for e in cal.entradas_del_dia(d))))
    check("el estado de cada fuente viaja en el archivo", set(datos['ultima_sincronizacion']) == {'pagina', 'feria', 'rss'})
    check("lista aparte lo que no pudo interpretar", isinstance(datos['para_revisar'], list))


def test_igual_que_el_servidor():
    app = crear_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.session.query(DiaInhabil).delete()
        s.sincronizar_pagina(db, hoy=HOY, html=HTML_2026)
        s.sincronizar_feria(db, html=FERIA)
        s.sincronizar_rss(db, hoy=HOY, xmls=[RSS])
        del_servidor = _firmas(f.a_entrada() for f in DiaInhabil.query.filter_by(activo=True).all())
        revisar_servidor = {f.fuente_url for f in DiaInhabil.query.filter_by(activo=False).all()}
    # El servidor, la primera vez, mira 800 días de noticias; con la misma ventana tienen que coincidir
    datos, informe = sync_local.construir_exportacion(hoy=HOY, descargar=_sitio(), pausa=0, dias_rss=s.DIAS_RSS_PRIMERA_VEZ)
    cal, _ = exportacion.calendario_desde_exportacion(datos)
    locales = _firmas(e for d in datos['entradas'] for e in [exportacion.entrada_desde_dict(d)])
    check("mismas entradas activas que el sincronizador del servidor", locales == del_servidor,
          f"solo local: {len(locales - del_servidor)} · solo servidor: {len(del_servidor - locales)}")
    check("deja para revisar las mismas noticias ambiguas que el servidor (y hay al menos una)",
          {n['link'] for n in informe['para_revisar']} == revisar_servidor and len(revisar_servidor) >= 1,
          f"{len(informe['para_revisar'])} vs {len(revisar_servidor)}")
    check("la noticia ambigua no descuenta ningún día", all(
        e['fuente_url'] != 'https://www.jusentrerios.gov.ar/2026/10/queda-sin-efecto-el-inhabil/' for e in datos['entradas']))
    check("el cálculo del 9/10 sigue siendo hábil (no se aplicó la anulación a ciegas)", cal.es_habil(date(2026, 10, 9)))


def test_pide_poco_y_con_pausa():
    d = _sitio()
    sync_local.construir_exportacion(hoy=HOY, descargar=d, pausa=0)
    check("solo pide URLs del sitio oficial", all(u.startswith('https://www.jusentrerios.gov.ar/') for u in d.pedidos))
    check("pagina, feria, feed y dos consultas de noticias (corta al primer 404)", len(d.pedidos) == 3 + 2 * 2, len(d.pedidos))
    pausas = []
    real = sync_local.time.sleep
    sync_local.time.sleep = pausas.append
    try:
        sync_local.construir_exportacion(hoy=HOY, descargar=_sitio(), pausa=0.5)
    finally:
        sync_local.time.sleep = real
    check("espera entre pedidos para no abusar del sitio", len(pausas) == 6 and set(pausas) == {0.5}, pausas)


def test_corta_cuando_las_noticias_ya_son_mas_viejas_que_la_ventana():
    d = _sitio()
    sync_local.construir_exportacion(hoy=date(2027, 12, 1), descargar=d, pausa=0)   # todo lo del feed tiene > 400 días
    check("si la primera página ya es más vieja que la ventana, no pide más (el buscador es lento)",
          len(d.pedidos) == 3 + 2, len(d.pedidos))
    d2 = _sitio()
    sync_local.construir_exportacion(hoy=HOY, descargar=d2, pausa=0, dias_rss=800)
    check("con noticias dentro de la ventana sigue hasta que se terminan las páginas", len(d2.pedidos) == 3 + 2 * 2, len(d2.pedidos))
    avisos = []
    sync_local.construir_exportacion(hoy=HOY, descargar=_sitio(), pausa=0, avisar=avisos.append)
    check("avisa qué está leyendo antes de cada pedido", len(avisos) == 7 and all('jusentrerios.gov.ar' in a for a in avisos), avisos[:2])


def test_guardado_y_codigos():
    with tempfile.TemporaryDirectory() as tmp:
        destino = str(Path(tmp) / 'cal.json')

        r = sync_local.actualizar_calendario_local(destino, descargar=_sitio(), hoy=HOY, pausa=0)
        check("todo bien: guarda y código 0", r['codigo'] == 0 and r['guardado'] and Path(destino).exists())
        bueno = Path(destino).read_text(encoding='utf-8')
        check("el archivo guardado es un calendario válido",
              exportacion.calendario_desde_exportacion(json.loads(bueno))[1] == [])

        r = sync_local.actualizar_calendario_local(destino, descargar=_sitio(caidos=('pagina',)), hoy=HOY, pausa=0)
        check("si la página de feriados falla y había archivo, lo conserva (código 1)",
              r['codigo'] == 1 and not r['guardado'] and Path(destino).read_text(encoding='utf-8') == bueno)

        r = sync_local.actualizar_calendario_local(destino, descargar=_sitio(caidos=('rss',)), hoy=HOY, pausa=0)
        check("si falla solo el RSS guarda lo que pudo y avisa (código 2)",
              r['codigo'] == 2 and r['guardado'] and r['datos']['ultima_sincronizacion']['rss']['resultado'] == 'error')

        r = sync_local.actualizar_calendario_local(destino, descargar=_sitio(caidos=('pagina', 'feria', 'rss')), hoy=HOY, pausa=0)
        check("sin internet no pisa el archivo anterior", r['codigo'] == 1 and not r['guardado'])

        vacio = str(Path(tmp) / 'vacio.json')
        r = sync_local.actualizar_calendario_local(vacio, descargar=_sitio(caidos=('pagina', 'feria', 'rss')), hoy=HOY, pausa=0)
        check("sin internet y sin archivo previo no crea un calendario vacío", r['codigo'] == 1 and not Path(vacio).exists()
              and 'ninguna fuente' in r['motivo'], r['motivo'])

        nuevo = str(Path(tmp) / 'nuevo.json')
        r = sync_local.actualizar_calendario_local(nuevo, descargar=_sitio(caidos=('pagina',)), hoy=HOY, pausa=0)
        check("sin archivo anterior y con la página caída guarda lo que hay y avisa (código 2)",
              r['codigo'] == 2 and Path(nuevo).exists()
              and r['datos']['ultima_sincronizacion']['pagina']['resultado'] == 'rechazado')

        r = sync_local.actualizar_calendario_local(str(Path(tmp) / 'otro.json'),
                                                   descargar=_sitio(pagina='<html></html>'), hoy=HOY, pausa=0)
        check("una página que cambió de formato se rechaza, no se interpreta a ciegas",
              r['datos']['ultima_sincronizacion']['pagina']['resultado'] == 'rechazado')

        r = sync_local.actualizar_calendario_local(str(Path(tmp) / 'ene.json'), descargar=_sitio(), hoy=date(2027, 1, 4), pausa=0)
        check("en enero, con la página del año anterior, no la usa (evita cargar fechas de otro año)",
              r['datos']['ultima_sincronizacion']['pagina']['resultado'] == 'rechazado')


def test_comando_de_terminal():
    with tempfile.TemporaryDirectory() as tmp:
        destino = str(Path(tmp) / 'cal.json')
        check("el comando guarda y sale con código 0",
              sync_local.main([destino], descargar=_sitio(), hoy=HOY, pausa=0) == 0 and Path(destino).exists())
        check("ante una falla parcial sale con código 2",
              sync_local.main([destino], descargar=_sitio(caidos=('feria',)), hoy=HOY, pausa=0) == 2)
        check("si no puede guardar nada sale con código 1 y deja el archivo",
              sync_local.main([destino], descargar=_sitio(caidos=('pagina',)), hoy=HOY, pausa=0) == 1
              and Path(destino).exists())


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE LA ACTUALIZACIÓN LOCAL DEL CALENDARIO")
    print("=" * 70)
    test_arma_el_calendario_desde_las_tres_fuentes()
    test_igual_que_el_servidor()
    test_pide_poco_y_con_pausa()
    test_corta_cuando_las_noticias_ya_son_mas_viejas_que_la_ventana()
    test_guardado_y_codigos()
    test_comando_de_terminal()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
