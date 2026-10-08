"""
Sincronización automática del calendario con el sitio oficial del STJER.

Fuentes (solo jusentrerios.gov.ar):
1. Página "Inhábiles y feriados": dos tablas (feriados provinciales y
   nacionales / feriados locales por localidad). No indica el año: se asume
   el año en curso y se valida contra la fecha de Pascua (si el Viernes
   Santo de la página no coincide, la página es de otro año y se descarta).
2. Feed RSS de noticias: el STJER anuncia ahí los inhábiles por acuerdo o
   resolución ("Mañana será inhábil judicial…") y la feria de julio.
3. Página de la feria judicial.

Las funciones de parseo/clasificación son puras (reciben texto, devuelven
datos) para poder testearlas sin red ni base. `sincronizar()` es la que
toca la base.

Criterio de seguridad para el cómputo (el error peligroso es alargar un
plazo de más): un inhábil se descuenta solo si alcanza a toda la provincia,
a un departamento, a una localidad completa o a un fuero. Si es de un
organismo puntual, o el texto es ambiguo, NO se descuenta: se muestra como
aviso o queda guardado inactivo para que el admin lo revise.
"""

import logging
import re
import threading
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from modulos.plazos import reglas, textos
from modulos.plazos.calculo import hoy_argentina
from modulos.plazos.localidades import (
    buscar_localidades, departamento_de, departamento_oficial, localidad_oficial,
    normalizar,
)
from modulos.plazos.reglas import Entrada

logger = logging.getLogger(__name__)

DOMINIO_OFICIAL = 'jusentrerios.gov.ar'
URL_PAGINA = 'https://www.jusentrerios.gov.ar/inhabiles-y-feriados/'
URL_FERIA = 'https://www.jusentrerios.gov.ar/feria-judicial/'
URL_FEED = 'https://www.jusentrerios.gov.ar/feed/'
URL_BUSQUEDA_RSS = 'https://www.jusentrerios.gov.ar/?s={q}&feed=rss2&paged={p}'
USER_AGENT = 'FojaBot/1.0 (+https://foja.com.ar; calendario de plazos judiciales)'
TIMEOUT = 25

# Cuántos días hacia atrás se miran las noticias en una corrida normal / la primera vez
DIAS_RSS = 150
DIAS_RSS_PRIMERA_VEZ = 800


class ErrorSync(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════
#  1) PÁGINA "INHÁBILES Y FERIADOS"
# ═══════════════════════════════════════════════════════════════════════════

_DIAS_SEM = textos.DIAS_SEMANA


def fecha_relativa(texto, anio):
    """'Último domingo de noviembre', '1er. domingo de octubre' → date (o None)."""
    t = textos._limpiar(texto)
    # (textos._limpiar ya convirtió "1er." en "1")
    m = re.search(r'(ultimo|primer\w*|[1-4])\s+(lunes|martes|miercoles|jueves|viernes|sabado|domingo)'
                  r'\s+de\s+(' + textos._MES_RE + ')', t)
    if not m:
        return None
    n = -1 if m.group(1) == 'ultimo' else (1 if m.group(1).startswith('primer') else int(m.group(1)))
    dow, mes = _DIAS_SEM[m.group(2)], textos.MESES[m.group(3)]
    if n == -1:
        d = date(anio + (mes == 12), (mes % 12) + 1, 1) - timedelta(days=1)
        while d.weekday() != dow:
            d -= timedelta(days=1)
        return d
    d = date(anio, mes, 1)
    while d.weekday() != dow:
        d += timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))


def _tipo_fila(motivo):
    m = normalizar(motivo)
    if 'no laborable' in m:
        return 'no_laborable'
    if 'inhabil' in m:
        return 'inhabil_judicial'
    if any(k in m for k in ('ley 7285', 'san miguel', 'patrono de la provincia', 'empleado judicial',
                            'magistratura', 'abogacia', 'caseros')):
        return 'feriado_provincial'
    return 'feriado_nacional'


def _filas_tabla(tabla):
    filas = []
    for tr in tabla.find_all('tr'):
        celdas = [c.get_text(' ', strip=True)[:200] for c in tr.find_all(['td', 'th'])]
        if celdas and not (normalizar(celdas[0]) in ('fecha', 'localidad')):
            filas.append(celdas)
    return filas


def parsear_pagina(html, anio):
    """
    Lee la página de inhábiles y feriados.

    Devuelve (entradas, problemas): `entradas` es una lista de Entrada del
    año `anio`; `problemas` es una lista de textos con filas que no se
    pudieron interpretar. Lanza ErrorSync si la página no parece válida o
    corresponde a otro año (ver validaciones).
    """
    soup = BeautifulSoup(html, 'html.parser')
    tablas = soup.find_all('table')
    if len(tablas) < 2:
        raise ErrorSync(f'La página tiene {len(tablas)} tablas (se esperaban 2): cambió el formato.')

    generales = _filas_tabla(tablas[0])
    locales = _filas_tabla(tablas[1])
    if len(generales) < 15 or len(locales) < 20:
        raise ErrorSync(f'Pocas filas en las tablas ({len(generales)} generales, {len(locales)} locales).')

    textos_motivo = {normalizar(f[1]) for f in generales if len(f) > 1}
    for ancla in ('ano nuevo', 'navidad'):
        if not any(ancla in t for t in textos_motivo):
            raise ErrorSync(f'Falta el feriado "{ancla}" en la tabla: la página parece incompleta.')

    # Validación de año (la página no lo indica): el Viernes Santo tiene que coincidir con la
    # Pascua de `anio`. Falla cerrado: si no se encuentra la fila o no se puede leer la fecha,
    # se rechaza la página en vez de guardar una tabla que podría ser de otro año.
    p = reglas.pascua(anio)
    fila_santo = next((f for f in generales if len(f) > 1 and 'viernes santo' in normalizar(f[1])), None)
    if not fila_santo:
        raise ErrorSync('No se encontró la fila de Jueves/Viernes Santo: no se puede confirmar de qué año es la tabla.')
    fechas = textos.fechas_en_texto(fila_santo[0], anio)
    if p - timedelta(days=2) not in fechas:
        raise ErrorSync(f'El Viernes Santo de la página ({fila_santo[0]}) no coincide con la Pascua de {anio} '
                        f'({p - timedelta(days=2)}): la página todavía muestra otro año.')

    entradas, problemas = [], []
    for celdas in generales:
        if len(celdas) < 2:
            continue
        fecha_txt, motivo = celdas[0], celdas[1]
        if re.search(r'\bferia\b', normalizar(motivo)):
            continue   # la feria de enero es regla; la de julio se toma de las noticias
        fechas = textos.fechas_en_texto(fecha_txt, anio)
        if not fechas:
            problemas.append(f'{fecha_txt} · {motivo}')
            continue
        tipo = _tipo_fila(motivo)
        for f in fechas:
            entradas.append(Entrada(desde=f, hasta=f, tipo=tipo, motivo=motivo.strip(), fuente='scraper',
                                    fuente_url=URL_PAGINA))

    for celdas in locales:
        if len(celdas) < 3:
            continue
        nombre, fecha_txt, santo = celdas[0], celdas[1], celdas[2]
        f = fecha_relativa(fecha_txt, anio)
        fechas = [f] if f else textos.fechas_en_texto(fecha_txt, anio)
        if not fechas:
            problemas.append(f'{nombre} · {fecha_txt}')
            continue
        santo = santo.strip().strip('".“”')
        loc = localidad_oficial(nombre)
        depto = departamento_oficial(nombre)
        motivo = f'Feriado local de {loc or depto or nombre}' + (f' ({santo})' if santo and santo != nombre else '')
        if loc:
            kw = dict(alcance='localidad', localidad=loc, departamento=departamento_de(loc))
        elif depto:
            kw = dict(alcance='departamento', departamento=depto)
        else:
            problemas.append(f'Localidad desconocida: {nombre}')
            continue
        for fch in fechas:
            entradas.append(Entrada(desde=fch, hasta=fch, tipo='feriado_local', motivo=motivo,
                                    fuente='scraper', fuente_url=URL_PAGINA, **kw))

    if len(problemas) > 0.25 * (len(generales) + len(locales)):
        raise ErrorSync(f'Demasiadas filas ilegibles ({len(problemas)}): {problemas[:5]}')
    return entradas, problemas


# ═══════════════════════════════════════════════════════════════════════════
#  2) NOTICIAS (RSS)
# ═══════════════════════════════════════════════════════════════════════════

_NS = {'content': 'http://purl.org/rss/1.0/modules/content/'}
_ART = timezone(timedelta(hours=-3))   # Argentina no tiene horario de verano


def url_oficial(url):
    """True si es una URL https del dominio oficial (o un subdominio)."""
    p = urlparse(url or '')
    host = p.hostname or ''
    return p.scheme == 'https' and (host == DOMINIO_OFICIAL or host.endswith('.' + DOMINIO_OFICIAL))


def parsear_rss(xml_texto):
    """
    Lista de dicts {titulo, link, fecha (datetime), texto} de un feed RSS 2.0.
    La fecha queda en hora de Argentina (el feed viene en UTC): importa para
    interpretar "hoy", "mañana" o "el lunes" en las noticias de la noche.
    """
    raiz = ET.fromstring(xml_texto)
    items = []
    for it in raiz.findall('./channel/item'):
        cuerpo = it.find('content:encoded', _NS)
        html = (cuerpo.text if cuerpo is not None and cuerpo.text else it.findtext('description')) or ''
        texto = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
        try:
            dt = parsedate_to_datetime(it.findtext('pubDate'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            fecha = dt.astimezone(_ART).replace(tzinfo=None)
        except Exception:
            continue
        items.append({
            'titulo': (it.findtext('title') or '').strip()[:300],
            'link': (it.findtext('link') or '').strip(),
            'fecha': fecha,
            'texto': re.sub(r'\s+', ' ', texto),
        })
    return items


_TODA_LA_PROVINCIA = re.compile(
    r'todas las dependencias judiciales|tribunales de la provincia|toda la provincia|'
    r'organismos judiciales de la provincia|regimen de rotacion anual|todos los organismos judiciales de entre rios|'
    r'todo el poder judicial')
_TODOS_LOS_ORGANISMOS = re.compile(
    r'todos los (organismos|juzgados|tribunales)|todas las (dependencias|oficinas)|'
    r'los tribunales de (esa|esas|la|las)\b|los organismos judiciales de')
_ORGANISMO = re.compile(
    r'\b(juzgados?|oficinas?|equipos? tecnicos?|camaras?|ministerios?|defensorias?|fiscalias?|centros? de mediacion|'
    r'registros?|salas?|tribunal(es)? de (juicio|apelaciones|familia)|cuerpo medico|secretarias?|vocalias?)\b')
_JURISDICCION = re.compile(r'\b(organismos|dependencias|juzgados|tribunales)\b[^.]{0,40}\bjurisdiccion\b')
_PARCIAL = re.compile(r'desde las \d|a partir de las \d|desde la hora|a partir de la hora|a partir del mediodia|'
                      r'desde el mediodia|por la tarde|turno tarde|horas de la tarde|media manana')
_FUERO = re.compile(r'fuero\s+(?:del\s+|de\s+)?(trabajo|laboral|civil|penal|familia|contencioso)')
_FUEROS = {'trabajo': 'laboral', 'laboral': 'laboral', 'civil': 'civil', 'penal': 'penal',
           'familia': 'familia', 'contencioso': 'contencioso'}
_EXCEPTO = re.compile(r'\b(excepto|salvo|con excepcion|a excepcion)\b')
# Avisos que anulan o aclaran un inhábil anterior ("no será inhábil", "se deja sin efecto"): jamás se
# interpretan como un inhábil nuevo
_NEGACION = re.compile(r'\bno (sera|seran|habra|se considerara) (dia |dias )?(inhabil|inhabiles)|'
                       r'\bsin efecto\b|\bse revoc|\bse anul|\bdeja de ser inhabil|'
                       r'\bse (suspende|suspendio|levanta|levanto) (el|la) (dia |jornada )?inhabil|'
                       r'\bno corresponde (declarar )?(el dia )?inhabil')
# Frases de contraste ("en el resto de la provincia la actividad será normal"): lo que sigue no describe el alcance
_CONTRASTE = r'\b(?:en el resto de|el resto de|mientras que|en tanto que|sin embargo|a diferencia de)\b'
_CALLE = re.compile(r'\b(?:calle|avenida|avda\.?|av\.?|esquina|pasaje|bv\.?|bulevar|ruta)\s+\S+(?:\s+\S+)?', re.I)
_FERIA_RE = re.compile(r'tendra lugar del (\d{1,2}) al (\d{1,2}) de (julio|enero) de (\d{4})')


def _limpiar_noticia(texto):
    """Saca el ruido del final de las noticias: visor de PDF, firma con fecha ("3 de marzo de 2026 SIC-STJER") y direcciones."""
    texto = re.split(r'Cargando\.\.\.', texto)[0]
    texto = re.sub(r'\d{1,2}\s+de\s+\w+\s+(?:de\s+)?\d{4}[\s.\-–—]*(?:SIC|Servicio de Informaci).*$', '', texto,
                   flags=re.S | re.I)
    texto = _CALLE.sub(' ', texto)
    return texto


def _relevante(titulo):
    t = normalizar(titulo)
    return bool(re.search(r'\binhabil\b|\binhabiles\b|\bdia inhabil\b', t))


def feria_en_texto(texto):
    """'Tendrá lugar del 6 al 17 de julio de 2026' → Entrada de la feria (o None)."""
    m = _FERIA_RE.search(normalizar(texto))
    if not m or m.group(3) != 'julio':
        return None
    anio = int(m.group(4))
    try:
        return Entrada(desde=date(anio, 7, int(m.group(1))), hasta=date(anio, 7, int(m.group(2))),
                       tipo='feria_julio', motivo='Feria judicial de julio', fuente='scraper')
    except ValueError:
        return None


def clasificar_noticia(titulo, texto, fecha_pub, link=None):
    """
    Interpreta una noticia del STJER.

    Devuelve {'estado': 'ok'|'ambiguo'|'ignorar', 'entradas': [Entrada], 'nota': str}.
    - 'ok': se pudieron determinar fechas y alcance.
    - 'ambiguo': parece un inhábil pero no se puede determinar con certeza (o es
      una revocación) → no se aplica; lo revisa el admin.
    - 'ignorar': no es un inhábil.

    Criterio de seguridad: un inhábil solo se DESCUENTA con evidencia positiva
    de que alcanza a todo (provincia, "todos los organismos" de una ciudad,
    un fuero). Ante cualquier otra duda queda como aviso (organismo puntual).
    """
    if isinstance(fecha_pub, datetime):
        fecha_pub = fecha_pub.date()
    titulo = (titulo or '')[:300]
    link = link if url_oficial(link) else None

    # Feria de julio ("Autoridades judiciales de feria")
    if 'feria' in normalizar(titulo):
        f = feria_en_texto(texto)
        if f:
            return {'estado': 'ok', 'nota': 'feria de julio', 'entradas': [_con_fuente(f, link)]}
    if not _relevante(titulo):
        return {'estado': 'ignorar', 'entradas': [], 'nota': ''}

    cabeza = f'{titulo}. {_limpiar_noticia(texto)[:700]}'
    n = normalizar(cabeza)
    if _NEGACION.search(n):
        return {'estado': 'ambiguo', 'entradas': [], 'nota': 'Parece una anulación o aclaración de un inhábil anterior'}

    fechas = textos.fechas_en_noticia(cabeza, fecha_pub)
    # Un inhábil se anuncia con pocos días de anticipación: se descartan fechas
    # lejanas (números de resolución, años viejos mencionados de pasada, etc.)
    fechas = [f for f in fechas if -4 <= (f - fecha_pub).days <= 150]
    if not fechas:
        return {'estado': 'ambiguo', 'entradas': [], 'nota': 'No se encontró la fecha del inhábil'}

    motivo = re.sub(r'\s+', ' ', titulo).strip()
    base = dict(tipo='inhabil_judicial', motivo=motivo, fuente='scraper', fuente_url=link)

    def armar(**kw):
        return [Entrada(desde=f, hasta=f, **base, **kw) for f in fechas]

    # Solo se analiza lo que viene antes de una frase de contraste ("en el resto de la provincia ... normal")
    n_alcance = re.split(_CONTRASTE, normalizar(_CALLE.sub(' ', cabeza)))[0]
    parcial = any(_PARCIAL.search(o) for o in textos.oraciones(n_alcance) if 'inhabil' in o)
    fm = _FUERO.search(n_alcance)
    fuero = _FUEROS[fm.group(1)] if fm else None
    if fm and _EXCEPTO.search(n_alcance):
        return {'estado': 'ambiguo', 'entradas': [], 'nota': 'Menciona excepciones por fuero'}
    toda = bool(_TODA_LA_PROVINCIA.search(n_alcance))
    todos_org = bool(_TODOS_LOS_ORGANISMOS.search(n_alcance))
    organismo = bool(_ORGANISMO.search(n_alcance))
    locs = buscar_localidades(n_alcance)
    locs_titulo = buscar_localidades(titulo)

    if toda and not locs_titulo:
        return {'estado': 'ok', 'nota': 'provincia',
                'entradas': armar(alcance='provincia', fuero=fuero, descuenta=not parcial)}
    if fm and not locs:
        if organismo and not todos_org:
            return {'estado': 'ambiguo', 'entradas': [], 'nota': 'Un organismo y un fuero, sin localidad'}
        # "inhábil judicial para el fuero del Trabajo" (en toda la provincia)
        return {'estado': 'ok', 'nota': 'fuero', 'entradas': armar(alcance='provincia', fuero=fuero, descuenta=not parcial)}

    if locs:
        if todos_org:
            if parcial:
                # Medio día: no se descuenta, pero se avisa en esas localidades
                ents = []
                for loc in locs:
                    ents += armar(alcance='organismo', localidad=loc, departamento=departamento_de(loc), descuenta=False)
                return {'estado': 'ok', 'nota': 'parcial', 'entradas': ents}
            if _JURISDICCION.search(n_alcance):
                deptos = []
                for loc in locs:
                    d = departamento_de(loc)
                    if d and d not in deptos:
                        deptos.append(d)
                ents = []
                for d in deptos:
                    ents += armar(alcance='departamento', departamento=d, fuero=fuero)
                return {'estado': 'ok', 'nota': 'departamento', 'entradas': ents}
            ents = []
            for loc in locs:
                ents += armar(alcance='localidad', localidad=loc, departamento=departamento_de(loc), fuero=fuero)
            return {'estado': 'ok', 'nota': 'localidades', 'entradas': ents}
        # Sin evidencia de que alcance a todos los organismos: se trata como organismo puntual (solo aviso).
        # Si el título nombra la localidad, solo esa: el cuerpo suele mencionar otras ciudades.
        ents = []
        for loc in (locs_titulo or locs):
            ents += armar(alcance='organismo', localidad=loc, departamento=departamento_de(loc), descuenta=False)
        return {'estado': 'ok', 'nota': 'organismo', 'entradas': ents}

    return {'estado': 'ambiguo', 'entradas': [], 'nota': 'No se pudo determinar a qué organismos o localidades alcanza'}


def _con_fuente(e, link):
    return Entrada(**{**e.__dict__, 'fuente_url': link})


# ═══════════════════════════════════════════════════════════════════════════
#  3) RED + BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════

MAX_BYTES = 3_000_000
MAX_REDIRECCIONES = 3


def _get(url):
    """
    GET a una URL https del dominio oficial. Los redirects se siguen a mano
    revalidando el dominio en cada salto, y la respuesta tiene un tope de tamaño.
    """
    import requests
    actual = url
    for _ in range(MAX_REDIRECCIONES + 1):
        if not url_oficial(actual):
            raise ErrorSync(f'URL fuera del dominio oficial: {actual}')
        r = requests.get(actual, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT,
                         allow_redirects=False, stream=True)
        try:
            if r.status_code in (301, 302, 303, 307, 308):
                actual = urljoin(actual, r.headers.get('Location', ''))
                continue
            r.raise_for_status()
            contenido = bytearray()
            for trozo in r.iter_content(65536):
                contenido += trozo
                if len(contenido) > MAX_BYTES:
                    raise ErrorSync('La respuesta es demasiado grande')
            return bytes(contenido).decode('utf-8', errors='replace')
        finally:
            r.close()
    raise ErrorSync('Demasiadas redirecciones')


def _log(db, fuente, resultado, filas=0, agregados=0, quitados=0, detalle=None):
    from modulos.models import CalendarioSyncLog
    db.session.add(CalendarioSyncLog(fuente=fuente, resultado=resultado, filas=filas, agregados=agregados,
                                     quitados=quitados, detalle=(detalle or '')[:4000]))
    db.session.commit()


def _firma(e):
    return (e.desde, e.hasta, e.tipo, e.motivo, e.alcance, e.departamento, e.localidad, e.fuero, e.descuenta)


def aplicar_entradas(db, origen, entradas, activo=True):
    """
    Reemplaza lo que el sincronizador cargó antes bajo `origen` por `entradas`.
    No toca nada con fuente='manual'. Si por una corrida simultánea quedaron
    filas duplicadas, deja una sola. Devuelve (agregados, quitados).
    """
    from modulos.models import DiaInhabil
    actuales = DiaInhabil.query.filter_by(origen=origen, fuente='scraper').all()
    por_firma = {}
    for f in actuales:
        por_firma.setdefault(_firma(f.a_entrada()), []).append(f)
    firmas_nuevas = {_firma(e): e for e in entradas}
    quitados = []
    for firma, filas in por_firma.items():
        quitados += filas if firma not in firmas_nuevas else filas[1:]
    agregados = [e for firma, e in firmas_nuevas.items() if firma not in por_firma]
    for f in quitados:
        db.session.delete(f)
    for e in agregados:
        db.session.add(DiaInhabil(
            fecha_desde=e.desde, fecha_hasta=e.hasta, tipo=e.tipo, motivo=e.motivo[:500], alcance=e.alcance,
            departamento=e.departamento, localidad=e.localidad, fuero=e.fuero, descuenta=e.descuenta,
            fuente='scraper', origen=origen, fuente_url=e.fuente_url, activo=activo))
    db.session.commit()
    return len(agregados), len(quitados)


def avisar_admin(asunto, cuerpo):
    """Mail al admin (config.CONTACT_EMAIL). Nunca rompe la sincronización."""
    try:
        import config
        if not config.CONTACT_EMAIL:
            return
        from flask_mail import Message
        from modulos.extensions import mail
        mail.send(Message(subject=f'[Foja · Plazos] {asunto}'.replace('\n', ' ')[:200],
                          recipients=[config.CONTACT_EMAIL], body=cuerpo))
    except Exception:
        logger.warning('[PLAZOS] No se pudo mandar el aviso al admin', exc_info=True)


def sincronizar_pagina(db, hoy=None, html=None):
    """Descarga y aplica la página de inhábiles y feriados del año en curso."""
    from modulos.models import CalendarioSyncLog
    hoy = hoy or hoy_argentina()
    try:
        html = html if html is not None else _get(URL_PAGINA)
        entradas, problemas = parsear_pagina(html, hoy.year)
    except Exception as e:
        previo = (CalendarioSyncLog.query.filter_by(fuente='pagina')
                  .order_by(CalendarioSyncLog.fecha.desc()).first())
        repetido = bool(previo and previo.resultado == 'rechazado' and (previo.detalle or '')[:200] == str(e)[:200])
        _log(db, 'pagina', 'rechazado', detalle=str(e))
        if not repetido:   # un mismo rechazo se avisa una sola vez, no en cada reintento
            avisar_admin('La página de inhábiles no se pudo leer', f'{e}\n\nSe mantiene el calendario anterior.')
        logger.warning(f'[PLAZOS] Página rechazada: {e}')
        return {'resultado': 'rechazado', 'detalle': str(e)}
    ag, qu = aplicar_entradas(db, f'pagina:{hoy.year}', entradas)
    _log(db, 'pagina', 'ok' if (ag or qu) else 'sin_cambios', len(entradas), ag, qu, '\n'.join(problemas))
    return {'resultado': 'ok', 'filas': len(entradas), 'agregados': ag, 'quitados': qu, 'problemas': problemas}


def sincronizar_feria(db, html=None):
    """Lee la página de la feria judicial (fechas de la feria de julio)."""
    try:
        html = html if html is not None else _get(URL_FERIA)
        texto = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
        e = feria_en_texto(texto)
    except Exception as ex:
        _log(db, 'feria', 'error', detalle=str(ex))
        return {'resultado': 'error', 'detalle': str(ex)}
    if not e:
        # Se deja constancia igual: el hilo periódico usa el log para saber cuándo le toca de nuevo
        _log(db, 'feria', 'sin_cambios', detalle='La página de la feria no trae fechas')
        return {'resultado': 'sin_cambios'}
    e = _con_fuente(e, URL_FERIA)
    ag, qu = aplicar_entradas(db, f'feria:{e.desde.year}', [e])
    _log(db, 'feria', 'ok' if (ag or qu) else 'sin_cambios', 1, ag, qu)
    return {'resultado': 'ok', 'agregados': ag, 'quitados': qu}


def sincronizar_rss(db, hoy=None, xmls=None):
    """
    Lee las noticias del STJER y aplica los inhábiles / ferias anunciados.
    `xmls` (lista de textos RSS) permite testear sin red.
    """
    from modulos.models import DiaInhabil
    hoy = hoy or hoy_argentina()
    primera = DiaInhabil.query.filter(DiaInhabil.origen.like('rss:%')).count() == 0
    dias = DIAS_RSS_PRIMERA_VEZ if primera else DIAS_RSS
    paginas = 8 if primera else 2
    items, fallos = [], []
    try:
        if xmls is None:
            import requests
            xmls = [_get(URL_FEED)]
            for q in ('inh%C3%A1bil', 'feria+judicial'):
                for p in range(1, paginas + 1):
                    try:
                        xmls.append(_get(URL_BUSQUEDA_RSS.format(q=q, p=p)))
                    except requests.HTTPError as e:
                        if p > 1 and e.response is not None and e.response.status_code == 404:
                            break   # WordPress responde 404 cuando se acaban las páginas de resultados
                        fallos.append(f'{q} p{p}: {e}')
                        break
                    except Exception as e:
                        fallos.append(f'{q} p{p}: {e}')
                        break
        for x in xmls:
            items += parsear_rss(x)
    except Exception as e:
        _log(db, 'rss', 'error', detalle=str(e))
        logger.warning(f'[PLAZOS] RSS con error: {e}')
        return {'resultado': 'error', 'detalle': str(e)}

    vistos = {r[0] for r in db.session.query(DiaInhabil.origen).filter(DiaInhabil.origen.like('rss:%')).all()}
    nuevos, ambiguos, aplicados = 0, [], 0
    por_link = {}
    for it in items:
        por_link.setdefault(it['link'], it)
    for link, it in sorted(por_link.items(), key=lambda kv: kv[1]['fecha']):
        origen = f'rss:{link}'
        if origen in vistos or (hoy - it['fecha'].date()).days > dias:
            continue
        try:
            r = clasificar_noticia(it['titulo'], it['texto'], it['fecha'], link)
        except Exception as ex:
            logger.warning(f'[PLAZOS] No se pudo interpretar la noticia {link}: {ex}')
            r = {'estado': 'ambiguo', 'entradas': [], 'nota': f'Error al interpretarla ({type(ex).__name__})'}
        if r['estado'] == 'ignorar':
            continue
        nuevos += 1
        if r['estado'] == 'ok':
            aplicar_entradas(db, origen, r['entradas'])
            aplicados += len(r['entradas'])
        else:
            # Se guarda inactiva: sirve de marca para no reprocesarla y el admin la ve en el panel
            e = Entrada(desde=it['fecha'].date(), hasta=it['fecha'].date(), tipo='inhabil_judicial',
                        motivo=f"[REVISAR] {it['titulo']}", descuenta=False, fuente='scraper',
                        fuente_url=link if url_oficial(link) else None)
            aplicar_entradas(db, origen, [e], activo=False)
            ambiguos.append(f"{it['titulo']} — {link} ({r['nota']})")
    if ambiguos:
        avisar_admin('Inhábiles para revisar a mano',
                     'El sincronizador no pudo interpretar con certeza estas noticias (no se aplicaron). '
                     'Si corresponde, cargá el día a mano en /admin/calendario:\n\n' + '\n'.join(ambiguos))
    detalle = '\n'.join(ambiguos + [f'CONSULTA FALLIDA {f}' for f in fallos])
    if fallos:
        # Se aplicó lo que se pudo leer, pero se registra como error para que se reintente pronto
        _log(db, 'rss', 'error', nuevos, aplicados, 0, detalle)
        return {'resultado': 'error', 'nuevas': nuevos, 'aplicadas': aplicados, 'detalle': detalle}
    _log(db, 'rss', 'ok' if nuevos else 'sin_cambios', nuevos, aplicados, 0, detalle)
    return {'resultado': 'ok' if nuevos else 'sin_cambios', 'nuevas': nuevos, 'aplicadas': aplicados,
            'ambiguas': len(ambiguos)}


FUENTES_SYNC = ('pagina', 'feria', 'rss')
_FUENTES = {'pagina': sincronizar_pagina, 'feria': sincronizar_feria, 'rss': sincronizar_rss}
_LOCK = threading.Lock()   # una sola sincronización a la vez (hilo periódico y botón del admin)


def sincronizar_todo(db, fuentes=None):
    """Corre las fuentes indicadas (por defecto las tres). Cada una falla por separado sin afectar a las otras."""
    if not _LOCK.acquire(blocking=False):
        return {'en_curso': {'resultado': 'en_curso'}}
    try:
        resumen = {}
        for nombre in (fuentes or FUENTES_SYNC):
            try:
                resumen[nombre] = _FUENTES[nombre](db)
            except Exception as e:
                db.session.rollback()
                logger.exception(f'[PLAZOS] Falló la sincronización de {nombre}')
                try:
                    _log(db, nombre, 'error', detalle=f'{type(e).__name__}: {e}')
                except Exception:
                    db.session.rollback()
                resumen[nombre] = {'resultado': 'error', 'detalle': str(e)}
        return resumen
    finally:
        _LOCK.release()


# ═══════════════════════════════════════════════════════════════════════════
#  4) HILO DE FONDO
# ═══════════════════════════════════════════════════════════════════════════

REINTENTO_ERROR_HORAS = 6
REINTENTO_RECHAZO_HORAS = 24


def fuentes_pendientes(ultimos, ahora, cada_dias):
    """
    Qué fuentes hay que sincronizar ahora.

    ultimos: {fuente: (fecha_utc, resultado)} con el último intento de cada
    fuente; una fuente que nunca corrió no figura.
    - Nunca corrió → toca.
    - Último intento exitoso ('ok' / 'sin_cambios') → toca a los `cada_dias`.
    - 'error' (red, base) → se reintenta a las 6 horas.
    - 'rechazado' (la página cambió de formato o muestra otro año) → se reintenta
      a las 24 horas: no se arregla en un rato y no hay que molestar al sitio.
    """
    pendientes = []
    for fuente in FUENTES_SYNC:
        if fuente not in ultimos:
            pendientes.append(fuente)
            continue
        fecha, resultado = ultimos[fuente]
        if resultado == 'error':
            espera = timedelta(hours=REINTENTO_ERROR_HORAS)
        elif resultado == 'rechazado':
            espera = timedelta(hours=REINTENTO_RECHAZO_HORAS)
        else:
            espera = timedelta(days=cada_dias)
        if ahora - fecha >= espera:
            pendientes.append(fuente)
    return pendientes


def toca_sincronizar(ultimos, ahora, cada_dias):
    return bool(fuentes_pendientes(ultimos, ahora, cada_dias))


def _ultimos_intentos(db):
    from modulos.models import CalendarioSyncLog
    salida = {}
    for fuente in FUENTES_SYNC:
        ultimo = (CalendarioSyncLog.query.filter_by(fuente=fuente)
                  .order_by(CalendarioSyncLog.fecha.desc()).first())
        if ultimo:
            salida[fuente] = (ultimo.fecha, ultimo.resultado)
    return salida


def iniciar_sincronizacion_periodica(app, cada_dias=15, espera_inicial_seg=45, revisar_cada_seg=3600):
    """
    Mantiene el calendario al día en un hilo daemon (mismo patrón que
    iniciar_limpieza_periodica_pdfs en rutas/descargas.py): cada hora mira el
    log y sincroniza las fuentes que ya les toca (ver fuentes_pendientes).
    Mirar el log en vez de dormir 15 días seguidos hace que un redeploy no
    reinicie la cuenta: Render reinicia el proceso en cada deploy y la próxima
    sincronización se calcula desde la última registrada. Render corre un solo
    worker de gunicorn, así que no se duplica (y _LOCK evita solapamientos).
    """
    import time

    def _loop():
        time.sleep(espera_inicial_seg)   # que la app termine de arrancar
        while True:
            try:
                with app.app_context():
                    from modulos.database import db
                    pendientes = fuentes_pendientes(_ultimos_intentos(db), datetime.utcnow(), cada_dias)
                    if pendientes:
                        resumen = sincronizar_todo(db, pendientes)
                        logger.info(f'[PLAZOS] Sincronización de {pendientes}: {resumen}')
            except Exception:
                logger.exception('[PLAZOS] Error en el hilo de sincronización')
            time.sleep(revisar_cada_seg)

    threading.Thread(target=_loop, daemon=True, name='plazos-sync').start()


ultimo_hilo_manual = None


def lanzar_sincronizacion(app):
    """
    Sincroniza las tres fuentes en un hilo (para el botón "Sincronizar ahora": hacerlo
    dentro del request podría superar el límite de tiempo del proxy). Devuelve el hilo.
    """
    global ultimo_hilo_manual

    def _correr():
        with app.app_context():
            from modulos.database import db
            try:
                logger.info(f'[PLAZOS] Sincronización manual: {sincronizar_todo(db)}')
            except Exception:
                logger.exception('[PLAZOS] Error en la sincronización manual')

    ultimo_hilo_manual = threading.Thread(target=_correr, daemon=True, name='plazos-sync-manual')
    ultimo_hilo_manual.start()
    return ultimo_hilo_manual
