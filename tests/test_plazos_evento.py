#!/usr/bin/env python3
"""
Tests del evento de calendario (modulos/plazos/evento.py: link de Google
Calendar y archivo .ics) y de la decisión de cuándo sincronizar
(sync_stjer.toca_sincronizar). Lógica pura. `python tests/test_plazos_evento.py`
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.plazos.calculo import calcular
from modulos.plazos.calendario import Calendario
from modulos.plazos.evento import datos_calendario, _plegar, _escapar, titulo_evento
from modulos.plazos.sync_stjer import toca_sincronizar, fuentes_pendientes

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:   # bajo pytest una falla tiene que hacer fallar el test
            raise AssertionError(nombre)


def _resultado():
    return calcular(Calendario(), tipo_notificacion='cedula', fecha=date(2026, 10, 5), dias=5)   # vence 13/10/2026


def test_google_calendar():
    r = _resultado()
    d = datos_calendario(r, nombre='Contestación; demanda, Exp. 123 & "cía"', localidad='Paraná',
                         fuero_texto='Civil y Comercial', url_app='https://foja.com.ar/plazos/')
    u = urlparse(d['google_url'])
    q = parse_qs(u.query)
    check("el link es de calendar.google.com", u.scheme == 'https' and u.netloc == 'calendar.google.com'
          and u.path == '/calendar/render', d['google_url'][:60])
    check("action=TEMPLATE", q['action'] == ['TEMPLATE'])
    check("día completo: del 13/10 al 14/10 (fin exclusivo)", q['dates'] == ['20261013/20261014'], q.get('dates'))
    check("el título lleva el nombre tal cual (sin romper la URL)",
          q['text'] == ['Vence plazo: Contestación; demanda, Exp. 123 & "cía"'], q['text'])
    check("los detalles traen vencimiento y gracia", 'Vencimiento: Martes 13 de octubre de 2026' in q['details'][0]
          and 'hasta las 09:00' in q['details'][0], q['details'][0])
    check("lugar = localidad", q['location'] == ['Paraná'])
    check("la URL no se pasa de 2000 caracteres", len(d['google_url']) < 2000, len(d['google_url']))


def test_ics():
    r = _resultado()
    ahora = datetime(2026, 10, 8, 12, 0, 0)
    d = datos_calendario(r, nombre='Contestación de demanda', localidad='Paraná', fuero_texto='Civil y Comercial',
                         url_app='https://foja.com.ar/plazos/', ahora=ahora)
    ics = d['ics']
    check("termina con CRLF y usa CRLF en todo el archivo", ics.endswith('\r\n') and '\n' not in ics.replace('\r\n', ''))
    check("estructura VCALENDAR/VEVENT", ics.startswith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n')
          and 'BEGIN:VEVENT' in ics and ics.rstrip().endswith('END:VCALENDAR'))
    check("evento de día completo", 'DTSTART;VALUE=DATE:20261013' in ics and 'DTEND;VALUE=DATE:20261014' in ics)
    check("DTSTAMP en UTC", 'DTSTAMP:20261008T120000Z' in ics)
    check("tres recordatorios", ics.count('BEGIN:VALARM') == 3 and 'TRIGGER:-P2DT15H' in ics
          and 'TRIGGER:-PT15H' in ics and 'TRIGGER:PT8H' in ics)
    check("no marca el día como ocupado", 'TRANSP:TRANSPARENT' in ics)
    check("el título está en el SUMMARY", 'SUMMARY:Vence plazo: Contestación de demanda' in ics)
    check("el nombre de archivo lleva la fecha", d['archivo'] == 'plazo-2026-10-13.ics', d['archivo'])
    check("ninguna línea pasa de 75 octetos",
          all(len(l.encode('utf-8')) <= 75 for l in ics.split('\r\n')),
          [len(l.encode('utf-8')) for l in ics.split('\r\n') if len(l.encode('utf-8')) > 75])
    # Desplegado de las líneas plegadas: la descripción completa tiene que recomponerse
    desplegado = ics.replace('\r\n ', '')
    check("la descripción se recompone al desplegar (con \\n escapados)",
          'DESCRIPTION:Contestación de demanda\\nPlazo de 5 días hábiles.' in desplegado, desplegado[desplegado.index('DESCRIPTION'):][:120])


def test_ics_inyeccion():
    # Un nombre con saltos de línea no debe poder inyectar propiedades al .ics (ej. un ATTENDEE o un VALARM falso)
    r = _resultado()
    malo = 'Plazo\r\nATTENDEE:mailto:x@y.com\r\nBEGIN:VALARM\r\nEND:VALARM'
    d = datos_calendario(r, nombre=malo)
    lineas = d['ics'].split('\r\n')
    check("el nombre malicioso no crea líneas nuevas", not any(l.startswith('ATTENDEE') for l in lineas)
          and len([l for l in lineas if l.startswith('SUMMARY')]) == 1)
    check("la cantidad de alarmas sigue siendo 3 (cuenta líneas reales, no texto dentro del título)",
          sum(1 for l in lineas if l == 'BEGIN:VALARM') == 3)
    check("ni siquiera en el título (una sola línea)", '\n' not in titulo_evento(r, malo) and '\r' not in titulo_evento(r, malo))
    check("escapado de ; , \\ y saltos de línea", _escapar('a;b,c\\d\ne') == 'a\\;b\\,c\\\\d\\ne')
    largo = 'x' * 500
    check("un nombre larguísimo se recorta a 120", len(titulo_evento(r, largo)) <= len('Vence plazo: ') + 120)
    plegada = _plegar('DESCRIPTION:' + 'ñ' * 200)
    check("el plegado no parte caracteres de 2 bytes",
          all(len(l.encode('utf-8')) <= 75 for l in plegada.split('\r\n'))
          and plegada.replace('\r\n ', '') == 'DESCRIPTION:' + 'ñ' * 200)


def test_toca_sincronizar():
    ahora = datetime(2026, 10, 8, 12, 0)
    ok = lambda dias: (ahora - timedelta(days=dias), 'ok')
    todas = lambda **kw: {'pagina': kw.get('pagina', ok(1)), 'feria': kw.get('feria', ok(1)), 'rss': kw.get('rss', ok(1))}
    check("nunca corrió → toca", toca_sincronizar({}, ahora, 15))
    check("falta una fuente → toca", toca_sincronizar({'pagina': ok(1), 'rss': ok(1)}, ahora, 15))
    check("todo hace 1 día → no toca", not toca_sincronizar(todas(), ahora, 15))
    check("hace 14 días → no toca", not toca_sincronizar(todas(pagina=ok(14), feria=ok(14), rss=ok(14)), ahora, 15))
    check("hace 15 días → toca", toca_sincronizar(todas(rss=ok(15)), ahora, 15))
    check("sin_cambios cuenta como éxito", not toca_sincronizar(
        todas(pagina=(ahora - timedelta(days=3), 'sin_cambios')), ahora, 15))
    check("un rechazo reciente (hace 1 h) no se reintenta todavía", not toca_sincronizar(
        todas(pagina=(ahora - timedelta(hours=1), 'rechazado')), ahora, 15))
    check("un rechazo de hace 7 h todavía no se reintenta (la página no se arregla sola en horas)", not toca_sincronizar(
        todas(pagina=(ahora - timedelta(hours=7), 'rechazado')), ahora, 15))
    check("un rechazo de hace 25 h se reintenta (no hay que esperar 15 días)", toca_sincronizar(
        todas(pagina=(ahora - timedelta(hours=25), 'rechazado')), ahora, 15))
    check("un error de hace 7 h se reintenta", toca_sincronizar(
        todas(rss=(ahora - timedelta(hours=7), 'error')), ahora, 15))
    check("solo se piden las fuentes que les toca (no las tres)",
          fuentes_pendientes(todas(rss=(ahora - timedelta(hours=7), 'error')), ahora, 15) == ['rss'],
          fuentes_pendientes(todas(rss=(ahora - timedelta(hours=7), 'error')), ahora, 15))
    check("sin log de ninguna fuente, las tres",
          fuentes_pendientes({}, ahora, 15) == ['pagina', 'feria', 'rss'])


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE EVENTO DE CALENDARIO Y CALENDARIO DE SINCRONIZACIÓN")
    print("=" * 70)
    test_google_calendar()
    test_ics()
    test_ics_inyeccion()
    test_toca_sincronizar()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
