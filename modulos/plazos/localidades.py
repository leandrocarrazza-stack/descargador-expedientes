"""
Localidades de Entre Ríos con organismos judiciales, agrupadas por departamento.

Se usa para:
- el selector de "localidad" del contador (feriados locales / santos patronos),
- reconocer en las noticias del STJER a qué localidades afecta un inhábil
  ("El 7 de octubre será inhábil judicial en Paraná, Crespo, ...").

La página oficial de inhábiles no trae el departamento de cada localidad, por
eso la tabla se mantiene acá a mano.
"""

import re
import unicodedata

# Departamento → localidades (nombre "oficial" que se muestra en el selector)
DEPARTAMENTOS = {
    'Colón': ['Colón', 'San José', 'Villa Elisa'],
    'Concordia': ['Concordia'],
    'Diamante': ['Diamante', 'General Ramírez', 'Libertador San Martín'],
    'Federación': ['Chajarí', 'Federación', 'San Jaime de la Frontera', 'Villa del Rosario'],
    'Federal': ['Federal', 'Sauce de Luna'],
    'Feliciano': ['San José de Feliciano'],
    'Gualeguay': ['Gualeguay', 'General Galarza'],
    'Gualeguaychú': ['Gualeguaychú', 'Larroque', 'Urdinarrain'],
    'Islas del Ibicuy': ['Villa Paranacito', 'Ibicuy'],
    'La Paz': ['La Paz', 'Alcaraz', 'Bovril', 'Santa Elena'],
    'Nogoyá': ['Nogoyá', 'Hernández', 'Lucas González'],
    'Paraná': ['Paraná', 'Cerrito', 'Crespo', 'Hasenkamp', 'Hernandarias', 'María Grande',
               'Oro Verde', 'Pueblo Brugo', 'Seguí', 'Viale', 'Villa Urquiza'],
    'San Salvador': ['San Salvador', 'General Campos'],
    'Tala': ['Rosario del Tala', 'Gobernador Mansilla', 'Maciá'],
    'Uruguay': ['Concepción del Uruguay', 'Basavilbaso', 'Caseros', 'Villa Mantero', 'Villa San Marcial'],
    'Victoria': ['Victoria'],
    'Villaguay': ['Villaguay', 'Villa Clara', 'Villa Domínguez'],
}

# Abreviaturas o variantes que usa el sitio del STJER → nombre oficial
ALIAS = {
    'c. del uruguay': 'Concepción del Uruguay',
    'c del uruguay': 'Concepción del Uruguay',
    'gdor. mansilla': 'Gobernador Mansilla',
    'gral. campos': 'General Campos',
    'gral. ramirez': 'General Ramírez',
    'galarza': 'General Galarza',
    'tala': 'Rosario del Tala',
    'feliciano': 'San José de Feliciano',
    'villa de rosario': 'Villa del Rosario',
    'c.del uruguay': 'Concepción del Uruguay',
    'villa gobernador dominguez': 'Villa Domínguez',
}


def normalizar(texto):
    """Minúsculas, sin tildes y con espacios simples (para comparar nombres)."""
    texto = unicodedata.normalize('NFKD', texto or '')
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texto).strip().lower()


# Índice normalizado: nombre o alias → nombre oficial
_INDICE = {}
DEPARTAMENTO_DE = {}
for _depto, _locs in DEPARTAMENTOS.items():
    for _loc in _locs:
        _INDICE[normalizar(_loc)] = _loc
        DEPARTAMENTO_DE[_loc] = _depto
for _alias, _loc in ALIAS.items():
    _INDICE[normalizar(_alias)] = _loc


def localidad_oficial(nombre):
    """Devuelve el nombre oficial de una localidad (o None si no se conoce)."""
    return _INDICE.get(normalizar(nombre))


def departamento_de(localidad):
    """Departamento judicial de una localidad oficial (o None)."""
    return DEPARTAMENTO_DE.get(localidad)


def departamento_oficial(nombre):
    for d in DEPARTAMENTOS:
        if normalizar(d) == normalizar(nombre):
            return d
    return None


def buscar_localidades(texto):
    """
    Lista de localidades oficiales mencionadas en un texto, en orden de
    aparición. Busca primero los nombres más largos ("San José de Feliciano"
    antes que "San José") y tacha lo encontrado para no contarlo dos veces.
    """
    norm = normalizar(texto)
    encontradas = []
    for clave in sorted(_INDICE, key=len, reverse=True):
        # Los alias cortos y ambiguos ("tala", "galarza", "feliciano") sí
        # sirven: el sitio los usa así en títulos ("en Gualeguay y Galarza").
        patron = r'(?<![\w])' + re.escape(clave) + r'(?![\w])'
        m = re.search(patron, norm)
        if m:
            encontradas.append((m.start(), _INDICE[clave]))
            norm = norm[:m.start()] + ('#' * len(clave)) + norm[m.end():]
    vistos = []
    for _, loc in sorted(encontradas):
        if loc not in vistos:
            vistos.append(loc)
    return vistos
