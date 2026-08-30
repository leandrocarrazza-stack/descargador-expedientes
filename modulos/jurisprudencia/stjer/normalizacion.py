"""
Normalizacion de texto juridico
===============================

Una sola definicion de normalizar_texto / tokenizar / STOP_WORDS para todo el
proyecto. modulos/jurisprudencia/tesauro.py re-exporta desde aca.

Por que se reescribio la version anterior
-----------------------------------------
La implementacion vieja era:

    nfd = normalize('NFD', texto.lower())
    return ''.join(c for c in nfd if ord(c) < 128 or c.isalpha())

Sacaba los acentos bien, pero `ord(c) < 128` conserva TODA la puntuacion
ASCII. Entonces "art. 1113, inc. 2)" se normalizaba a si mismo, y el match por
token de obtener_voces_para_consulta (comparaba `token in tokens_clave`) nunca
enganchaba con nada que tuviera puntuacion pegada. Ademas `c.isalpha()` dejaba
pasar letras no latinas.

Aca la puntuacion pasa a ser separador de tokens, que es lo que hace falta.
"""

import re
import unicodedata

# Stop-words castellanas. Es la union de la lista que estaba en tesauro.py con
# los conectores que aparecen todo el tiempo en consultas juridicas.
STOP_WORDS = frozenset(
    {
        "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos",
        "ante", "antes", "aquel", "aquella", "aquellas", "aquellos", "aqui",
        "asi", "aun", "aunque", "bajo", "bien", "cabe", "cada", "como", "con",
        "contra", "cual", "cuales", "cuando", "cuanto", "de", "del", "desde",
        "donde", "dos", "durante", "e", "el", "ella", "ellas", "ellos", "en",
        "entre", "era", "eran", "es", "esa", "esas", "ese", "eso", "esos",
        "esta", "estan", "estas", "este", "esto", "estos", "estoy", "fue",
        "fueron", "ha", "hace", "hacia", "han", "hasta", "hay", "la", "las",
        "le", "les", "lo", "los", "mas", "me", "mi", "mientras", "muy", "ni",
        "no", "nos", "o", "otra", "otras", "otro", "otros", "para", "pero",
        "poco", "por", "porque", "pues", "que", "quien", "quienes", "se",
        "segun", "ser", "si", "sido", "sin", "sobre", "solo", "son", "su",
        "sus", "tambien", "tan", "tanto", "te", "tiene", "tienen", "toda",
        "todas", "todo", "todos", "tras", "un", "una", "unas", "uno", "unos",
        "y", "ya",
    }
)

# Todo lo que no sea letra o digito pasa a ser separador.
_NO_ALFANUM = re.compile(r"[^0-9a-z]+")
_ESPACIOS = re.compile(r"\s+")


def normalizar_texto(texto: str) -> str:
    """
    Minusculas, sin acentos, sin puntuacion, espacios colapsados.

    La 'n' con virgulilla colapsa a 'n' a proposito: es lo mismo que hace el
    tokenizador de SQLite con `remove_diacritics 2`, asi que el texto guardado
    y la consulta se normalizan igual y "dano moral" encuentra "daño moral".

    >>> normalizar_texto("Art. 1113, inc. 2) del C.C.")
    'art 1113 inc 2 del c c'
    >>> normalizar_texto("DAÑO MORAL")
    'dano moral'
    """
    if not texto:
        return ""

    # NFD separa la letra base de la marca diacritica; despues se descartan
    # las marcas (categoria Mn) y queda la letra pelada.
    descompuesto = unicodedata.normalize("NFD", str(texto).lower())
    sin_acentos = "".join(
        c for c in descompuesto if unicodedata.category(c) != "Mn"
    )

    return _ESPACIOS.sub(" ", _NO_ALFANUM.sub(" ", sin_acentos)).strip()


def tokenizar(texto: str, minimo: int = 3, quitar_stop: bool = True) -> list:
    """
    Tokens normalizados, sin stop-words y sin tokens demasiado cortos.

    Los numeros se conservan aunque sean cortos: en jurisprudencia "art 14 bis"
    o un numero de expediente son señal, no ruido.

    >>> tokenizar("Responsabilidad del Estado por caída de un árbol")
    ['responsabilidad', 'estado', 'caida', 'arbol']
    """
    tokens = []
    for token in normalizar_texto(texto).split():
        if quitar_stop and token in STOP_WORDS:
            continue
        if len(token) < minimo and not token.isdigit():
            continue
        tokens.append(token)
    return tokens


def normalizar_expediente(texto: str) -> str:
    """
    Clave comparable para un numero de expediente.

    Los numeros vienen escritos de formas distintas segun el organismo
    ("Nro. 12.345", "12345/2019", "EXP-12345"), asi que se deja solo lo
    alfanumerico en minusculas.

    >>> normalizar_expediente("Nro. 12.345/2019")
    'nro123452019'
    """
    return re.sub(r"[^0-9a-z]", "", normalizar_texto(texto))


def colapsar(texto: str) -> str:
    """Limpia espacios y saltos de linea sin tocar acentos ni mayusculas.

    Para el texto que se le muestra al usuario, donde la normalizacion
    agresiva estorbaria.
    """
    if not texto:
        return ""
    return _ESPACIOS.sub(" ", str(texto).replace("\xa0", " ")).strip()
