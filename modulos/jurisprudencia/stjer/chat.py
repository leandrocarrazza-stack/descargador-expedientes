"""
Chat de jurisprudencia STJER
=============================

Interpreta una consulta en lenguaje natural -corta, larga, mal escrita, o
directamente el texto de un escrito judicial completo (demanda, recurso,
etc.)- y la traduce en terminos de busqueda + voces reales del tesauro
([[tesauro_stjer]]), para despues buscar en el corpus local
(busqueda.BuscadorCorpus).

Con cliente de Claude: la interpretacion la hace el modelo, que puede seguir
el hilo de una consulta mal escrita o extraer los argumentos centrales de un
escrito largo. Las voces que devuelve se validan contra la lista real del
tesauro -si Claude "inventa" una que no existe, se descarta- para no mandarle
al buscador una voz que no va a matchear nunca.

Sin cliente (no hay ANTHROPIC_API_KEY) o si la llamada falla: se cae a
sugerir_voces()/buscar() de toda la vida, que funciona sin red y sin costo
pero es mas literal (no entiende una consulta muy larga o muy mal escrita
tan bien como Claude).
"""

import json
import logging

from .busqueda import BuscadorCorpus, ErrorBusqueda
from .tesauro_stjer import Tesauro

logger = logging.getLogger(__name__)

# Un escrito judicial real rara vez supera esto; sirve de tope de costo/tokens
# antes de mandarlo entero a Claude.
MAX_CARACTERES_DOCUMENTO = 60_000

MODELO = "claude-sonnet-5"

SYSTEM_PROMPT_BASE = """Sos un asistente legal especializado en la jurisprudencia
del Superior Tribunal de Justicia de Entre Rios (STJER). Tu tarea es leer una
consulta (a veces mal escrita, larga o coloquial) o el texto de un escrito
judicial, y decidir que jurisprudencia conviene buscar.

Reglas:
- Las "voces_juridicas" que devuelvas TIENEN que copiarse EXACTAMENTE de la
  lista de voces reales de abajo (no inventes ninguna nueva ni la parafrasees).
  Si ninguna aplica bien, devolve una lista vacia.
- Los "terminos_busqueda" son entre 2 y 6 palabras o frases cortas en
  castellano juridico que describan el nucleo de lo que se busca. No repitas
  frases de cortesia o de encuadre ("necesito saber", "quisiera consultar",
  "buenos dias").
- Si la entrada es un escrito largo (demanda, contestacion, recurso, etc.) en
  vez de una pregunta directa, identificá los 2 o 3 argumentos juridicos
  centrales y buscá jurisprudencia que sirva para sostenerlos o para
  anticipar la postura contraria.

Respondé SIEMPRE con JSON valido, sin texto alrededor, en este formato exacto:
{
  "terminos_busqueda": ["termino1", "termino2"],
  "voces_juridicas": ["voz exacta 1", "voz exacta 2"],
  "respuesta_usuario": "Texto breve en castellano explicando que se va a buscar"
}

Voces reales disponibles (elegi SOLO de esta lista, copiandolas tal cual):
"""


def _listado_de_voces(tesauro: Tesauro) -> str:
    return "\n".join(f"- {v}" for v in sorted(tesauro.voces()))


class ChatSTJER:
    """Interpreta consultas o escritos y busca en el corpus STJER."""

    def __init__(self, con, cliente_anthropic=None, tesauro=None):
        self.con = con
        self.cliente = cliente_anthropic
        self.tesauro = tesauro if tesauro is not None else Tesauro.cargar()
        self.buscador = BuscadorCorpus(con, tesauro=self.tesauro)

    # ── consulta corta (chat) ────────────────────────────────────────────

    def procesar_mensaje(self, mensaje: str, historial=None, limite: int = 5) -> dict:
        """Interpreta una consulta de chat y devuelve fallos relacionados."""
        if not mensaje or not mensaje.strip():
            return self._respuesta_vacia("Escribime una consulta.")

        interpretacion = None
        if self.cliente:
            interpretacion = self._interpretar_con_claude(mensaje, historial)
        if interpretacion is None:
            interpretacion = self._interpretar_localmente(mensaje)

        return self._buscar_y_formatear(interpretacion, limite)

    # ── escrito completo (demanda, recurso, etc.) ────────────────────────

    def procesar_documento(self, texto: str, nombre_archivo: str = "", limite: int = 8) -> dict:
        """Interpreta el texto de un escrito subido y sugiere jurisprudencia de apoyo."""
        texto = (texto or "").strip()
        if not texto:
            return self._respuesta_vacia(
                "El documento no tiene texto para analizar."
            )

        truncado = len(texto) > MAX_CARACTERES_DOCUMENTO
        texto_recortado = texto[:MAX_CARACTERES_DOCUMENTO]

        interpretacion = None
        if self.cliente:
            entrada = f"Escrito judicial ({nombre_archivo}):\n\n{texto_recortado}"
            interpretacion = self._interpretar_con_claude(entrada, historial=None)
        if interpretacion is None:
            # Sin IA, se usan las primeras lineas del escrito como consulta.
            interpretacion = self._interpretar_localmente(texto_recortado[:2000])

        resultado = self._buscar_y_formatear(interpretacion, limite)
        if truncado:
            resultado["respuesta"] += (
                "\n\n(El documento era muy largo: se analizaron los primeros "
                f"{MAX_CARACTERES_DOCUMENTO:,} caracteres.)"
            )
        return resultado

    # ── interpretacion ────────────────────────────────────────────────────

    def _interpretar_con_claude(self, texto_usuario: str, historial=None) -> dict | None:
        try:
            system = SYSTEM_PROMPT_BASE + _listado_de_voces(self.tesauro)
            messages = [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in (historial or [])[-10:]
            ]
            messages.append({"role": "user", "content": texto_usuario})

            respuesta = self.cliente.messages.create(
                model=MODELO, max_tokens=700, system=system, messages=messages,
            )
            texto_respuesta = respuesta.content[0].text

            inicio, fin = texto_respuesta.find("{"), texto_respuesta.rfind("}") + 1
            if inicio < 0 or fin <= inicio:
                logger.warning(
                    "Claude no devolvio JSON reconocible: %.200s", texto_respuesta
                )
                return None
            datos = json.loads(texto_respuesta[inicio:fin])
        except Exception as e:
            logger.error("Fallo la interpretacion con Claude: %s", e)
            return None

        voces_reales = set(self.tesauro.voces())
        datos["voces_juridicas"] = [
            v for v in (datos.get("voces_juridicas") or []) if v in voces_reales
        ]
        datos.setdefault("terminos_busqueda", [])
        datos.setdefault("respuesta_usuario", "")
        return datos

    def _interpretar_localmente(self, texto: str) -> dict:
        sugerencias = self.buscador.sugerir_voces(texto, n=5)
        return {
            "terminos_busqueda": [texto],
            "voces_juridicas": [s["voz"] for s in sugerencias],
            "respuesta_usuario": "Búsqueda por palabras clave (sin IA).",
        }

    # ── busqueda + formato ───────────────────────────────────────────────

    def _buscar_y_formatear(self, interpretacion: dict, limite: int) -> dict:
        terminos = interpretacion.get("terminos_busqueda") or []
        voces = interpretacion.get("voces_juridicas") or []
        consulta = " ".join(str(t) for t in terminos).strip()

        resultados = []
        if consulta:
            try:
                resultados = self.buscador.buscar(
                    consulta, voces=voces or None, limite=limite
                )
            except ErrorBusqueda:
                resultados = []

        respuesta = interpretacion.get("respuesta_usuario") or ""
        if resultados:
            respuesta += f"\n\nEncontré {len(resultados)} fallo(s) relacionado(s):"
        elif not respuesta:
            respuesta = "No encontré fallos que coincidan. Probá con otros términos."

        return {
            "respuesta": respuesta,
            "resultados": resultados,
            "terminos_usados": terminos,
            "voces_usadas": voces,
        }

    @staticmethod
    def _respuesta_vacia(mensaje: str) -> dict:
        return {
            "respuesta": mensaje,
            "resultados": [],
            "terminos_usados": [],
            "voces_usadas": [],
        }
