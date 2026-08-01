#!/usr/bin/env python3
"""
MCP Server para Jurisprudencia STJER
======================================

Expone herramientas de solo lectura sobre el corpus local de jurisprudencia
del Superior Tribunal de Justicia de Entre Rios (STJER): busqueda de texto
completo, voces del tesauro real, y el detalle completo de un fallo.

A diferencia del chat web (modulos/jurisprudencia/stjer/chat.py), este
servidor NO llama a la API de Anthropic para interpretar la consulta: quien
lee el pedido del usuario, decide que terminos/voces usar, y redacta la
respuesta final es el propio Claude que esta corriendo estas herramientas
(Claude Desktop, con tu suscripcion) -no se consume API por separado, mas
alla del uso normal de tu plan-.

Dos formas de transporte, segun como lo conectes:

1. **stdio** (default) - para clientes que arrancan el proceso ellos mismos
   via `command`/`args` en su config (Claude Code, o un
   claude_desktop_config.json clasico):

       python mcp_server_jurisprudencia.py

   {
     "mcpServers": {
       "jurisprudencia_stjer": {
         "command": "python",
         "args": ["C:/ruta/a/este/mcp_server_jurisprudencia.py"],
         "cwd": "C:/ruta/al/proyecto"
       }
     }
   }

2. **HTTP local** (`--http`) - para el dialogo "Agregar conector
   personalizado" de Claude Desktop, que solo acepta una URL de servidor MCP
   remoto (no un comando local). El proceso queda corriendo y escuchando en
   http://127.0.0.1:8765/mcp -tiene que seguir corriendo mientras lo uses-:

       python mcp_server_jurisprudencia.py --http

   En "Agregar conector personalizado": Nombre = lo que quieras, URL del
   servidor MCP remoto = http://127.0.0.1:8765/mcp, OAuth = dejar vacio (no
   hace falta, es local).
"""

import sys
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, List

# Agregar el directorio raiz del proyecto al path para que los imports de
# modulos/ funcionen igual que en mcp_server.py.
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("jurisprudencia_stjer_mcp")

mcp = FastMCP("jurisprudencia_stjer_mcp", port=8765)


# ─────────────────────────────────────────────
# Modelos Pydantic (validacion de inputs)
# ─────────────────────────────────────────────

class BuscarInput(BaseModel):
    """Parametros para buscar jurisprudencia."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    consulta: str = Field(
        ...,
        description=(
            "Terminos de busqueda de texto libre en castellano juridico "
            "(2 a 6 palabras o una frase corta, UN solo concepto por "
            "llamada). Si el pedido del usuario mezcla varios conceptos "
            "distintos, no los pongas todos juntos: llama a esta "
            "herramienta una vez por cada concepto y combina los "
            "resultados vos mismo -la busqueda exige que TODAS las "
            "palabras de la consulta aparezcan en el mismo sumario-."
        ),
        min_length=1,
        max_length=300,
    )
    voces: Optional[List[str]] = Field(
        default=None,
        description=(
            "Voces EXACTAS del tesauro para acotar la busqueda (ver "
            "jurisprudencia_listar_voces o jurisprudencia_sugerir_voces). "
            "No inventes voces que no esten en esas listas."
        ),
    )
    fuero: Optional[str] = Field(
        default=None,
        description="Ej: 'civil', 'laboral', 'penal'. Coincidencia laxa.",
    )
    organismo: Optional[str] = Field(
        default=None, description="Nombre parcial del organismo o camara."
    )
    desde: Optional[str] = Field(
        default=None,
        description="Fecha minima del fallo: 'YYYY', 'YYYY-MM' o 'YYYY-MM-DD'.",
    )
    hasta: Optional[str] = Field(
        default=None,
        description="Fecha maxima del fallo, mismo formato que 'desde'.",
    )
    limite: int = Field(default=10, ge=1, le=30, description="Maximo de resultados.")


class SugerirVocesInput(BaseModel):
    """Parametros para sugerir voces del tesauro."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    consulta: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description=(
            "Consulta o fragmento de texto (puede ser un parrafo de un "
            "escrito) para el que se quieren voces del tesauro relacionadas."
        ),
    )
    limite: int = Field(default=8, ge=1, le=20)


class ListarVocesInput(BaseModel):
    """Parametros para listar el tesauro."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    materia: Optional[str] = Field(
        default=None,
        description="Si se indica, solo devuelve las voces de esa materia (coincidencia laxa).",
    )


class ObtenerFalloInput(BaseModel):
    """Parametros para obtener un fallo completo."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    identificador: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description=(
            "Id numerico del fallo (el 'fallo_id' de un resultado de "
            "jurisprudencia_buscar) o su clave natural (ej: 'sha1:...')."
        ),
    )


# ─────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────

def _con_buscador(fn):
    """Abre el corpus de solo lectura, corre fn(buscador) y cierra la conexion."""
    from modulos.jurisprudencia.stjer import ajustes, corpus
    from modulos.jurisprudencia.stjer.busqueda import BuscadorCorpus

    con = corpus.abrir(ajustes.CORPUS_PATH, solo_lectura=True)
    try:
        return fn(BuscadorCorpus(con))
    finally:
        con.close()


def _error_corpus_no_existe(e: FileNotFoundError) -> str:
    return json.dumps({"error": str(e)}, ensure_ascii=False)


# ─────────────────────────────────────────────
# Herramienta 1: buscar
# ─────────────────────────────────────────────

@mcp.tool(
    name="jurisprudencia_buscar",
    annotations={
        "title": "Buscar Jurisprudencia STJER",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def jurisprudencia_buscar(params: BuscarInput) -> str:
    """
    Busca fallos del Superior Tribunal de Justicia de Entre Rios (STJER) por
    texto libre, con filtros opcionales de voz del tesauro, fuero, organismo
    y fecha.

    El corpus es local (fallos ya cosechados del sitio del STJER, con
    sumario, voces juridicas y link al PDF). No hace falta red ni sesion:
    responde en milisegundos.

    Devuelve una lista de SUMARIOS (no de fallos: un fallo con varios
    sumarios puede aparecer mas de una vez, cada vez con un fragmento
    distinto), ordenados por relevancia, con caratula, organismo, fecha,
    fragmento resaltado, voces asignadas y link al PDF.

    Ejemplos de uso:
        - Responder una pregunta juridica citando jurisprudencia real
        - Buscar precedentes que sostengan un argumento de un escrito
        - No usar para traer el texto completo de un fallo puntual (usar
          jurisprudencia_obtener_fallo con el fallo_id)
    """
    def _hacer(buscador):
        from modulos.jurisprudencia.stjer.busqueda import ErrorBusqueda

        try:
            return buscador.buscar(
                params.consulta,
                voces=params.voces,
                fuero=params.fuero,
                organismo=params.organismo,
                desde=params.desde,
                hasta=params.hasta,
                limite=params.limite,
            )
        except ErrorBusqueda as e:
            return {"error": str(e)}

    try:
        resultado = await asyncio.to_thread(_con_buscador, _hacer)
        return json.dumps(resultado, ensure_ascii=False, indent=1, default=str)
    except FileNotFoundError as e:
        return _error_corpus_no_existe(e)
    except Exception as e:
        logger.exception("Error en jurisprudencia_buscar")
        return json.dumps({"error": f"Error buscando: {e}"}, ensure_ascii=False)


# ─────────────────────────────────────────────
# Herramienta 2: sugerir voces
# ─────────────────────────────────────────────

@mcp.tool(
    name="jurisprudencia_sugerir_voces",
    annotations={
        "title": "Sugerir Voces del Tesauro STJER",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def jurisprudencia_sugerir_voces(params: SugerirVocesInput) -> str:
    """
    Sugiere voces REALES del tesauro juridico del STJER relacionadas con una
    consulta o un fragmento de texto (por ejemplo, un parrafo de un escrito
    subido), combinando coincidencia de etiqueta con co-ocurrencia sobre el
    corpus ya cosechado.

    Util antes de jurisprudencia_buscar para saber que voces existen de
    verdad y acotar por ellas. Las voces que devuelve siempre estan en el
    tesauro real -no inventes otras-.
    """
    def _hacer(buscador):
        return buscador.sugerir_voces(params.consulta, n=params.limite)

    try:
        resultado = await asyncio.to_thread(_con_buscador, _hacer)
        return json.dumps(resultado, ensure_ascii=False, indent=1)
    except FileNotFoundError as e:
        return _error_corpus_no_existe(e)
    except Exception as e:
        logger.exception("Error en jurisprudencia_sugerir_voces")
        return json.dumps({"error": f"Error: {e}"}, ensure_ascii=False)


# ─────────────────────────────────────────────
# Herramienta 3: listar voces del tesauro
# ─────────────────────────────────────────────

@mcp.tool(
    name="jurisprudencia_listar_voces",
    annotations={
        "title": "Listar Voces del Tesauro STJER",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def jurisprudencia_listar_voces(params: ListarVocesInput) -> str:
    """
    Devuelve el tesauro juridico real del STJER: materia > voz principal >
    voz. Son las UNICAS voces validas para filtrar jurisprudencia_buscar.

    Si el usuario carga un escrito largo (demanda, recurso, etc.), usa esta
    lista (o jurisprudencia_sugerir_voces) para elegir las voces que
    correspondan a sus argumentos centrales -no inventes voces que no
    esten aca-.
    """
    def _hacer() -> dict:
        from modulos.jurisprudencia.stjer.tesauro_stjer import Tesauro

        tesauro = Tesauro.cargar()
        if not tesauro:
            return {"error": "Todavia no hay tesauro cosechado en este corpus."}

        materias = tesauro.materias
        if params.materia:
            filtro = params.materia.lower()
            materias = [m for m in materias if filtro in m["nombre"].lower()]

        return {"version": tesauro.version, "materias": materias}

    try:
        resultado = await asyncio.to_thread(_hacer)
        return json.dumps(resultado, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.exception("Error en jurisprudencia_listar_voces")
        return json.dumps({"error": f"Error: {e}"}, ensure_ascii=False)


# ─────────────────────────────────────────────
# Herramienta 4: obtener fallo completo
# ─────────────────────────────────────────────

@mcp.tool(
    name="jurisprudencia_obtener_fallo",
    annotations={
        "title": "Obtener Fallo Completo STJER",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def jurisprudencia_obtener_fallo(params: ObtenerFalloInput) -> str:
    """
    Devuelve un fallo completo del corpus STJER: todos sus sumarios (sin
    truncar), voces asignadas, votos de los jueces, y el link al PDF
    original.

    Usa el 'fallo_id' o la 'clave' que vienen en los resultados de
    jurisprudencia_buscar.
    """
    def _hacer(buscador):
        identificador = (
            int(params.identificador)
            if params.identificador.isdigit()
            else params.identificador
        )
        return buscador.obtener_fallo(identificador)

    try:
        resultado = await asyncio.to_thread(_con_buscador, _hacer)
        if not resultado:
            return json.dumps(
                {"error": f"No hay ningun fallo con id/clave {params.identificador!r}"},
                ensure_ascii=False,
            )
        return json.dumps(resultado, ensure_ascii=False, indent=1, default=str)
    except FileNotFoundError as e:
        return _error_corpus_no_existe(e)
    except Exception as e:
        logger.exception("Error en jurisprudencia_obtener_fallo")
        return json.dumps({"error": f"Error: {e}"}, ensure_ascii=False)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    if "--http" in sys.argv:
        logger.info(
            "Iniciando MCP Server - Jurisprudencia STJER (HTTP en "
            "http://127.0.0.1:8765/mcp, dejalo corriendo)"
        )
        mcp.run(transport="streamable-http")
    else:
        logger.info("Iniciando MCP Server - Jurisprudencia STJER (stdio)")
        mcp.run()  # Transporte stdio por defecto
