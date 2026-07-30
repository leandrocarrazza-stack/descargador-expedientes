"""
Tesauro real del STJER
======================

El repo traia `data/jurisprudencia/tesauro.json` con **10 categorias
inventadas** (RESPONSABILIDAD CIVIL, ACCIDENTE DE TRANSITO, ...). No es el
tesauro del STJER: es un placeholder. Por eso la skill no sabia traducir una
consulta a voces juridicas — no tenia contra que traducir.

Este modulo cosecha el arbol real (Materia > Voz Principal > Voz) a
`data/jurisprudencia/tesauro_stjer.json`, que **si se versiona en git**: pesa
poco, cambia poco, y hace que el mapeo a voces funcione incluso sin corpus.

Sobre el mapeo consulta -> voces
--------------------------------
Aca vive solo el matcheo contra las ETIQUETAS (difflib de la stdlib, sin
dependencias nuevas). El mapeo bueno es por **co-ocurrencia sobre el corpus**
y vive en busqueda.py: cada sumario trae su texto Y sus voces asignadas, asi
que el propio corpus enseña que voces usan los jueces cuando hablan de un
tema. Esto de aca es el arranque en frio y la señal secundaria.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from . import ajustes
from .normalizacion import normalizar_texto, tokenizar
from .parser import parece_tesauro_valido, parsear_arbol_tesauro

logger = logging.getLogger(__name__)

MAX_NODOS_A_EXPANDIR = 2000  # tope de seguridad si el arbol viene por AJAX


def _etiqueta(nodo) -> str:
    """La etiqueta 'hoja' de un NodoVoz, para comparar firmas de respuestas."""
    return nodo.voz or nodo.voz_principal or nodo.materia


# ═══════════════════════════════════════════════════════════════════════════
#  Estructura
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Tesauro:
    """
    Arbol de voces con un indice plano para buscar.

    `indice_plano` mapea VOZ -> {materia, voz_principal}, que es lo que se
    necesita para mostrarle al usuario de donde sale cada voz sugerida.
    """

    version: str = ""
    materias: list = field(default_factory=list)
    indice_plano: dict = field(default_factory=dict)

    # cache: [(voz, voz_norm, tokens)]
    _indice_busqueda: list = field(default_factory=list, repr=False)

    def __len__(self):
        return len(self.indice_plano)

    def __bool__(self):
        return bool(self.indice_plano)

    # ── construccion ──────────────────────────────────────────────────────

    @classmethod
    def desde_nodos(cls, nodos, version: str = None) -> "Tesauro":
        """Arma el tesauro desde los NodoVoz que devolvio el parser."""
        arbol = {}
        for n in nodos:
            materia = n.materia or "(sin materia)"
            vp = n.voz_principal or ""
            arbol.setdefault(materia, {}).setdefault(vp, set())
            if n.voz:
                arbol[materia][vp].add(n.voz)

        materias = [
            {
                "nombre": materia,
                "voces_principales": [
                    {"nombre": vp, "voces": sorted(voces)}
                    for vp, voces in sorted(ramas.items())
                    if vp or voces
                ],
            }
            for materia, ramas in sorted(arbol.items())
        ]

        t = cls(version=version or date.today().isoformat(), materias=materias)
        t._reconstruir_indices()
        return t

    def _reconstruir_indices(self) -> None:
        self.indice_plano = {}
        for m in self.materias:
            vps = m.get("voces_principales", [])
            if not vps:
                # Sin nada debajo: el nombre de la materia YA es un termino
                # buscable en si mismo. Pasa si el tesauro del sitio es una
                # lista plana en vez de un arbol de 3 niveles — mejor
                # aprovechar el termino que descartarlo como si no sirviera.
                self.indice_plano.setdefault(
                    m["nombre"], {"materia": "", "voz_principal": ""}
                )
                continue
            for vp in vps:
                nombre_vp = vp.get("nombre") or ""
                voces = vp.get("voces") or []
                if nombre_vp and not voces:
                    # Una voz principal sin hijas es, en la practica, una voz.
                    self.indice_plano[nombre_vp] = {
                        "materia": m["nombre"], "voz_principal": ""
                    }
                for voz in voces:
                    self.indice_plano[voz] = {
                        "materia": m["nombre"], "voz_principal": nombre_vp
                    }

        self._indice_busqueda = [
            (voz, normalizar_texto(voz), set(tokenizar(voz, minimo=3)))
            for voz in self.indice_plano
        ]

    # ── persistencia ──────────────────────────────────────────────────────

    def guardar(self, ruta=None) -> Path:
        ruta = Path(ruta or ajustes.TESAURO_STJER_PATH)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps(
                {
                    "version": self.version,
                    "materias": self.materias,
                    "indice_plano": self.indice_plano,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return ruta

    @classmethod
    def cargar(cls, ruta=None) -> "Tesauro":
        """Carga el tesauro. Devuelve uno vacio si todavia no se cosecho."""
        ruta = Path(ruta or ajustes.TESAURO_STJER_PATH)
        if not ruta.exists():
            logger.info(
                "Todavia no hay tesauro real en %s. Cosechalo con:\n"
                "    python -m scripts.stjer tesauro --cosechar",
                ruta,
            )
            return cls()
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error("No se pudo leer el tesauro %s: %s", ruta, e)
            return cls()

        t = cls(
            version=datos.get("version", ""),
            materias=datos.get("materias", []),
        )
        t._reconstruir_indices()
        return t

    # ── consultas ─────────────────────────────────────────────────────────

    def voces(self) -> list:
        return list(self.indice_plano)

    def ruta_de(self, voz: str) -> str:
        info = self.indice_plano.get(voz)
        if not info:
            return voz
        return " > ".join(
            p for p in (info["materia"], info["voz_principal"], voz) if p
        )

    def buscar_por_etiqueta(self, consulta: str, n: int = 10) -> list:
        """
        Voces cuya ETIQUETA se parece a la consulta.

        Puntaje combinado: solapamiento de tokens (que es lo que de verdad
        importa en lenguaje juridico, donde "daño moral" y "daños morales"
        tienen que caer juntos) mas similitud de cadena como desempate.
        Devuelve [(voz, puntaje)] ordenado de mejor a peor.
        """
        tokens_consulta = set(tokenizar(consulta, minimo=3))
        if not tokens_consulta:
            return []

        consulta_norm = normalizar_texto(consulta)
        puntajes = []

        for voz, voz_norm, tokens_voz in self._indice_busqueda:
            if not tokens_voz:
                continue

            comunes = tokens_consulta & tokens_voz
            # Sobre los tokens de la VOZ: que la consulta sea larga no tiene
            # que castigar a una voz corta que aparece entera en ella.
            solapamiento = len(comunes) / len(tokens_voz)
            if solapamiento == 0 and voz_norm not in consulta_norm:
                continue

            similitud = SequenceMatcher(None, consulta_norm, voz_norm).ratio()
            puntaje = 0.75 * solapamiento + 0.25 * similitud
            if voz_norm in consulta_norm:  # la voz aparece literal
                puntaje = min(1.0, puntaje + 0.25)

            puntajes.append((voz, round(puntaje, 4)))

        puntajes.sort(key=lambda p: (-p[1], p[0]))
        return puntajes[:n]

    def expandir(self, voz: str) -> list:
        """
        Voces hermanas de una dada (misma voz principal).

        Sirve para ampliar una busqueda que trajo poco sin salirse del tema.
        """
        info = self.indice_plano.get(voz)
        if not info:
            return []
        return [
            otra
            for otra, i in self.indice_plano.items()
            if otra != voz
            and i["materia"] == info["materia"]
            and i["voz_principal"] == info["voz_principal"]
        ]


# ═══════════════════════════════════════════════════════════════════════════
#  Cosecha
# ═══════════════════════════════════════════════════════════════════════════

def cosechar_arbol(cliente, destino=None, max_nodos: int = MAX_NODOS_A_EXPANDIR) -> Tesauro:
    """
    Trae el arbol del tesauro y lo guarda.

    Cubre los dos casos que puede devolver la Fase 0:
      * el arbol entero viene en una sola respuesta -> un request;
      * cada nivel se pide por AJAX -> se expande en anchura, con tope.
    """
    respuesta = cliente.arbol_tesauro()
    nodos = list(parsear_arbol_tesauro(respuesta.html))
    logger.info("Tesauro: %d nodos en la respuesta inicial", len(nodos))

    # Firma (conjunto de etiquetas) de la respuesta inicial. Si "expandir" un
    # nodo devuelve esta MISMA firma, es que el sitio no diferencio el pedido
    # y volvio a mandar la lista de siempre — no son hijos reales.
    #
    # Se observo en la practica: sin esta guarda, cada "expansion" que en
    # realidad no hacia nada releia los mismos ~160 items de siempre y los
    # contaba como hijos nuevos de cada nodo — 160 x 160 x 160 = una cola de
    # cientos de miles y mas de dos horas de corrida para terminar
    # descartando todo en la validacion final.
    firma_raiz = frozenset(_etiqueta(n) for n in nodos)

    # Si ya vinieron hojas, el arbol llego entero y no hay nada que expandir.
    if any(n.nivel >= 2 for n in nodos):
        logger.info("El arbol vino completo en una sola respuesta")
    else:
        pendientes = [n for n in nodos if n.ref and n.nivel < 2]
        vistos = {n.ruta for n in nodos}
        expandidos = 0
        omitidos_por_eco = 0

        while pendientes and expandidos < max_nodos:
            nodo = pendientes.pop(0)
            expandidos += 1
            try:
                r = cliente.arbol_tesauro(nodo.ref)
            except Exception as e:
                logger.warning("No se pudo expandir %s: %s", nodo.ruta, e)
                continue

            hijos = parsear_arbol_tesauro(
                r.html,
                materia=nodo.materia,
                voz_principal=nodo.voz_principal or (
                    nodo.materia and nodo.nivel == 1 and nodo.voz_principal
                ) or "",
            )

            firma_hijos = frozenset(_etiqueta(h) for h in hijos)
            if firma_hijos and firma_hijos == firma_raiz:
                omitidos_por_eco += 1
                continue

            for h in hijos:
                if h.ruta and h.ruta not in vistos:
                    vistos.add(h.ruta)
                    nodos.append(h)
                    if h.ref and h.nivel < 2:
                        pendientes.append(h)

            if expandidos % 25 == 0:
                logger.info(
                    "Tesauro: %d nodos expandidos, %d en cola, %d voces",
                    expandidos, len(pendientes), len(nodos),
                )

        if omitidos_por_eco:
            logger.warning(
                "%d de %d expansiones devolvieron la misma lista de siempre "
                "(el sitio no diferencio el pedido): se descartaron para no "
                "inflar el arbol con duplicados. El 'ref' que se usa para "
                "expandir un nodo probablemente no esta funcionando — mira "
                "docs/STJER_FASE0.md para capturar el pedido real con "
                "DevTools y ajustar descubrimiento/selectores.json o "
                "formato_consulta.json.",
                omitidos_por_eco, expandidos,
            )

        if expandidos >= max_nodos:
            logger.warning(
                "Se corto la expansion del tesauro en %d nodos (tope de "
                "seguridad). Subilo con --max-nodos si el arbol es mas grande.",
                max_nodos,
            )

    es_valido, motivo = parece_tesauro_valido(nodos)
    if not es_valido:
        logger.error(
            "Lo cosechado NO parece un tesauro juridico real: %s\n"
            "No se guarda para no pisar un tesauro anterior con basura. "
            "Esto suele pasar cuando el pedido no llega al panel real del "
            "Tesauro (con --motor http) y el sitio devuelve la pagina de "
            "busqueda comun en su lugar. Probá con --motor navegador "
            "--visible para ver la pagina real y ajustar el selector "
            "'panel_tesauro' en descubrimiento/selectores.json si hace "
            "falta.",
            motivo,
        )
        tesauro = Tesauro.desde_nodos([])
        tesauro.version = f"INVALIDO: {motivo}"
        return tesauro

    tesauro = Tesauro.desde_nodos(nodos)
    ruta = tesauro.guardar(destino)
    logger.info(
        "Tesauro guardado en %s: %d materias, %d voces",
        ruta, len(tesauro.materias), len(tesauro),
    )
    return tesauro


def importar_desde_corpus(con, destino=None) -> Tesauro:
    """
    Arma el tesauro con las voces vistas en los fallos ya cosechados.

    Es el plan B si el panel del tesauro resulta imposible de recorrer: las
    voces que de verdad se usan en los fallos son, para buscar, mejores que
    la taxonomia completa.
    """
    from .parser import NodoVoz

    nodos = [
        NodoVoz(
            materia=f["materia"] or "",
            voz_principal=f["voz_principal"] or "",
            voz=f["voz"],
            nivel=2,
        )
        for f in con.execute(
            "SELECT materia, voz_principal, voz FROM voces ORDER BY ruta"
        )
    ]
    tesauro = Tesauro.desde_nodos(nodos)
    tesauro.guardar(destino)
    logger.info("Tesauro derivado del corpus: %d voces", len(tesauro))
    return tesauro


# ═══════════════════════════════════════════════════════════════════════════
#  Compatibilidad con la app web
# ═══════════════════════════════════════════════════════════════════════════

def obtener_voces_para_consulta(consulta: str, tesauro=None, limite: int = 10) -> list:
    """
    Reemplazo drop-in de `modulos.jurisprudencia.tesauro.obtener_voces_para_consulta`.

    Misma firma, misma forma de salida (lista de strings). La diferencia es
    que ahora matchea contra el tesauro REAL en vez de contra 10 categorias
    inventadas, y con puntaje en vez de con igualdad exacta de tokens.

    Acepta el dict que ya usaba la app web, o un Tesauro, o nada (carga el
    tesauro real del disco).
    """
    if isinstance(tesauro, Tesauro):
        t = tesauro
    elif isinstance(tesauro, dict) and tesauro:
        if "materias" in tesauro or "indice_plano" in tesauro:
            t = Tesauro(
                version=tesauro.get("version", ""),
                materias=tesauro.get("materias", []),
            )
            t._reconstruir_indices()
        else:
            # El formato viejo {VOZ: {terminos: [...]}}: se envuelve.
            from .parser import NodoVoz

            t = Tesauro.desde_nodos([NodoVoz(voz=k, nivel=2) for k in tesauro])
    else:
        t = Tesauro.cargar()

    if not t:
        return []
    return [voz for voz, _ in t.buscar_por_etiqueta(consulta, n=limite)]
