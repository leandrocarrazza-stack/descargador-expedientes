"""
Buscador local sobre el corpus STJER
====================================

Esto es lo que reemplaza a "Claude maneja el navegador en cada busqueda":
una consulta FTS5 sobre SQLite que responde en milisegundos y cuesta,
en tokens, lo que ocupen los resultados.

La unidad de resultado es el **sumario**, no el fallo: es lo que se cita.

Sobre el mapeo consulta -> voces
--------------------------------
`sugerir_voces` usa dos señales:

  1. **Co-ocurrencia sobre el corpus** (la buena): se buscan los sumarios que
     matchean la consulta y se rankean sus voces por log-odds contra la
     frecuencia base de cada voz en el corpus. Como cada sumario trae su texto
     Y sus voces asignadas, el propio corpus enseña que voces usan los jueces
     cuando hablan de un tema. Descubre relaciones que el matcheo de etiquetas
     no puede: "delito tentado" -> TENTATIVA.

  2. **Parecido de etiqueta** (la de arranque en frio): difflib contra los
     nombres de las voces. Es lo unico disponible antes de que exista corpus.
"""

import json
import logging
import math
import re

from .normalizacion import normalizar_texto, tokenizar

logger = logging.getLogger(__name__)

# Pesos de bm25: texto del sumario > voces > caratula > organismo.
PESOS_BM25 = (10.0, 5.0, 3.0, 1.0)

# A partir de esta longitud se agrega '*' al termino. FTS5 no trae stemmer de
# español, asi que el prefijo es lo que hace que "prescripcion" tambien
# encuentre "prescripciones" y "prescriptivo".
LARGO_PARA_PREFIJO = 5


class ErrorBusqueda(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════
#  Construccion de la consulta FTS5
# ═══════════════════════════════════════════════════════════════════════════

def construir_match(consulta: str, prefijos: bool = True) -> str:
    """
    Pasa lenguaje natural a sintaxis de FTS5, sin romperse con la puntuacion.

    Cada termino se entrecomilla (asi ningun caracter se interpreta como
    operador de FTS5) y se le agrega '*' si es largo.

    Se respetan las frases entre comillas de la consulta original: escribir
    "daño moral" con comillas busca la frase exacta.

    >>> construir_match('prescripcion liberatoria')
    '"prescripcion"* AND "liberatoria"*'
    >>> construir_match('"daño moral"')
    '"dano moral"'
    """
    if not consulta or not consulta.strip():
        raise ErrorBusqueda("La consulta esta vacia")

    partes = []

    # Frases exactas entre comillas
    for frase in re.findall(r'"([^"]+)"', consulta):
        frase_norm = normalizar_texto(frase)
        if frase_norm:
            partes.append(f'"{frase_norm}"')
    resto = re.sub(r'"[^"]+"', " ", consulta)

    for token in tokenizar(resto, minimo=2):
        if prefijos and len(token) >= LARGO_PARA_PREFIJO:
            partes.append(f'"{token}"*')
        else:
            partes.append(f'"{token}"')

    if not partes:
        raise ErrorBusqueda(
            f"La consulta {consulta!r} quedo vacia despues de sacar las "
            f"palabras vacias. Probá con terminos mas especificos."
        )
    return " AND ".join(partes)


def _relajar(match: str) -> str:
    """Pasa el AND a OR, para reintentar cuando el AND no trajo nada."""
    return match.replace(" AND ", " OR ")


# ═══════════════════════════════════════════════════════════════════════════
#  Buscador
# ═══════════════════════════════════════════════════════════════════════════

class BuscadorCorpus:
    """Busqueda sobre el corpus local. Todo offline."""

    def __init__(self, con, tesauro=None):
        self.con = con
        self._tesauro = tesauro

    @property
    def tesauro(self):
        """Carga perezosa: no hace falta para una busqueda de texto."""
        if self._tesauro is None:
            from .tesauro_stjer import Tesauro

            self._tesauro = Tesauro.cargar()
        return self._tesauro

    # ── busqueda ──────────────────────────────────────────────────────────

    def buscar(
        self,
        consulta: str,
        *,
        voces=None,
        fuero=None,
        organismo=None,
        juez=None,
        desde=None,
        hasta=None,
        limite: int = 10,
        contexto: int = 32,
        relajar: bool = True,
    ) -> list:
        """
        Busca sumarios y devuelve los mejores.

        Los filtros van como WHERE sobre `fallos`, no como texto: es
        justamente lo que fallaba al intentar cargarlos en el formulario web.

        `relajar=True` reintenta con OR si el AND no trajo nada, antes que
        devolver una lista vacia.
        """
        match = construir_match(consulta)
        filas = self._ejecutar(
            match, voces, fuero, organismo, juez, desde, hasta, limite, contexto
        )

        if not filas and relajar and " AND " in match:
            logger.info("Sin resultados con AND; se reintenta con OR")
            filas = self._ejecutar(
                _relajar(match), voces, fuero, organismo, juez,
                desde, hasta, limite, contexto,
            )

        return filas

    def _ejecutar(
        self, match, voces, fuero, organismo, juez, desde, hasta, limite, contexto
    ) -> list:
        condiciones, params = ["documentos_fts MATCH :match"], {"match": match}

        if fuero:
            # Coincidencia laxa: el usuario escribe "civil", el sitio guarda
            # "Fuero Civil y Comercial".
            condiciones.append("f.fuero LIKE :fuero")
            params["fuero"] = f"%{fuero}%"
        if organismo:
            condiciones.append("f.organismo LIKE :organismo")
            params["organismo"] = f"%{organismo}%"
        if desde:
            condiciones.append("f.fecha_fallo >= :desde")
            params["desde"] = _a_fecha(desde, inicio=True)
        if hasta:
            condiciones.append("f.fecha_fallo <= :hasta")
            params["hasta"] = _a_fecha(hasta, inicio=False)
        if juez:
            condiciones.append(
                "EXISTS (SELECT 1 FROM votos vo WHERE vo.fallo_id=f.id "
                "AND vo.juez_norm LIKE :juez)"
            )
            params["juez"] = f"%{normalizar_texto(juez)}%"
        if voces:
            marcadores = []
            for i, voz in enumerate(voces):
                clave = f"voz{i}"
                marcadores.append(f"v.ruta_norm LIKE :{clave}")
                params[clave] = f"%{normalizar_texto(voz)}%"
            condiciones.append(
                "EXISTS (SELECT 1 FROM sumario_voces sv JOIN voces v "
                f"ON v.id=sv.voz_id WHERE sv.sumario_id=s.id AND ({' OR '.join(marcadores)}))"
            )

        params["limite"] = limite
        params["contexto"] = contexto

        sql = f"""
            SELECT s.id            AS sumario_id,
                   f.id            AS fallo_id,
                   f.clave_natural, f.caratula, f.nro_expediente, f.fecha_fallo,
                   f.organismo, f.jurisdiccion, f.fuero, f.enlace_pdf,
                   f.pdf_ruta, f.detalle_ok,
                   s.texto, s.orden, s.truncado,
                   bm25(documentos_fts, {', '.join(str(p) for p in PESOS_BM25)}) AS puntaje,
                   snippet(documentos_fts, 0, '«', '»', '…', :contexto) AS fragmento
              FROM documentos_fts
              JOIN documentos d ON d.id = documentos_fts.rowid
              JOIN sumarios   s ON s.id = d.id
              JOIN fallos     f ON f.id = d.fallo_id
             WHERE {' AND '.join(condiciones)}
             ORDER BY puntaje
             LIMIT :limite
        """
        try:
            filas = self.con.execute(sql, params).fetchall()
        except Exception as e:
            raise ErrorBusqueda(f"Fallo la consulta: {e}") from e

        return [self._como_resultado(f) for f in filas]

    def _como_resultado(self, fila) -> dict:
        voces = [
            r["ruta"]
            for r in self.con.execute(
                "SELECT v.ruta FROM sumario_voces sv JOIN voces v ON v.id=sv.voz_id "
                "WHERE sv.sumario_id=? ORDER BY v.ruta",
                (fila["sumario_id"],),
            )
        ]
        return {
            "fallo_id": fila["fallo_id"],
            "clave": fila["clave_natural"],
            "caratula": fila["caratula"],
            "expediente": fila["nro_expediente"],
            "fecha": fila["fecha_fallo"],
            "organismo": fila["organismo"],
            "jurisdiccion": fila["jurisdiccion"],
            "fuero": fila["fuero"],
            # bm25 devuelve negativos y mas bajo es mejor; se invierte para
            # que en la salida un numero mas alto sea mejor, que es lo que
            # cualquiera espera al leerlo.
            "puntaje": round(-fila["puntaje"], 3),
            "fragmento": fila["fragmento"],
            "sumario": fila["texto"],
            "sumario_truncado": bool(fila["truncado"]),
            "voces": voces,
            "url_pdf": _url_pdf(fila["enlace_pdf"]),
            "pdf_local": fila["pdf_ruta"],
            "detalle_cosechado": bool(fila["detalle_ok"]),
        }

    # ── voces ─────────────────────────────────────────────────────────────

    def sugerir_voces(self, consulta: str, n: int = 8, muestra: int = 120) -> list:
        """
        Voces del tesauro que mejor describen la consulta.

        Combina co-ocurrencia sobre el corpus con parecido de etiqueta.
        Devuelve [{voz, puntaje, origen, frecuencia}].
        """
        por_corpus = self._voces_por_cooocurrencia(consulta, muestra=muestra)
        por_etiqueta = dict(self.tesauro.buscar_por_etiqueta(consulta, n=n * 2))

        combinado = {}
        for voz, puntaje in por_corpus.items():
            combinado[voz] = {"voz": voz, "puntaje": puntaje, "origen": "corpus"}
        for voz, puntaje in por_etiqueta.items():
            if voz in combinado:
                # Las dos señales de acuerdo: sube, con tope en 1.
                combinado[voz]["puntaje"] = min(
                    1.0, combinado[voz]["puntaje"] + puntaje * 0.5
                )
                combinado[voz]["origen"] = "corpus+etiqueta"
            else:
                combinado[voz] = {
                    "voz": voz, "puntaje": puntaje * 0.6, "origen": "etiqueta"
                }

        for item in combinado.values():
            item["puntaje"] = round(item["puntaje"], 4)
            item["ruta"] = self.tesauro.ruta_de(item["voz"])

        salida = sorted(
            combinado.values(), key=lambda i: (-i["puntaje"], i["voz"])
        )
        return salida[:n]

    def _voces_por_cooocurrencia(self, consulta: str, muestra: int = 120) -> dict:
        """
        Rankea voces por cuanto se concentran en los sumarios que matchean.

        El puntaje es log-odds: una voz que aparece en el 40% de los sumarios
        que matchean pero solo en el 2% del corpus es muy informativa; una que
        aparece en el 40% de ambos no dice nada.
        """
        try:
            match = construir_match(consulta)
        except ErrorBusqueda:
            return {}

        total_sumarios = self.con.execute(
            "SELECT COUNT(*) FROM documentos"
        ).fetchone()[0]
        if not total_sumarios:
            return {}

        try:
            filas = self.con.execute(
                """
                SELECT v.voz AS voz, COUNT(*) AS n, v.frecuencia AS base
                  FROM documentos_fts
                  JOIN sumario_voces sv ON sv.sumario_id = documentos_fts.rowid
                  JOIN voces v ON v.id = sv.voz_id
                 WHERE documentos_fts MATCH :match
                 GROUP BY v.id
                 ORDER BY n DESC
                 LIMIT :muestra
                """,
                {"match": match, "muestra": muestra},
            ).fetchall()
        except Exception as e:
            logger.debug("Co-ocurrencia no disponible: %s", e)
            return {}

        if not filas:
            return {}

        encontrados = sum(f["n"] for f in filas) or 1
        puntajes = {}
        for f in filas:
            base = max(f["base"] or 1, 1)
            p_en_resultados = f["n"] / encontrados
            p_en_corpus = base / total_sumarios
            # +1 para que una voz rarisima no dispare el log al infinito.
            lift = math.log((p_en_resultados + 1e-9) / (p_en_corpus + 1e-9) + 1)
            puntajes[f["voz"]] = round(min(1.0, lift / 5.0), 4)

        return puntajes

    # ── acceso a un fallo ─────────────────────────────────────────────────

    def obtener_fallo(self, identificador, con_texto: bool = True) -> dict:
        """Un fallo completo, por id numerico o por clave natural."""
        columna = "id" if isinstance(identificador, int) else "clave_natural"
        fila = self.con.execute(
            f"SELECT * FROM fallos WHERE {columna}=?", (identificador,)
        ).fetchone()
        if fila is None:
            return {}

        datos = dict(fila)
        datos["url_pdf"] = _url_pdf(fila["enlace_pdf"])
        datos["votos"] = [
            {"juez": v["juez"], "tipo_voto": v["tipo_voto"]}
            for v in self.con.execute(
                "SELECT juez, tipo_voto FROM votos WHERE fallo_id=? ORDER BY orden",
                (fila["id"],),
            )
        ]
        if con_texto:
            datos["sumarios"] = [
                {"orden": s["orden"], "texto": s["texto"], "truncado": bool(s["truncado"])}
                for s in self.con.execute(
                    "SELECT orden, texto, truncado FROM sumarios "
                    "WHERE fallo_id=? ORDER BY orden",
                    (fila["id"],),
                )
            ]
            datos["voces"] = [
                r["ruta"]
                for r in self.con.execute(
                    "SELECT DISTINCT v.ruta FROM voces v "
                    "JOIN sumario_voces sv ON sv.voz_id=v.id "
                    "JOIN sumarios s ON s.id=sv.sumario_id "
                    "WHERE s.fallo_id=? ORDER BY v.ruta",
                    (fila["id"],),
                )
            ]
        return datos


# ═══════════════════════════════════════════════════════════════════════════
#  Salida
# ═══════════════════════════════════════════════════════════════════════════

def _url_pdf(enlace):
    if not enlace:
        return None
    from . import ajustes

    if enlace.startswith("http"):
        return enlace
    return ajustes.BASE_URL + enlace.lstrip("/")


def _a_fecha(valor, inicio: bool = True) -> str:
    """Acepta '2018', '2018-05' o '2018-05-20' y lo lleva a ISO completo."""
    s = str(valor).strip()
    if re.fullmatch(r"\d{4}", s):
        return f"{s}-01-01" if inicio else f"{s}-12-31"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        if inicio:
            return f"{s}-01"
        import calendar

        a, m = int(s[:4]), int(s[5:7])
        return f"{s}-{calendar.monthrange(a, m)[1]:02d}"
    return s


def a_markdown(resultados: list, compacto: bool = True) -> str:
    """
    Markdown pensado para que leerlo cueste poco.

    En modo compacto: ~180 tokens por resultado. Diez resultados entran en
    menos de 2.500 tokens, contra las decenas de miles que costaba una
    busqueda por navegador.
    """
    if not resultados:
        return "_Sin resultados en el corpus local._"

    lineas = []
    for i, r in enumerate(resultados, 1):
        lineas.append(f"### {i}. {r['caratula'] or '(sin caratula)'}")
        meta = [
            p for p in (
                r.get("organismo"), r.get("jurisdiccion"), r.get("fuero"),
                r.get("fecha"), r.get("expediente"),
            ) if p
        ]
        lineas.append(" · ".join(meta))

        texto = r["fragmento"] if compacto else r["sumario"]
        if r.get("sumario_truncado"):
            texto += "  _(extracto del listado; el sumario completo llega con `cosechar detalles`)_"
        lineas.append(f"\n> {texto}\n")

        if r.get("voces"):
            lineas.append(f"**Voces:** {'; '.join(r['voces'])}")
        if r.get("url_pdf"):
            lineas.append(f"**PDF:** {r['url_pdf']}")
        lineas.append(f"`clave: {r['clave']}`")
        lineas.append("")

    return "\n".join(lineas)


def a_json(resultados: list, compacto: bool = True) -> str:
    """JSON. En compacto se omite el sumario entero y va solo el fragmento."""
    if compacto:
        recortados = []
        for r in resultados:
            item = {k: v for k, v in r.items() if k != "sumario"}
            recortados.append(item)
        resultados = recortados
    return json.dumps(resultados, ensure_ascii=False, indent=1)
