"""
Evento de calendario para el vencimiento de un plazo.

Genera, a partir de un Resultado (calculo.py):
- un link de "Agregar a Google Calendar" (URL de plantilla de evento), y
- un archivo .ics (iCalendar, RFC 5545) que abren Apple Calendar, Outlook,
  Thunderbird y también Google Calendar (Importar).

El evento es de día completo en la fecha del vencimiento y, en el .ics,
trae recordatorios (3 días antes, 1 día antes y la mañana del vencimiento).
Lógica pura: sin Flask ni base de datos.
"""

import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

from modulos.plazos.calculo import fecha_larga

_MAX_NOMBRE = 120


def _nombre_limpio(nombre):
    """Una sola línea, sin caracteres de control, de hasta 120 caracteres."""
    n = re.sub(r'[\x00-\x1f\x7f]+', ' ', nombre or '')
    return re.sub(r'\s+', ' ', n).strip()[:_MAX_NOMBRE]


def titulo_evento(resultado, nombre=''):
    nombre = _nombre_limpio(nombre)
    if nombre:
        return f'Vence plazo: {nombre}'
    tipo = 'hábiles' if resultado.modo == 'habiles' else 'corridos'
    return f'Vence plazo de {resultado.dias} días {tipo}'


def descripcion_evento(resultado, nombre='', localidad=None, fuero_texto=None, url_app=None):
    lineas = []
    if _nombre_limpio(nombre):
        lineas.append(_nombre_limpio(nombre))
    tipo = 'hábiles' if resultado.modo == 'habiles' else 'corridos'
    lineas.append(f'Plazo de {resultado.dias} días {tipo}.')
    lineas.append(f'Notificación perfeccionada: {fecha_larga(resultado.fecha_perfeccion)}.')
    lineas.append(f'Vencimiento: {fecha_larga(resultado.vencimiento)}.')
    if resultado.gracia_fecha and resultado.gracia_hora:
        lineas.append(f'Con la hora de gracia: hasta las {resultado.gracia_hora} hs del '
                      f'{fecha_larga(resultado.gracia_fecha)}.')
    contexto = ' · '.join(x for x in (localidad, fuero_texto) if x)
    if contexto:
        lineas.append(f'({contexto})')
    lineas.append('Cálculo orientativo de Foja: verificalo en el expediente.')
    if url_app:
        lineas.append(url_app)
    return '\n'.join(lineas)


def _ymd(f):
    return f'{f.year:04d}{f.month:02d}{f.day:02d}'


def url_google_calendar(resultado, titulo, descripcion, lugar=None):
    """Link que abre Google Calendar con el evento de día completo ya cargado."""
    fin = resultado.vencimiento + timedelta(days=1)   # en Google el fin de un evento de día completo es exclusivo
    partes = [
        'action=TEMPLATE',
        f'text={quote(titulo, safe="")}',
        f'dates={_ymd(resultado.vencimiento)}/{_ymd(fin)}',
        f'details={quote(descripcion, safe="")}',
    ]
    if lugar:
        partes.append(f'location={quote(lugar, safe="")}')
    return 'https://calendar.google.com/calendar/render?' + '&'.join(partes)


# ── iCalendar ────────────────────────────────────────────────────────────────

def _escapar(texto):
    """Escapado de valores de texto de iCalendar (RFC 5545, 3.3.11)."""
    texto = texto.replace('\r', '')
    return (texto.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n'))


def _plegar(linea):
    """Pliega una línea a 75 octetos como máximo (RFC 5545, 3.1), sin partir caracteres UTF-8."""
    salida, actual, largo = [], '', 0
    for ch in linea:
        b = len(ch.encode('utf-8'))
        limite = 75 if not salida else 74   # las líneas de continuación empiezan con un espacio
        if largo + b > limite:
            salida.append(actual)
            actual, largo = '', 0
        actual += ch
        largo += b
    salida.append(actual)
    return '\r\n '.join(salida)


def generar_ics(resultado, titulo, descripcion, lugar=None, ahora=None, uid=None):
    """Texto del archivo .ics (con saltos de línea CRLF, como exige el estándar)."""
    ahora = ahora or datetime.utcnow()
    uid = uid or f'{uuid.uuid4()}@foja.com.ar'
    fin = resultado.vencimiento + timedelta(days=1)
    lineas = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Foja//Contador de plazos//ES',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:{uid}',
        f'DTSTAMP:{ahora.strftime("%Y%m%dT%H%M%SZ")}',
        f'DTSTART;VALUE=DATE:{_ymd(resultado.vencimiento)}',
        f'DTEND;VALUE=DATE:{_ymd(fin)}',
        f'SUMMARY:{_escapar(titulo)}',
        f'DESCRIPTION:{_escapar(descripcion)}',
    ]
    if lugar:
        lineas.append(f'LOCATION:{_escapar(lugar)}')
    lineas += [
        'TRANSP:TRANSPARENT',   # no marca el día como "ocupado"
        'CATEGORIES:Plazo judicial',
    ]
    # El evento arranca a las 00:00 del día del vencimiento: los disparadores son relativos a esa hora
    for disparador, texto in (('-P2DT15H', 'Faltan 3 días para el vencimiento'),
                              ('-PT15H', 'Mañana vence el plazo'),
                              ('PT8H', 'Hoy vence el plazo')):
        lineas += ['BEGIN:VALARM', 'ACTION:DISPLAY', f'DESCRIPTION:{_escapar(texto)}',
                   f'TRIGGER:{disparador}', 'END:VALARM']
    lineas += ['END:VEVENT', 'END:VCALENDAR']
    return '\r\n'.join(_plegar(l) for l in lineas) + '\r\n'


def nombre_archivo(resultado):
    return f'plazo-{resultado.vencimiento.isoformat()}.ics'


def datos_calendario(resultado, nombre='', localidad=None, fuero_texto=None, url_app=None, ahora=None):
    """Todo lo que necesita la pantalla: link de Google, contenido del .ics y nombre de archivo."""
    titulo = titulo_evento(resultado, nombre)
    descripcion = descripcion_evento(resultado, nombre, localidad, fuero_texto, url_app)
    lugar = localidad
    return {
        'titulo': titulo,
        'google_url': url_google_calendar(resultado, titulo, descripcion, lugar),
        'ics': generar_ics(resultado, titulo, descripcion, lugar, ahora=ahora),
        'archivo': nombre_archivo(resultado),
    }
