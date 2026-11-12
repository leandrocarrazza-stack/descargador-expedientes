"""
Exportación del calendario de plazos para usarlo fuera del servidor (plugin local).

El servidor publica en /plazos/calendario.json las entradas que NO salen de una
regla (feria de julio, puentes, inhábiles por acuerdo, feriados de cada ciudad:
lo que el sincronizador lee del sitio del STJER). Las reglas fijas (feriados
nacionales, Carnaval, Semana Santa, feria de enero, etc.) ya viajan en el código
(reglas.py). Con las dos cosas juntas el cálculo local da lo mismo que el sitio.

Este módulo usa solo la biblioteca estándar y no importa Flask ni la base:
se puede copiar tal cual junto con reglas.py, calendario.py, calculo.py y
localidades.py.

Uso desde el plugin:

    from modulos.plazos.exportacion import descargar_calendario, calendario_desde_exportacion
    datos = descargar_calendario(destino='calendario_foja.json')      # baja y guarda
    cal, problemas = calendario_desde_exportacion(datos)              # listo para calcular()

Desde la terminal:  python -m modulos.plazos.exportacion calendario_foja.json
"""

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from modulos.plazos.calendario import Calendario
from modulos.plazos.reglas import Entrada

# Versión del formato. Si cambia de forma incompatible, se sube y los clientes viejos avisan.
FORMATO = 1

URL_OFICIAL = 'https://foja.com.ar/plazos/calendario.json'

ALCANCES = ('provincia', 'departamento', 'localidad', 'organismo')

# Topes de seguridad: un archivo más grande que esto no es un calendario de verdad
MAX_ENTRADAS = 5000
MAX_BYTES = 2 * 1024 * 1024

_TIPO_RE = re.compile(r'^[a-z_]{1,30}$')
_HOSTS_HTTP_PERMITIDOS = ('localhost', '127.0.0.1', '::1')


class ErrorExportacion(ValueError):
    """El archivo o la descarga no sirven; el mensaje explica por qué."""


# ── entrada ⇄ dict ────────────────────────────────────────────────────────
def entrada_a_dict(e):
    return {
        'desde': e.desde.isoformat(),
        'hasta': e.hasta.isoformat(),
        'tipo': e.tipo,
        'motivo': e.motivo,
        'alcance': e.alcance,
        'departamento': e.departamento,
        'localidad': e.localidad,
        'fuero': e.fuero,
        'descuenta': bool(e.descuenta),
        'fuente': e.fuente,
        'fuente_url': e.fuente_url,
    }


def _texto_opcional(d, clave, maximo):
    v = d.get(clave)
    if v is None or v == '':
        return None
    if not isinstance(v, str) or len(v) > maximo:
        raise ValueError(f'«{clave}» inválido')
    return v


def entrada_desde_dict(d):
    """Valida estrictamente y arma la Entrada. Lanza ValueError si algo no cierra."""
    if not isinstance(d, dict):
        raise ValueError('la entrada no es un objeto')
    try:
        desde = date.fromisoformat(d.get('desde'))
        hasta = date.fromisoformat(d.get('hasta'))
    except (TypeError, ValueError):
        raise ValueError('fechas inválidas')
    if hasta < desde:
        raise ValueError('el rango termina antes de empezar')
    tipo = d.get('tipo')
    if not isinstance(tipo, str) or not _TIPO_RE.match(tipo):
        raise ValueError('«tipo» inválido')
    motivo = d.get('motivo')
    if not isinstance(motivo, str) or not motivo.strip() or len(motivo) > 500:
        raise ValueError('«motivo» inválido')
    alcance = d.get('alcance', 'provincia')
    if alcance not in ALCANCES:
        raise ValueError('«alcance» inválido')
    descuenta = d.get('descuenta', True)
    if not isinstance(descuenta, bool):
        raise ValueError('«descuenta» inválido')
    return Entrada(
        desde=desde, hasta=hasta, tipo=tipo, motivo=motivo, alcance=alcance,
        departamento=_texto_opcional(d, 'departamento', 60),
        localidad=_texto_opcional(d, 'localidad', 80),
        fuero=_texto_opcional(d, 'fuero', 20),
        descuenta=descuenta,
        fuente=_texto_opcional(d, 'fuente', 20) or 'scraper',
        fuente_url=_texto_opcional(d, 'fuente_url', 500),
    )


# ── armado (lado servidor) ────────────────────────────────────────────────
def armar_exportacion(entradas, sincronizaciones=None, generado=None):
    """
    Arma el diccionario que publica el servidor.

    sincronizaciones: {'pagina': {'fecha': iso, 'resultado': 'ok'}, ...} con el
    último intento de cada fuente, para que el cliente sepa qué tan al día está.
    """
    generado = generado or datetime.now(timezone.utc)
    return {
        'formato': FORMATO,
        'generado': generado.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'ultima_sincronizacion': sincronizaciones or {},
        'entradas': [entrada_a_dict(e) for e in sorted(entradas, key=lambda e: (e.desde, e.hasta, e.motivo))],
    }


# ── lectura (lado cliente) ────────────────────────────────────────────────
def calendario_desde_exportacion(datos):
    """
    Devuelve (Calendario, problemas). `problemas` lista las entradas que se
    descartaron por no ser válidas (se omiten, nunca se inventan: una entrada
    mal formada deja de descontar un día, no descuenta de más).
    Lanza ErrorExportacion si el archivo entero no sirve.
    """
    if not isinstance(datos, dict):
        raise ErrorExportacion('El calendario descargado no tiene el formato esperado.')
    formato = datos.get('formato')
    if formato != FORMATO:
        raise ErrorExportacion(
            f'El calendario es de formato {formato!r} y este código entiende el {FORMATO}: '
            'actualizá el módulo de plazos del plugin.')
    crudas = datos.get('entradas')
    if not isinstance(crudas, list) or len(crudas) > MAX_ENTRADAS:
        raise ErrorExportacion('El calendario descargado no tiene una lista de entradas válida.')
    entradas, problemas = [], []
    for i, d in enumerate(crudas):
        try:
            entradas.append(entrada_desde_dict(d))
        except ValueError as exc:
            problemas.append(f'entrada {i}: {exc}')
    return Calendario(entradas), problemas


def dias_de_antiguedad(datos, hoy=None):
    """Días desde que el servidor generó el archivo (None si no se puede saber)."""
    try:
        g = datetime.strptime(datos['generado'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    hoy = hoy or datetime.now(timezone.utc)
    return max(0, (hoy - g).days)


# ── descarga y guardado ───────────────────────────────────────────────────
def _url_permitida(url):
    p = urllib.parse.urlparse(url)
    if p.scheme == 'https' and p.hostname:
        return True
    return p.scheme == 'http' and p.hostname in _HOSTS_HTTP_PERMITIDOS   # solo para pruebas locales


def cargar_archivo(ruta):
    """Lee un calendario guardado antes con descargar_calendario()."""
    with open(ruta, 'r', encoding='utf-8') as f:
        return json.load(f)


def descargar_calendario(url=URL_OFICIAL, destino=None, timeout=15):
    """
    Baja el calendario del servidor, lo valida y (si se pide) lo guarda en `destino`.
    Si la descarga falla o el contenido no sirve, NO toca el archivo que ya hubiera:
    el plugin puede seguir usando la última copia buena.
    """
    if not _url_permitida(url):
        raise ErrorExportacion('La dirección del calendario tiene que ser https.')
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'foja-plugin-plazos/1'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not _url_permitida(r.geturl()):   # una redirección no puede bajar a http
                raise ErrorExportacion('El servidor redirigió el calendario a una dirección no segura.')
            crudo = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise ErrorExportacion(f'No se pudo bajar el calendario: {exc}')
    if len(crudo) > MAX_BYTES:
        raise ErrorExportacion('El calendario descargado es demasiado grande.')
    try:
        datos = json.loads(crudo.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ErrorExportacion('La respuesta no es un JSON válido.')
    calendario_desde_exportacion(datos)   # valida el archivo entero antes de guardarlo
    if destino:
        carpeta = os.path.dirname(os.path.abspath(destino))
        os.makedirs(carpeta, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=carpeta, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(datos, f, ensure_ascii=False, indent=1)
            os.replace(tmp, destino)   # reemplazo atómico: nunca queda un archivo a medias
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    return datos


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    destino = argv[0] if argv else 'calendario_foja.json'
    url = argv[1] if len(argv) > 1 else URL_OFICIAL
    try:
        datos = descargar_calendario(url, destino)
        cal, problemas = calendario_desde_exportacion(datos)
    except ErrorExportacion as exc:
        print(f'Error: {exc}')
        return 1
    print(f'Calendario guardado en {destino}: {len(datos["entradas"])} entradas, generado {datos["generado"]}.')
    for p in problemas:
        print(f'  (se omitió) {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
