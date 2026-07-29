"""
Parseo del HTML del buscador STJER (SIU-Toba)
=============================================

Este es el modulo que decide si todo lo demas funciona, y el unico que se
puede desarrollar y testear **sin red**, contra las fixtures HTML que se
guardan en la Fase 0.

Estrategia frente a un sitio que no podemos ver desde aca
--------------------------------------------------------
En vez de clavar selectores adivinados, el parseo es **por etiquetas y por
forma**: se buscan encabezados de tabla por su texto ("Carátula", "Fuero",
"Nº Expediente"...) y pares etiqueta/valor en la vista de detalle. Eso
sobrevive a que las clases CSS y los ids de Toba no sean los que uno supuso.

Ademas, todo lo que puede necesitar ajuste vive en `PERFIL`, que se puede
sobrescribir desde `data/jurisprudencia/descubrimiento/perfil.json` sin tocar
codigo. Cuando la Fase 0 diga como es el HTML real, se ajusta ese JSON y
listo.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from .normalizacion import colapsar, normalizar_texto

logger = logging.getLogger(__name__)


def _sopa(html: str) -> BeautifulSoup:
    """BeautifulSoup con lxml si esta, y si no con el parser de la stdlib."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _texto_suave(nodo) -> str:
    """
    Minusculas y sin acentos, pero CONSERVANDO puntuacion y digitos juntos.

    normalizar_texto() parte "14.822" en "14 822", asi que no sirve para leer
    los contadores del pie ("Se encontraron 14.822 registros"). Aca hace falta
    la version suave.
    """
    import unicodedata

    texto = nodo if isinstance(nodo, str) else nodo.get_text(" ")
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_acentos = "".join(
        c for c in descompuesto if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_acentos)


# ═══════════════════════════════════════════════════════════════════════════
#  Perfil ajustable del sitio
# ═══════════════════════════════════════════════════════════════════════════

# Sinonimos de encabezado -> campo canonico. Se comparan normalizados, asi que
# "Nº Expediente", "NRO. EXPEDIENTE" y "nro expediente" caen todos en el mismo
# lugar. Agregar variantes aca es la forma barata de adaptar el parser.
ENCABEZADOS = {
    "jurisdiccion": "jurisdiccion",
    "organismo": "organismo",
    "fallo": "fecha_fallo",
    "fecha": "fecha_fallo",
    "fecha del fallo": "fecha_fallo",
    "fecha sentencia": "fecha_fallo",
    "fecha de sentencia": "fecha_fallo",
    "n expediente": "nro_expediente",
    "nro expediente": "nro_expediente",
    "numero de expediente": "nro_expediente",
    "expediente": "nro_expediente",
    "caratula": "caratula",
    "sumario": "sumario",
    "fuero": "fuero",
    "voces": "voces",
    "votos": "votos",
    "materia": "materia",
    "voz principal": "voz_principal",
    "voz": "voz",
    "juez": "juez",
    "jueza": "juez",
    # normalizar_texto("Juez/a") -> "juez a": la barra pasa a ser separador.
    "juez a": "juez",
    "juez vocal": "juez",
    "vocal": "juez",
    "magistrado": "juez",
    "tipo de voto": "tipo_voto",
    "tipo voto": "tipo_voto",
    "voto": "tipo_voto",
}

TIPOS_VOTO = (
    "primer voto", "segundo voto", "tercer voto",
    "adhesion", "abstencion", "disidencia", "disidencia parcial",
)


@dataclass
class PerfilSitio:
    """Todo lo que puede cambiar cuando se vea el HTML real."""

    encabezados: dict = field(default_factory=lambda: dict(ENCABEZADOS))
    # Marcadores que indican que la respuesta es la pared de captcha/login.
    marcadores_captcha: tuple = (
        "verificacion", "captcha", "codigo de seguridad", "ingrese el codigo",
    )
    # De donde sacar el link al PDF.
    patron_pdf: str = r"(dossier/[^\"'\s>]+\.(?:pdf|PDF))"
    # "Se encontraron 123 registros"
    patron_total: str = r"se\s+encontraron\s+([\d.]+)\s+registro"
    # "Página 2 de 7"
    patron_paginacion: str = r"p[aá]gina\s+(\d+)\s+de\s+(\d+)"
    # Hash de sesion de Toba en la URL: ah=st672374e1c84563.72627224
    patron_ah: str = r"[?&;]ah=([A-Za-z0-9._-]+)"

    @classmethod
    def cargar(cls, ruta=None) -> "PerfilSitio":
        """
        Carga overrides desde descubrimiento/perfil.json si existe.

        Asi la Fase 0 se puede volcar en datos en vez de en un parche.
        """
        perfil = cls()
        if ruta is None:
            from . import ajustes

            ruta = ajustes.DESCUBRIMIENTO_DIR / "perfil.json"
        ruta = Path(ruta)
        if not ruta.exists():
            return perfil

        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("No se pudo leer el perfil %s: %s", ruta, e)
            return perfil

        if isinstance(datos.get("encabezados"), dict):
            # Se suman a los que ya hay, no los reemplazan.
            perfil.encabezados.update(
                {normalizar_texto(k): v for k, v in datos["encabezados"].items()}
            )
        for campo in (
            "patron_pdf", "patron_total", "patron_paginacion", "patron_ah",
        ):
            if isinstance(datos.get(campo), str):
                setattr(perfil, campo, datos[campo])
        if isinstance(datos.get("marcadores_captcha"), list):
            perfil.marcadores_captcha = tuple(datos["marcadores_captcha"])
        logger.info("Perfil de parseo cargado desde %s", ruta)
        return perfil


PERFIL = PerfilSitio()


# ═══════════════════════════════════════════════════════════════════════════
#  Desenvolver la respuesta de Toba
# ═══════════════════════════════════════════════════════════════════════════

# Toba responde JavaScript que reinyecta HTML, tipico:
#   toba.actualizar_celda('ei_1234', '<table>...</table>');
_LITERAL_JS = re.compile(
    r"""(['"])((?:\\.|(?!\1).){80,})\1""", re.DOTALL
)

_ESCAPES = {
    r"\/": "/", r"\'": "'", r"\"": '"', r"\n": "\n",
    r"\r": "\r", r"\t": "\t", r"\\": "\\",
}


def _desescapar_js(texto: str) -> str:
    """Deshace el escapeo de un literal de cadena de JavaScript."""
    texto = re.sub(
        r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), texto
    )
    texto = re.sub(
        r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), texto
    )
    for k, v in _ESCAPES.items():
        texto = texto.replace(k, v)
    return texto


def desenvolver_toba(cuerpo: str) -> str:
    """
    Devuelve el HTML util de una respuesta de Toba.

    Maneja los tres casos que se pueden dar:
      1. HTML directo -> se devuelve tal cual.
      2. JSON con HTML adentro -> se concatenan los strings con marcas de HTML.
      3. JavaScript con el HTML en literales de cadena -> se extraen y
         des-escapan (es el caso tipico de Toba y el que hay que presupuestar).

    Si no encuentra nada mejor, devuelve el cuerpo original: es preferible que
    el parser de arriba falle con algo que perder la respuesta entera.
    """
    if not cuerpo:
        return ""

    muestra = cuerpo.lstrip()[:400].lower()
    if muestra.startswith(("<!doctype", "<html", "<table", "<div", "<form")):
        return cuerpo

    # JSON
    if muestra.startswith(("{", "[")):
        try:
            datos = json.loads(cuerpo)
        except json.JSONDecodeError:
            pass
        else:
            trozos = []

            def recorrer(nodo):
                if isinstance(nodo, str):
                    if "<" in nodo and ">" in nodo:
                        trozos.append(nodo)
                elif isinstance(nodo, dict):
                    for v in nodo.values():
                        recorrer(v)
                elif isinstance(nodo, list):
                    for v in nodo:
                        recorrer(v)

            recorrer(datos)
            if trozos:
                return "\n".join(trozos)

    # JavaScript con HTML en literales
    trozos = [
        _desescapar_js(m.group(2))
        for m in _LITERAL_JS.finditer(cuerpo)
        if "<" in m.group(2) and ">" in m.group(2)
    ]
    if trozos:
        return "\n".join(trozos)

    return cuerpo


def extraer_token_ah(cuerpo: str):
    """
    Saca el hash de sesion `ah` de Toba.

    Es lo que hace viable la rama B: si el token rota pero aparece literal en
    la respuesta anterior, se lo arrastra de request en request sin necesidad
    de un navegador.
    """
    if not cuerpo:
        return None
    m = re.search(PERFIL.patron_ah, cuerpo)
    return m.group(1) if m else None


def hay_captcha(cuerpo: str) -> bool:
    """
    True si la respuesta es la pared de verificacion en vez de datos.

    La cosecha la usa para devolver la tarea a la cola sin contarla como
    fallo: no fallo la tarea, se corto la sesion.
    """
    if not cuerpo:
        return False
    texto = _texto_suave(_sopa(cuerpo))
    if not texto:
        return False
    # "verificacion" sola aparece en textos legitimos; se pide que ademas no
    # haya llegado una tabla de resultados.
    tiene_marcador = any(m in texto for m in PERFIL.marcadores_captcha)
    tiene_resultados = bool(re.search(PERFIL.patron_total, texto))
    return tiene_marcador and not tiene_resultados


# ═══════════════════════════════════════════════════════════════════════════
#  Listado
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ListadoParseado:
    filas: list = field(default_factory=list)
    total_registros: int = None
    pagina: int = None
    total_paginas: int = None
    hay_siguiente: bool = False

    def __len__(self):
        return len(self.filas)


def _mapear_encabezados(tabla) -> dict:
    """
    Indice de columna -> campo canonico, leyendo la fila de encabezado.

    Devuelve {} si la tabla no parece de resultados.
    """
    fila_enc = tabla.find("tr")
    if fila_enc is None:
        return {}

    celdas = fila_enc.find_all(["th", "td"])
    mapa = {}
    for i, celda in enumerate(celdas):
        clave = normalizar_texto(celda.get_text(" "))
        if clave in PERFIL.encabezados:
            mapa[i] = PERFIL.encabezados[clave]
    return mapa


def _ref_detalle(fila) -> str:
    """
    Como volver a abrir este fallo.

    Toba casi siempre lleva un id en el onclick de la fila o en un input
    oculto. Se guarda crudo lo primero que aparezca; el cliente sabe que
    hacer con eso.
    """
    for attr in ("onclick", "ondblclick", "href", "data-id", "id"):
        valor = fila.get(attr)
        if valor:
            return colapsar(valor)
    for celda in fila.find_all(["td", "th"]):
        for hijo in celda.find_all(["a", "input", "span"]):
            for attr in ("onclick", "href", "value", "data-id", "id"):
                valor = hijo.get(attr)
                if valor and str(valor).strip():
                    return colapsar(str(valor))
    return ""


def _id_sitio(ref: str) -> str:
    """
    Id numerico propio del sitio, si se puede sacar de la referencia.

    Si aparece, es una clave natural mucho mas estable que un hash de
    expediente + caratula + fecha.
    """
    if not ref:
        return ""
    candidatos = re.findall(r"\b(\d{3,})\b", ref)
    return candidatos[-1] if candidatos else ""


def _normalizar_fecha(texto: str) -> str:
    """
    Fecha a ISO 'YYYY-MM-DD'.

    El sitio muestra dd/mm/aaaa; se acepta tambien ISO por si algun campo ya
    viene asi. Si no se entiende, se devuelve "" antes que inventar.
    """
    texto = colapsar(texto)
    if not texto:
        return ""
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", texto)
    if m:
        d, mes, a = m.groups()
        a = int(a)
        if a < 100:  # '19' -> 2019
            a += 2000 if a < 70 else 1900
        try:
            from datetime import date

            return date(a, int(mes), int(d)).isoformat()
        except ValueError:
            return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", texto)
    return m.group(0) if m else ""


def parsear_listado(cuerpo: str) -> ListadoParseado:
    """
    Parsea una pagina de resultados.

    Cada fila trae ya lo suficiente para ser buscable: jurisdiccion,
    organismo, fecha, expediente, caratula, fuero y el extracto del sumario.
    Por eso la pasada de listados sola ya deja un corpus util.
    """
    html = desenvolver_toba(cuerpo)
    sopa = _sopa(html)
    resultado = ListadoParseado()

    # Suave, no normalizado: hay que conservar el punto de "14.822".
    texto_plano = _texto_suave(sopa)

    m = re.search(PERFIL.patron_total, texto_plano)
    if m:
        resultado.total_registros = int(m.group(1).replace(".", "").replace(",", ""))

    m = re.search(PERFIL.patron_paginacion, texto_plano)
    if m:
        resultado.pagina = int(m.group(1))
        resultado.total_paginas = int(m.group(2))
        resultado.hay_siguiente = resultado.pagina < resultado.total_paginas

    # La tabla de resultados es la que mas encabezados reconocidos tenga.
    mejor_tabla, mejor_mapa = None, {}
    for tabla in sopa.find_all("table"):
        mapa = _mapear_encabezados(tabla)
        if len(mapa) > len(mejor_mapa):
            mejor_tabla, mejor_mapa = tabla, mapa

    if mejor_tabla is None or len(mejor_mapa) < 2:
        logger.debug("No se reconocio una tabla de resultados")
        return resultado

    filas = mejor_tabla.find_all("tr")[1:]  # se saltea el encabezado
    for fila in filas:
        celdas = fila.find_all(["td", "th"])
        if not celdas:
            continue

        datos = {}
        for i, campo in mejor_mapa.items():
            if i < len(celdas):
                datos[campo] = colapsar(celdas[i].get_text(" "))

        # Una fila sin caratula ni expediente es separador o pie de tabla.
        if not datos.get("caratula") and not datos.get("nro_expediente"):
            continue

        datos["fecha_fallo"] = _normalizar_fecha(datos.get("fecha_fallo", ""))
        datos["ref_detalle"] = _ref_detalle(fila)
        datos["id_sitio"] = _id_sitio(datos["ref_detalle"])

        m_pdf = re.search(PERFIL.patron_pdf, str(fila))
        if m_pdf:
            datos["enlace_pdf"] = m_pdf.group(1)

        # El extracto de la columna Sumario se guarda como sumario truncado.
        extracto = datos.pop("sumario", "")
        datos["sumario_extracto"] = extracto

        resultado.filas.append(datos)

    if resultado.total_registros is None and resultado.filas:
        resultado.total_registros = len(resultado.filas)

    return resultado


# ═══════════════════════════════════════════════════════════════════════════
#  Detalle
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DetalleParseado:
    caratula: str = ""
    nro_expediente: str = ""
    fecha_fallo: str = ""
    jurisdiccion: str = ""
    organismo: str = ""
    fuero: str = ""
    enlace_pdf: str = ""
    sumarios: list = field(default_factory=list)  # [{texto, voces:[...]}]
    voces: list = field(default_factory=list)     # [{materia, voz_principal, voz}]
    votos: list = field(default_factory=list)     # [{juez, tipo_voto}]

    def como_fallo(self) -> dict:
        """Dict listo para corpus.upsert_fallo()."""
        return {
            "caratula": self.caratula,
            "nro_expediente": self.nro_expediente,
            "fecha_fallo": self.fecha_fallo,
            "jurisdiccion": self.jurisdiccion,
            "organismo": self.organismo,
            "fuero": self.fuero,
            "enlace_pdf": self.enlace_pdf,
        }


def _pares_etiqueta_valor(sopa) -> dict:
    """
    Pares ETIQUETA: valor de la vista de detalle.

    Cubre las dos formas habituales: celdas contiguas en una tabla, y texto
    corrido con "ETIQUETA: valor". Devuelve {campo_canonico: valor}.
    """
    encontrados = {}

    # Forma 1: <td>ETIQUETA</td><td>valor</td>
    for fila in sopa.find_all("tr"):
        celdas = fila.find_all(["td", "th"])
        for i in range(len(celdas) - 1):
            clave = normalizar_texto(celdas[i].get_text(" ")).rstrip(": ")
            campo = PERFIL.encabezados.get(clave)
            if campo and campo not in encontrados:
                valor = colapsar(celdas[i + 1].get_text(" "))
                if valor:
                    encontrados[campo] = valor

    # Forma 2: "CARATULA: Perez c/ Municipalidad"
    texto = sopa.get_text("\n")
    for linea in texto.split("\n"):
        if ":" not in linea:
            continue
        izq, der = linea.split(":", 1)
        campo = PERFIL.encabezados.get(normalizar_texto(izq))
        der = colapsar(der)
        if campo and der and campo not in encontrados:
            encontrados[campo] = der

    return encontrados


def _parsear_voces(sopa) -> list:
    """
    Voces del fallo, con su jerarquia Materia > Voz Principal > Voz.

    Se busca una tabla cuyos encabezados sean justamente esos tres.
    """
    voces = []
    for tabla in sopa.find_all("table"):
        mapa = _mapear_encabezados(tabla)
        campos = set(mapa.values())
        if not ({"voz", "voz_principal", "materia"} & campos):
            continue
        if "juez" in campos:  # es la tabla de votos, no la de voces
            continue

        for fila in tabla.find_all("tr")[1:]:
            celdas = fila.find_all(["td", "th"])
            if not celdas:
                continue
            v = {"materia": "", "voz_principal": "", "voz": ""}
            for i, campo in mapa.items():
                if campo in v and i < len(celdas):
                    v[campo] = colapsar(celdas[i].get_text(" "))
            if v["voz"] or v["voz_principal"]:
                if not v["voz"]:  # sin hoja, la voz principal es la hoja
                    v["voz"], v["voz_principal"] = v["voz_principal"], ""
                voces.append(v)
    return voces


def _parsear_votos(sopa) -> list:
    """Votos del fallo: juez/a y tipo de voto."""
    votos = []
    for tabla in sopa.find_all("table"):
        mapa = _mapear_encabezados(tabla)
        if "juez" not in mapa.values():
            continue
        for fila in tabla.find_all("tr")[1:]:
            celdas = fila.find_all(["td", "th"])
            if not celdas:
                continue
            voto = {"juez": "", "tipo_voto": ""}
            for i, campo in mapa.items():
                if campo in voto and i < len(celdas):
                    voto[campo] = colapsar(celdas[i].get_text(" "))
            if voto["juez"]:
                votos.append(voto)

    if votos:
        return votos

    # Fallback: texto corrido "Dra. Gonzalez - primer voto".
    # El nombre tiene que ser algo DISTINTO del tipo de voto, si no se
    # terminan guardando filas como {juez: 'primer voto'}.
    for linea in sopa.get_text("\n").split("\n"):
        norm = normalizar_texto(linea)
        tipo = next((t for t in TIPOS_VOTO if t in norm), None)
        if not tipo:
            continue
        juez = colapsar(re.split(r"[-–—:]", linea)[0])
        if not juez or len(juez) >= 120:
            continue
        if normalizar_texto(juez) in TIPOS_VOTO:
            continue
        votos.append({"juez": juez, "tipo_voto": tipo})
    return votos


def _parsear_sumarios(sopa) -> list:
    """
    Bloques de sumario de la vista de detalle.

    Se prefieren los delimitados por el propio sitio. No se intenta tallar el
    sumario del texto del PDF con regex: los sumarios que publica el STJER ya
    vienen curados y separados, y son mejores que cualquier heuristica.
    """
    textos = []

    # Nodos marcados explicitamente como sumario.
    for nodo in sopa.find_all(
        attrs={"class": re.compile(r"sumario", re.I)}
    ) + sopa.find_all(attrs={"id": re.compile(r"sumario", re.I)}):
        t = colapsar(nodo.get_text(" "))
        if len(t) > 40:
            textos.append(t)

    if not textos:
        # Celda a la derecha de una etiqueta SUMARIO.
        for fila in sopa.find_all("tr"):
            celdas = fila.find_all(["td", "th"])
            for i in range(len(celdas) - 1):
                if normalizar_texto(celdas[i].get_text(" ")).rstrip(": ") == "sumario":
                    t = colapsar(celdas[i + 1].get_text(" "))
                    if len(t) > 40:
                        textos.append(t)

    # Se deduplica conservando el orden.
    vistos, unicos = set(), []
    for t in textos:
        clave = normalizar_texto(t)[:200]
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(t)
    return unicos


def parsear_detalle(cuerpo: str) -> DetalleParseado:
    """Parsea la vista de detalle de un fallo."""
    html = desenvolver_toba(cuerpo)
    sopa = _sopa(html)
    d = DetalleParseado()

    pares = _pares_etiqueta_valor(sopa)
    d.caratula = pares.get("caratula", "")
    d.nro_expediente = pares.get("nro_expediente", "")
    d.fecha_fallo = _normalizar_fecha(pares.get("fecha_fallo", ""))
    d.jurisdiccion = pares.get("jurisdiccion", "")
    d.organismo = pares.get("organismo", "")
    d.fuero = pares.get("fuero", "")

    m = re.search(PERFIL.patron_pdf, html)
    if m:
        d.enlace_pdf = m.group(1)

    d.voces = _parsear_voces(sopa)
    d.votos = _parsear_votos(sopa)

    textos = _parsear_sumarios(sopa)
    if not textos and pares.get("sumario"):
        textos = [pares["sumario"]]

    # Las voces se cuelgan del primer sumario: el sitio las publica por fallo,
    # no por sumario, y colgarlas de todos inflaria el indice con repetidos.
    for i, texto in enumerate(textos):
        d.sumarios.append({"texto": texto, "voces": d.voces if i == 0 else []})

    return d


# ═══════════════════════════════════════════════════════════════════════════
#  Tesauro
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class NodoVoz:
    materia: str = ""
    voz_principal: str = ""
    voz: str = ""
    ref: str = ""       # para pedir la expansion del nivel siguiente
    nivel: int = 0      # 0 = materia, 1 = voz principal, 2 = voz

    @property
    def ruta(self) -> str:
        return " > ".join(
            p for p in (self.materia, self.voz_principal, self.voz) if p
        )


def parsear_arbol_tesauro(cuerpo: str, materia: str = "", voz_principal: str = "") -> list:
    """
    Nodos del arbol del tesauro presentes en la respuesta.

    Sirve para los dos casos que puede devolver la Fase 0: que el arbol entero
    venga en una sola respuesta, o que cada nivel se pida por AJAX. En el
    segundo caso se pasa el contexto (`materia`, `voz_principal`) para que los
    hijos queden colgados donde corresponde.
    """
    html = desenvolver_toba(cuerpo)
    sopa = _sopa(html)
    nodos = []
    vistos = set()

    nivel_base = 2 if voz_principal else (1 if materia else 0)

    # Forma 1: arbol anidado con <ul>/<li>.
    # No alcanza con la profundidad: hay que leer las etiquetas de los <li>
    # ancestros para saber DE QUIEN cuelga cada nodo, si no todas las voces
    # quedan colgando de la raiz y se pierde la jerarquia.
    for li in sopa.find_all("li"):
        etiqueta = _etiqueta_li(li)
        if not etiqueta or len(etiqueta) > 200:
            continue

        # find_parents devuelve del mas cercano al mas lejano; se invierte
        # para tener la cadena raiz -> hoja.
        cadena = [
            e for e in (
                _etiqueta_li(p) for p in reversed(li.find_parents("li"))
            ) if e
        ]
        # El contexto que venga por parametro va adelante de todo (caso
        # "cada nivel se pide por AJAX").
        contexto = [c for c in (materia, voz_principal) if c]
        completa = (contexto + cadena + [etiqueta])[:3]

        nodo = NodoVoz(
            materia=completa[0] if len(completa) > 0 else "",
            voz_principal=completa[1] if len(completa) > 1 else "",
            voz=completa[2] if len(completa) > 2 else "",
            ref=_ref_de(li),
            nivel=min(len(completa) - 1, 2),
        )
        if nodo.ruta and nodo.ruta not in vistos:
            vistos.add(nodo.ruta)
            nodos.append(nodo)

    # Forma 2: <option> de un desplegable
    if not nodos:
        for opt in sopa.find_all("option"):
            etiqueta = colapsar(opt.get_text(" "))
            valor = colapsar(opt.get("value") or "")
            if not etiqueta or valor in ("", "0", "-1"):
                continue
            nodo = _nodo_en_nivel(etiqueta, nivel_base, materia, voz_principal, opt)
            if nodo and nodo.ruta not in vistos:
                vistos.add(nodo.ruta)
                nodos.append(nodo)

    # Forma 3: filas de tabla con Materia / Voz Principal / Voz
    if not nodos:
        for v in _parsear_voces(sopa):
            nodo = NodoVoz(
                materia=v["materia"], voz_principal=v["voz_principal"],
                voz=v["voz"], nivel=2,
            )
            if nodo.ruta and nodo.ruta not in vistos:
                vistos.add(nodo.ruta)
                nodos.append(nodo)

    return nodos


def _etiqueta_li(li) -> str:
    """
    Texto propio de un <li>, sin arrastrar el de sus hijos.

    En un arbol anidado, get_text() de un <li> padre devuelve tambien todas
    las voces de abajo, asi que hay que quedarse solo con los nodos de texto
    directos.
    """
    directo = "".join(
        str(t) for t in li.find_all(string=True, recursive=False)
    )
    etiqueta = colapsar(directo)
    if etiqueta:
        return etiqueta
    # Si el texto esta envuelto (<li><a>ETIQUETA</a><ul>...</ul></li>), se
    # toma el primer descendiente que no sea la lista de hijos.
    for hijo in li.find_all(["a", "span", "label", "b"], recursive=False):
        etiqueta = colapsar(hijo.get_text(" "))
        if etiqueta:
            return etiqueta
    return ""


def _ref_de(elemento) -> str:
    """Primer atributo utilizable para volver a pedir este nodo."""
    for attr in ("value", "data-id", "id", "onclick", "href"):
        v = elemento.get(attr) if hasattr(elemento, "get") else None
        if v:
            return colapsar(str(v))
    return ""


def _nodo_en_nivel(etiqueta, nivel, materia, voz_principal, elemento):
    """Arma un NodoVoz ubicando la etiqueta en el nivel que le toca."""
    ref = _ref_de(elemento)

    if nivel <= 0:
        return NodoVoz(materia=etiqueta, ref=ref, nivel=0)
    if nivel == 1:
        return NodoVoz(
            materia=materia or "", voz_principal=etiqueta, ref=ref, nivel=1
        )
    return NodoVoz(
        materia=materia or "", voz_principal=voz_principal or "",
        voz=etiqueta, ref=ref, nivel=2,
    )
