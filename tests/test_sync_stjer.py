#!/usr/bin/env python3
"""
Tests de la sincronización automática con el sitio del STJER
(modulos/plazos/sync_stjer.py).

Usa copias reales guardadas en tests/fixtures/plazos/ (las dos tablas de la
página "Inhábiles y feriados" de 2026 y noticias de inhábiles del feed RSS),
así que no necesita red. Las partes que guardan en la base usan la app real
en modo testing (SQLite en memoria). `python tests/test_sync_stjer.py`.
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
# Ver el comentario de tests/test_rutas_admin.py: hay que pisar la URI ANTES de
# importar servidor, que instancia una app a nivel de módulo.
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.models import DiaInhabil, CalendarioSyncLog
from modulos.plazos import sync_stjer as s
from modulos.plazos.calendario import Calendario, cargar_calendario
from modulos.plazos.calculo import calcular
from servidor import crear_app

FIX = Path(__file__).parent / 'fixtures' / 'plazos'
HTML_2026 = (FIX / 'pagina_inhabiles_2026.html').read_text(encoding='utf-8')
RSS = (FIX / 'noticias_inhabiles.xml').read_text(encoding='utf-8')

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:   # bajo pytest una falla tiene que hacer fallar el test
            raise AssertionError(nombre)


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    return app


def _limpiar_tablas():
    DiaInhabil.query.delete()
    CalendarioSyncLog.query.delete()
    db.session.commit()


def _noticia(parte_del_titulo):
    for it in s.parsear_rss(RSS):
        if parte_del_titulo in it['titulo']:
            return it
    raise AssertionError(f'No está la noticia {parte_del_titulo!r} en el fixture')


def _clasificar(parte_del_titulo):
    it = _noticia(parte_del_titulo)
    return s.clasificar_noticia(it['titulo'], it['texto'], it['fecha'], it['link'])


# ── parseo de la página ──────────────────────────────────────────────────────

def test_parsear_pagina():
    entradas, problemas = s.parsear_pagina(HTML_2026, 2026)
    check("sin filas ilegibles", problemas == [], problemas)
    por_fecha = {}
    for e in entradas:
        if e.alcance == 'provincia':
            por_fecha.setdefault(e.desde, []).append(e)
    check("23/3 es 'no laborable'", por_fecha[date(2026, 3, 23)][0].tipo == 'no_laborable')
    check("10/7 es 'no laborable'", por_fecha[date(2026, 7, 10)][0].tipo == 'no_laborable')
    check("7/12 es 'no laborable'", por_fecha[date(2026, 12, 7)][0].tipo == 'no_laborable')
    check("Carnaval 16 y 17 de febrero", date(2026, 2, 16) in por_fecha and date(2026, 2, 17) in por_fecha)
    check("Visita del Papa 9/11", date(2026, 11, 9) in por_fecha)
    check("no hay filas de feria (enero es regla, julio sale de las noticias)",
          not any(e.tipo.startswith('feria_') for e in entradas))
    locales = {(e.localidad or e.departamento): e for e in entradas if e.tipo == 'feriado_local'}
    check("Paraná: 7 de octubre", locales['Paraná'].desde == date(2026, 10, 7))
    check("'C. del Uruguay' se reconoce", 'Concepción del Uruguay' in locales)
    check("Urdinarrain: último domingo de noviembre = 29/11", locales['Urdinarrain'].desde == date(2026, 11, 29))
    check("Villa del Rosario: 1er. domingo de octubre = 4/10", locales['Villa del Rosario'].desde == date(2026, 10, 4))
    check("'Islas del Ibicuy' es un departamento",
          locales['Islas del Ibicuy'].alcance == 'departamento')


def test_pagina_de_otro_anio_se_rechaza():
    try:
        s.parsear_pagina(HTML_2026, 2027)
        check("la página de 2026 no se acepta como 2027", False)
    except s.ErrorSync as e:
        check("la página de 2026 no se acepta como 2027", 'Viernes Santo' in str(e), str(e)[:80])


def test_pagina_rota_se_rechaza():
    for nombre, html in (('sin tablas', '<html><body>nada</body></html>'),
                         ('una sola tabla', '<table><tr><td>x</td></tr></table>')):
        try:
            s.parsear_pagina(html, 2026)
            check(f"{nombre} → rechazada", False)
        except s.ErrorSync:
            check(f"{nombre} → rechazada", True)
    # Faltan los feriados ancla
    recortada = HTML_2026.replace('Año Nuevo', 'xx').replace('Navidad', 'yy')
    try:
        s.parsear_pagina(recortada, 2026)
        check("sin feriados ancla → rechazada", False)
    except s.ErrorSync:
        check("sin feriados ancla → rechazada", True)


# ── clasificación de noticias ────────────────────────────────────────────────

def test_clasificar_noticias():
    r = _clasificar('Día de la Magistratura')
    check("Magistratura 2026 → provincia, descuenta",
          r['estado'] == 'ok' and r['entradas'][0].alcance == 'provincia' and r['entradas'][0].descuenta
          and r['entradas'][0].desde == date(2026, 9, 15), r)

    r = _clasificar('Será inhábil judicial el 26 de diciembre')
    check("26/12/2025 → provincia (Acuerdo Especial)",
          r['estado'] == 'ok' and [(e.desde, e.alcance) for e in r['entradas']] == [(date(2025, 12, 26), 'provincia')], r)

    r = _clasificar('Libertador San Martín')
    check("Juzgado de Paz de Libertador San Martín → aviso, sin tomar la dirección '25 de Mayo 763'",
          r['estado'] == 'ok' and [(e.desde, e.alcance, e.localidad, e.descuenta) for e in r['entradas']]
          == [(date(2026, 8, 19), 'organismo', 'Libertador San Martín', False)], r)

    r = _clasificar('atención al público en organismos de Victoria')
    check("atención al público en organismos de Victoria → solo aviso (26/6 y 1/7)",
          r['estado'] == 'ok' and all(not e.descuenta for e in r['entradas'])
          and sorted(e.desde for e in r['entradas']) == [date(2026, 6, 26), date(2026, 7, 1)], r)

    r = _clasificar('Pueblo Brugo, Federal')
    check("tres juzgados de paz → avisos en las tres localidades",
          {e.localidad for e in r['entradas']} == {'Pueblo Brugo', 'Federal', 'Hernandarias'}
          and all(not e.descuenta for e in r['entradas']), r)

    r = _clasificar('El 7 de octubre será')
    check("Patrona del 7/10 → inhábil en cada localidad nombrada (descuenta)",
          r['estado'] == 'ok' and all(e.descuenta and e.alcance == 'localidad' for e in r['entradas'])
          and {e.localidad for e in r['entradas']} == {'Paraná', 'Crespo', 'Gualeguaychú', 'Pueblo Brugo',
                                                      'Villa San Marcial', 'Rosario del Tala'}, r)

    r = _clasificar('fuero Laboral')
    check("congreso: inhábil solo para el fuero laboral (23 y 24/10/2025)",
          r['estado'] == 'ok' and {(e.desde, e.fuero) for e in r['entradas']}
          == {(date(2025, 10, 23), 'laboral'), (date(2025, 10, 24), 'laboral')}, r)

    r = _clasificar('Mañana será inhábil judicial en La Paz')
    check("todos los organismos de La Paz → localidad, descuenta, 31/7/2025",
          r['estado'] == 'ok' and [(e.desde, e.localidad, e.descuenta) for e in r['entradas']]
          == [(date(2025, 7, 31), 'La Paz', True)], r)

    r = _clasificar('Será inhábil el lunes')
    check("'el lunes' en una noticia de viernes 11/4/2025 → 14/4",
          [e.desde for e in r['entradas']] == [date(2025, 4, 14)], r)

    r = _clasificar('Día de la abogacía')
    check("Abogacía 2025 → provincia (no confunde 'Paraná y Concordia' de las mesas de información)",
          [(e.desde, e.alcance) for e in r['entradas']] == [(date(2025, 8, 29), 'provincia')], r)

    r = s.clasificar_noticia('Condenan a 12 y 10 años de prisión', 'inhabilitación perpetua para ejercer cargos',
                             datetime(2026, 2, 27), 'x')
    check("'inhabilitación' (condena) no es un inhábil", r['estado'] == 'ignorar')

    r = s.clasificar_noticia('Resol. Sup. 273: solicita día inhábil en fecha', 'Texto sin fechas legibles',
                             datetime(2023, 3, 20), 'x')
    check("inhábil sin fecha → ambiguo (no se aplica)", r['estado'] == 'ambiguo' and r['entradas'] == [], r)

    r = s.clasificar_noticia('Inhábil judicial', 'Será inhábil el 5 de mayo por un motivo desconocido.',
                             datetime(2026, 5, 1), 'x')
    check("sin localidad ni alcance claro → ambiguo", r['estado'] == 'ambiguo', r)

    r = s.clasificar_noticia('Desde las 12 fue inhábil judicial en Concordia',
                             'Desde las 12 horas de hoy se declaró inhábil judicial en Concordia por un corte de luz.',
                             datetime(2023, 4, 18), 'x')
    check("inhábil de medio día → solo aviso", r['estado'] == 'ok' and all(not e.descuenta for e in r['entradas']), r)

    r = s.clasificar_noticia('Autoridades Judiciales de Feria en Entre Ríos',
                             'Tendrá lugar del 6 al 17 de julio de 2026. Los casos de urgencia...',
                             datetime(2026, 7, 3), 'x')
    check("feria de julio 2026 = 6 al 17",
          r['entradas'][0].tipo == 'feria_julio' and (r['entradas'][0].desde, r['entradas'][0].hasta)
          == (date(2026, 7, 6), date(2026, 7, 17)), r)


# ── persistencia ─────────────────────────────────────────────────────────────

def test_sincronizar_pagina_y_calculo():
    app = _app()
    with app.app_context():
        _limpiar_tablas()
        r = s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html=HTML_2026)
        check("primera sincronización de la página: ok", r['resultado'] == 'ok' and r['agregados'] > 50, r)
        cant = DiaInhabil.query.count()
        r = s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html=HTML_2026)
        check("segunda vez: sin cambios, sin duplicados",
              r['agregados'] == 0 and r['quitados'] == 0 and DiaInhabil.query.count() == cant, r)

        # Una carga manual no se pisa ni se borra
        db.session.add(DiaInhabil(fecha_desde=date(2026, 11, 2), fecha_hasta=date(2026, 11, 2), tipo='inhabil_judicial',
                                  motivo='Cargado a mano', alcance='provincia', fuente='manual', origen=None))
        db.session.commit()
        s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html=HTML_2026)
        check("la carga manual sobrevive a la sincronización",
              DiaInhabil.query.filter_by(fuente='manual').count() == 1)

        # Una página rota no borra lo que ya había
        r = s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html='<html></html>')
        check("página rota → rechazada y se conserva el calendario",
              r['resultado'] == 'rechazado' and DiaInhabil.query.filter_by(fuente='scraper').count() == cant, r)
        check("el rechazo queda en el log", CalendarioSyncLog.query.filter_by(resultado='rechazado').count() == 1)

        # Página de otro año (enero 2027 sin actualizar) → rechazada
        r = s.sincronizar_pagina(db, hoy=date(2027, 1, 4), html=HTML_2026)
        check("la página de 2026 vista en 2027 se rechaza", r['resultado'] == 'rechazado', r)

        cal = cargar_calendario()
        check("23/3/2026 (no laborable) inhábil en el cómputo", not cal.es_habil(date(2026, 3, 23)))
        check("7/10/2026 inhábil en Paraná (patrona), hábil en Concordia",
              not cal.es_habil(date(2026, 10, 7), 'Paraná') and cal.es_habil(date(2026, 10, 7), 'Concordia'))
        check("11/2026: Visita del Papa 9/11 inhábil", not cal.es_habil(date(2026, 11, 9)))


def test_sincronizar_rss_y_calculo():
    app = _app()
    with app.app_context():
        _limpiar_tablas()
        hoy = date(2026, 10, 8)
        r = s.sincronizar_rss(db, hoy=hoy, xmls=[RSS])
        check("RSS: se procesan las noticias de inhábiles", r['nuevas'] >= 10, r)
        cant = DiaInhabil.query.count()
        r = s.sincronizar_rss(db, hoy=hoy, xmls=[RSS])
        check("RSS: segunda vez no duplica", DiaInhabil.query.count() == cant and r['nuevas'] == 0, r)

        cal = cargar_calendario()
        check("15/9/2026 inhábil (Magistratura)", not cal.es_habil(date(2026, 9, 15)))
        check("19/8/2026 sigue hábil (solo un Juzgado de Paz), pero con aviso en esa localidad",
              cal.es_habil(date(2026, 8, 19), 'Libertador San Martín')
              and len(cal.avisos(date(2026, 8, 17), date(2026, 8, 21), 'Libertador San Martín')) == 1)
        check("31/7/2025 inhábil en La Paz, hábil en Paraná",
              not cal.es_habil(date(2025, 7, 31), 'La Paz') and cal.es_habil(date(2025, 7, 31), 'Paraná'))
        check("23/10/2025 inhábil para el fuero laboral, hábil para civil",
              not cal.es_habil(date(2025, 10, 23), 'Paraná', 'laboral') and cal.es_habil(date(2025, 10, 23), 'Paraná', 'civil'))

        # Cómputo de punta a punta con un aviso: cédula 17/8/2026 (lunes feriado), 5 hábiles, Juzgado de Paz de L. San Martín
        res = calcular(cal, tipo_notificacion='cedula', fecha=date(2026, 8, 14), dias=5, localidad='Libertador San Martín')
        check("el cómputo incluye el aviso del inhábil parcial del 19/8",
              len(res.avisos) == 1 and res.avisos[0]['fuente_url'], res.avisos)


# ── casos que encontró la revisión independiente ─────────────────────────────

def _c(titulo, cuerpo, pub=datetime(2026, 5, 6, 12, 0), link='https://www.jusentrerios.gov.ar/x/'):
    return s.clasificar_noticia(titulo, cuerpo, pub, link)


def test_clasificacion_segura():
    r = _c('Aclaración: el 8 de mayo no será inhábil judicial',
           'El Superior Tribunal aclaró que el 8 de mayo no será inhábil judicial en Paraná.')
    check("una aclaración 'no será inhábil' jamás crea un inhábil (queda para revisar)",
          r['estado'] == 'ambiguo' and r['entradas'] == [], r)
    r = _c('Se deja sin efecto el inhábil judicial del 8 de mayo en Paraná',
           'Se dejó sin efecto la declaración de inhábil para todos los organismos judiciales de Paraná.')
    check("'sin efecto' tampoco", r['estado'] == 'ambiguo' and r['entradas'] == [], r)

    r = _c('Inhábil judicial en los juzgados de Paz de Hernández y Lucas González',
           'El 8 de mayo será inhábil judicial en los juzgados de Paz de Hernández y Lucas González por la mudanza.')
    check("plural 'juzgados de Paz' sin 'todos los organismos' → solo aviso, no descuenta a toda la ciudad",
          r['estado'] == 'ok' and r['entradas'] and all(not e.descuenta for e in r['entradas']), r)

    r = _c('El 8 de mayo será inhábil judicial en Crespo',
           'Se declaró inhábil la sede de la Secretaría de Crespo por la mudanza de oficinas.')
    check("nombra una localidad pero no dice que alcance a todo → no descuenta (aviso)",
          all(not e.descuenta for e in r['entradas']) and r['estado'] == 'ok', r)

    r = _c('El 8 de mayo será inhábil judicial en Villa Clara',
           'Para todos los organismos jurisdiccionales de Villa Clara por un corte de luz.')
    check("'organismos jurisdiccionales' no se confunde con 'jurisdicción' (departamento)",
          not any(e.alcance == 'departamento' for e in r['entradas']), [(e.alcance, e.localidad) for e in r['entradas']])

    r = _c('Mañana será inhábil judicial en Paraná',
           'Será inhábil judicial para todos los organismos de Paraná. En el resto de los tribunales de la provincia '
           'la actividad será normal.')
    check("la frase 'en el resto de los tribunales de la provincia ... normal' no vuelve provincial al inhábil",
          r['estado'] == 'ok' and all(e.alcance == 'localidad' and e.localidad == 'Paraná' for e in r['entradas']),
          [(e.alcance, e.localidad) for e in r['entradas']])

    r = _c('Inhábil judicial en el fuero civil de Paraná el 8 de mayo',
           'Será inhábil judicial para todos los organismos del fuero civil de la ciudad de Paraná.')
    check("fuero + localidad: conserva el fuero",
          r['estado'] == 'ok' and all(e.fuero == 'civil' and e.localidad == 'Paraná' for e in r['entradas']),
          [(e.alcance, e.localidad, e.fuero) for e in r['entradas']])

    r = _c('Inhábil judicial el 8 de mayo para el fuero civil',
           'Será inhábil judicial para el fuero civil, excepto el fuero de familia.')
    check("'excepto' en una noticia por fuero → a revisión", r['estado'] == 'ambiguo', r)

    r = _c('Mañana será inhábil judicial en Paraná',
           'Por la mañana se cortó la luz en los tribunales. Será inhábil judicial para todos los organismos de Paraná.',
           pub=datetime(2026, 5, 6, 12, 0))
    check("'por la mañana' no suma un día extra", {e.desde for e in r['entradas']} == {date(2026, 5, 7)},
          sorted({str(e.desde) for e in r['entradas']}))

    r = _c('Inhábil judicial en Concordia', 'Será inhábil judicial el 8 de mayo para el Juzgado Civil N° 2, ubicado '
           'en calle 25 de Mayo 763.')
    check("una calle con fecha ('calle 25 de Mayo 763') no es un día inhábil",
          s._limpiar_noticia('en calle 25 de Mayo 763 de la ciudad').find('25 de Mayo') == -1)

    r = _c('Será inhábil judicial el 12 de mayo', 'Será inhábil judicial el 12 de mayo para todas las dependencias '
           'judiciales de la provincia. 6 de mayo de 2026 SIC-STJER')
    check("la firma 'SIC-STJER' con fecha no cuenta como inhábil",
          [str(e.desde) for e in r['entradas']] == ['2026-05-12'], [str(e.desde) for e in r['entradas']])

    r = _c('Inhábil judicial', 'Será inhábil judicial el ' + '1 y ' * 3000 + ' de mayo para todas las dependencias judiciales',
           pub=datetime(2026, 5, 6, 12, 0))
    check("un texto patológico no cuelga el proceso (sin catástrofe de regex)", r['estado'] in ('ok', 'ambiguo'))

    r = _c('Será inhábil judicial', 'Será inhábil judicial el 6, 7 y 10 al 12 de mayo para todas las dependencias '
           'judiciales de la provincia.')
    check("lista y rango mezclados ('6, 7 y 10 al 12 de mayo') se leen bien",
          sorted({e.desde.day for e in r['entradas']}) == [6, 7, 10, 11, 12], sorted({e.desde.day for e in r['entradas']}))

    it = s.parsear_rss("""<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>Mañana será inhábil judicial</title><link>https://www.jusentrerios.gov.ar/n/</link>
        <pubDate>Wed, 06 May 2026 01:30:00 +0000</pubDate><content:encoded><![CDATA[<p>x</p>]]></content:encoded>
        </item></channel></rss>""")[0]
    check("la hora del feed (UTC) se pasa a Argentina: 01:30 UTC del 6/5 es el 5/5 a las 22:30",
          it['fecha'] == datetime(2026, 5, 5, 22, 30), it['fecha'])

    check("url_oficial: https del dominio oficial sí; http, otro dominio, javascript: no",
          s.url_oficial('https://www.jusentrerios.gov.ar/x') and not s.url_oficial('http://www.jusentrerios.gov.ar/x')
          and not s.url_oficial('https://jusentrerios.gov.ar.evil.com/') and not s.url_oficial('javascript:alert(1)'))
    r = _c('Será inhábil judicial el 12 de mayo', 'Para todas las dependencias judiciales de la provincia.',
           link='javascript:alert(1)')
    check("un link que no es del dominio oficial no se guarda como fuente",
          r['entradas'] and all(e.fuente_url is None for e in r['entradas']), r)


def test_pagina_falla_cerrado():
    sin_santo = HTML_2026.replace('Jueves y Viernes Santo', 'Semana Santa')
    try:
        s.parsear_pagina(sin_santo, 2026)
        check("sin la fila de Viernes Santo se rechaza (no se puede confirmar el año)", False)
    except s.ErrorSync:
        check("sin la fila de Viernes Santo se rechaza (no se puede confirmar el año)", True)
    ilegible = HTML_2026.replace('2 y 3 de Abril', 'abril')
    try:
        s.parsear_pagina(ilegible, 2026)
        check("con la fecha del Viernes Santo ilegible se rechaza", False)
    except s.ErrorSync:
        check("con la fecha del Viernes Santo ilegible se rechaza", True)


def test_get_no_sigue_redirects_ajenos():
    import requests
    llamadas = []

    class Resp:
        def __init__(self, status, headers=None, cuerpo=b'<html></html>'):
            self.status_code, self.headers, self._c = status, headers or {}, cuerpo

        def iter_content(self, n):
            yield self._c

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def close(self):
            pass

    originales = requests.get
    try:
        def falso(url, **kw):
            llamadas.append((url, kw.get('allow_redirects')))
            if 'jusentrerios' in url:
                return Resp(302, {'Location': 'https://sitio-ajeno.example.com/x'})
            return Resp(200)
        requests.get = falso
        try:
            s._get('https://www.jusentrerios.gov.ar/algo/')
            check("un redirect a otro dominio se rechaza", False)
        except s.ErrorSync:
            check("un redirect a otro dominio se rechaza", True)
        check("nunca se pidió el dominio ajeno", all('example.com' not in u for u, _ in llamadas), llamadas)
        check("los redirects los maneja el código, no requests", all(a is False for _, a in llamadas))

        requests.get = lambda url, **kw: Resp(200, cuerpo=b'x' * (s.MAX_BYTES + 10))
        try:
            s._get('https://www.jusentrerios.gov.ar/grande/')
            check("una respuesta enorme se corta", False)
        except s.ErrorSync:
            check("una respuesta enorme se corta", True)
        try:
            s._get('http://www.jusentrerios.gov.ar/sin-https/')
            check("http (sin https) se rechaza", False)
        except s.ErrorSync:
            check("http (sin https) se rechaza", True)
    finally:
        requests.get = originales


def test_persistencia_robusta():
    app = _app()
    with app.app_context():
        _limpiar_tablas()
        # Filas duplicadas (dos sincronizaciones solapadas): al desaparecer de la fuente se borran las dos
        e = s.Entrada(desde=date(2026, 5, 7), hasta=date(2026, 5, 7), tipo='inhabil_judicial', motivo='x', fuente='scraper')
        s.aplicar_entradas(db, 'rss:dup', [e])
        db.session.add(DiaInhabil(fecha_desde=date(2026, 5, 7), fecha_hasta=date(2026, 5, 7), tipo='inhabil_judicial',
                                  motivo='x', alcance='provincia', fuente='scraper', origen='rss:dup'))
        db.session.commit()
        check("hay 2 filas idénticas", DiaInhabil.query.filter_by(origen='rss:dup').count() == 2)
        s.aplicar_entradas(db, 'rss:dup', [e])
        check("si siguen vigentes se deja una sola", DiaInhabil.query.filter_by(origen='rss:dup').count() == 1)
        s.aplicar_entradas(db, 'rss:dup', [])
        check("si desaparecen de la fuente se borran todas", DiaInhabil.query.filter_by(origen='rss:dup').count() == 0)

        # Si una fuente revienta, queda un log 'error' (para que se reintente a las 6 h y no cada hora)
        original = dict(s._FUENTES)
        try:
            def explota(_db):
                raise RuntimeError('se cayó la base')
            s._FUENTES['feria'] = explota
            resumen = s.sincronizar_todo(db, ['feria'])
        finally:
            s._FUENTES.update(original)
        from modulos.models import CalendarioSyncLog
        check("la excepción de una fuente queda registrada como 'error'",
              resumen['feria']['resultado'] == 'error'
              and CalendarioSyncLog.query.filter_by(fuente='feria', resultado='error').count() == 1)

        # Una sola sincronización a la vez
        s._LOCK.acquire()
        try:
            check("si ya hay una sincronización en curso, otra no arranca",
                  s.sincronizar_todo(db) == {'en_curso': {'resultado': 'en_curso'}})
        finally:
            s._LOCK.release()

        # Un rechazo repetido no repite el mail al admin
        avisos = []
        original_aviso = s.avisar_admin
        s.avisar_admin = lambda asunto, cuerpo: avisos.append(asunto)
        try:
            _limpiar_tablas()
            s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html='<html></html>')
            s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html='<html></html>')
            s.sincronizar_pagina(db, hoy=date(2026, 10, 8), html='<html></html>')
        finally:
            s.avisar_admin = original_aviso
        check("tres rechazos iguales seguidos mandan un solo mail", len(avisos) == 1, avisos)

        # Si una consulta del feed falla, la corrida queda como 'error' (se reintenta pronto), pero se aplica lo leído
        _limpiar_tablas()
        import requests
        original_get = s._get

        def get_con_fallo(url):
            if url == s.URL_FEED:
                return RSS
            raise requests.ConnectionError('sin red')
        s._get = get_con_fallo
        try:
            r = s.sincronizar_rss(db, hoy=date(2026, 10, 8))
        finally:
            s._get = original_get
        check("fallan las consultas de búsqueda → resultado 'error' (no 'ok')",
              r['resultado'] == 'error' and DiaInhabil.query.count() > 0, r.get('resultado'))

if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE SINCRONIZACIÓN CON EL STJER")
    print("=" * 70)
    test_parsear_pagina()
    test_pagina_de_otro_anio_se_rechaza()
    test_pagina_rota_se_rechaza()
    test_clasificar_noticias()
    test_sincronizar_pagina_y_calculo()
    test_sincronizar_rss_y_calculo()
    test_clasificacion_segura()
    test_pagina_falla_cerrado()
    test_get_no_sigue_redirects_ajenos()
    test_persistencia_robusta()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
