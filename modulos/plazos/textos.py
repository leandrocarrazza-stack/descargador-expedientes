"""
Lectura de fechas escritas en castellano, tal como aparecen en el sitio del
STJER: "1 de Enero", "16 y 17 de Febrero", "1 al 31 de Enero",
"23 y 24 de octubre de 2025", "viernes 31 de este mes", "hoy", "mañana".
"""

import re
from datetime import date, timedelta

from modulos.plazos.localidades import normalizar

MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
    'julio': 7, 'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12,
}
_MES_RE = '|'.join(MESES)

DIAS_SEMANA = {'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3, 'viernes': 4, 'sabado': 5, 'domingo': 6}

# "23 y 24 de octubre de 2025", "1 al 31 de enero", "2 y 3 de abril", "1º de mayo"
# (el grupo de días repite como mucho 8 veces: evita backtracking explosivo con textos raros)
_FECHAS_RE = re.compile(
    r'(?P<dias>\d{1,2}(?:\s*(?:,|y|e|al|a)\s*\d{1,2}){0,8})\s+de\s+(?P<mes>' + _MES_RE + r')'
    r'(?:\s+(?:(?:de|del)\s+)?(?:ano\s+)?(?P<anio>\d{4})\b)?'
)
# "31 de este mes", "17 de ese mes"
_DIA_ESTE_MES_RE = re.compile(r'(?P<dia>\d{1,2})\s+de\s+(?:este|ese)\s+mes')


def _limpiar(texto):
    """
    Normaliza y saca los ordinales ("1º", "1°", "1ro", "1er.") para que
    matcheen las regex. Ojo: normalizar() convierte "º" en "o".
    """
    t = normalizar(texto)
    t = re.sub(r'(\d)(?:o|°|ro|er)\b\.?', r'\1', t)
    return t


def _dias_de_grupo(grupo):
    """
    '16 y 17' → [16, 17]; '1 al 31' → [1..31]; '23, 24 y 25' → [23, 24, 25];
    '6, 7 y 10 al 12' → [6, 7, 10, 11, 12] (listas y rangos mezclados).
    """
    dias = []
    for desde, hasta in re.findall(r'(\d{1,2})(?:\s*(?:al|a)\s*(\d{1,2}))?', grupo):
        if hasta:
            if int(hasta) < int(desde):
                continue
            dias += list(range(int(desde), int(hasta) + 1))
        else:
            dias.append(int(desde))
    return dias


def _fechas_con_anio(texto, anio_por_defecto):
    """Como fechas_en_texto, pero devuelve (fecha, el_texto_decia_el_año)."""
    t = _limpiar(texto)
    fechas = []
    for m in _FECHAS_RE.finditer(t):
        mes = MESES[m.group('mes')]
        anio = int(m.group('anio')) if m.group('anio') else anio_por_defecto
        for dia in _dias_de_grupo(m.group('dias')):
            try:
                fechas.append((date(anio, mes, dia), bool(m.group('anio'))))
            except ValueError:
                pass
    return fechas


def fechas_en_texto(texto, anio_por_defecto):
    """
    Todas las fechas (date) mencionadas en el texto, en el orden en que aparecen.
    Si una fecha no dice el año, se usa `anio_por_defecto`. Fechas imposibles
    (ej. 31 de febrero) se descartan.
    """
    return [f for f, _ in _fechas_con_anio(texto, anio_por_defecto)]


def fechas_en_noticia(texto, fecha_publicacion):
    """
    Fechas de un anuncio de inhábil, interpretadas respecto de la fecha en que
    se publicó la noticia:
    - "15 de septiembre" sin año → año de la publicación; si así quedaría más
      de 60 días en el pasado (ej. noticia de diciembre que habla de "2 de
      enero"), se toma el año siguiente.
    - "31 de este mes" → mes de la publicación.
    - Si no hay ninguna fecha explícita: "hoy", "mañana", "pasado mañana",
      "hoy y mañana" o un día de la semana ("el lunes") → el próximo.
    """
    pub = fecha_publicacion
    fechas = []
    for f, anio_explicito in _fechas_con_anio(texto, pub.year):
        if f < pub - timedelta(days=60) and not anio_explicito:
            try:
                f = f.replace(year=f.year + 1)
            except ValueError:
                continue
        fechas.append(f)

    t = _limpiar(texto)
    for m in _DIA_ESTE_MES_RE.finditer(t):
        try:
            fechas.append(date(pub.year, pub.month, int(m.group('dia'))))
        except ValueError:
            pass

    if fechas:
        return sorted(set(fechas))

    relativas = []
    if re.search(r'\bhoy\b', t):
        relativas.append(pub)
    if re.search(r'\bpasado manana\b', t):
        relativas.append(pub + timedelta(days=2))
    elif re.search(r'(?<!\bla )(?<!\besta )(?<!\bde la )\bmanana\b', t):
        # "mañana" como día, no "por la mañana" / "esta mañana" / "horas de la mañana"
        relativas.append(pub + timedelta(days=1))
    if relativas:
        return sorted(set(relativas))

    # "el lunes", "el proximo viernes": el próximo día con ese nombre (sin contar hoy)
    m = re.search(r'\b(?:el\s+)?(?:proximo\s+)?(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b', t)
    if m:
        objetivo = DIAS_SEMANA[m.group(1)]
        dias = (objetivo - pub.weekday()) % 7 or 7
        return [pub + timedelta(days=dias)]
    return []


def oraciones(texto):
    """Divide un texto en oraciones (aprox.: por punto seguido de espacio)."""
    return [o.strip() for o in re.split(r'(?<=[.;])\s+', texto or '') if o.strip()]
