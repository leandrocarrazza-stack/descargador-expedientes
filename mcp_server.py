#!/usr/bin/env python3
"""
MCP Server para Mesa Virtual de Entre Ríos
===========================================

Expone herramientas para buscar, listar movimientos y descargar
expedientes del sistema judicial de la provincia de Entre Ríos
(https://mesavirtual.jusentrerios.gov.ar).

Transporte: stdio (servidor local)
Uso:  python mcp_server.py

Configuración en Claude Desktop (~/.config/claude/claude_desktop_config.json):
{
  "mcpServers": {
    "mesa_virtual": {
      "command": "python",
      "args": ["C:/ruta/a/este/mcp_server.py"],
      "cwd": "C:/ruta/al/proyecto"
    }
  }
}
"""

import sys
import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, List

# Agregar el directorio raíz del proyecto al path para que los imports
# de los módulos existentes (modulos/, config.py) funcionen correctamente.
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

from pydantic import BaseModel, Field, ConfigDict, field_validator
from mcp.server.fastmcp import FastMCP

# Configurar logging hacia stderr (no stdout, porque stdout lo usa el protocolo MCP)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("mesa_virtual_mcp")

# ─────────────────────────────────────────────
# Inicializar servidor MCP
# ─────────────────────────────────────────────
mcp = FastMCP("mesa_virtual_mcp")


# ─────────────────────────────────────────────
# Modelos Pydantic (validación de inputs)
# ─────────────────────────────────────────────

class BuscarExpedienteInput(BaseModel):
    """Parámetros para buscar un expediente."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    numero: str = Field(
        ...,
        description=(
            "Número de expediente a buscar. Puede tener formato con barra "
            "(ej: '21/24', '5289/23') o solo número (ej: '22066')."
        ),
        min_length=1,
        max_length=50
    )
    indice: Optional[int] = Field(
        default=None,
        description=(
            "Si la búsqueda devuelve múltiples resultados, indica cuál elegir "
            "(1 = primero). Si no se especifica, se elige el primero disponible."
        ),
        ge=1,
        le=100
    )

    @field_validator("numero")
    @classmethod
    def validar_numero(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El número de expediente no puede estar vacío")
        return v.strip()


class ObtenerMovimientosInput(BaseModel):
    """Parámetros para obtener movimientos de un expediente."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    numero: str = Field(
        ...,
        description="Número de expediente (ej: '21/24', '5289/23').",
        min_length=1,
        max_length=50
    )
    max_movimientos: int = Field(
        default=30,
        description="Cantidad máxima de movimientos a recuperar (default: 30, máximo: 100).",
        ge=1,
        le=100
    )

    @field_validator("numero")
    @classmethod
    def validar_numero(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El número de expediente no puede estar vacío")
        return v.strip()


class DescargarExpedienteInput(BaseModel):
    """Parámetros para descargar el PDF consolidado de un expediente."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    numero: str = Field(
        ...,
        description="Número de expediente a descargar (ej: '21/24', '5289/23').",
        min_length=1,
        max_length=50
    )

    @field_validator("numero")
    @classmethod
    def validar_numero(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El número de expediente no puede estar vacío")
        return v.strip()


# ─────────────────────────────────────────────
# Utilidades de formato y manejo de errores
# ─────────────────────────────────────────────

def _manejar_error(e: Exception, contexto: str = "") -> str:
    """Formatea errores de forma clara y accionable."""
    msg = str(e)
    prefijo = f"Error en {contexto}: " if contexto else "Error: "

    # Errores comunes con mensajes útiles
    if "sesion" in msg.lower() or "session" in msg.lower() or "pickle" in msg.lower():
        return (
            f"{prefijo}Sesión no válida o expirada. "
            "Usá la herramienta 'mesa_virtual_iniciar_sesion' para renovar el login."
        )
    if "not found" in msg.lower() or "no encontrado" in msg.lower():
        return f"{prefijo}Expediente no encontrado. Verificá el número e intentá de nuevo."
    if "timeout" in msg.lower():
        return (
            f"{prefijo}Tiempo de espera agotado. "
            "Mesa Virtual puede estar lenta. Intentá de nuevo en unos minutos."
        )
    if "webdriver" in msg.lower() or "chrome" in msg.lower():
        return (
            f"{prefijo}Error al abrir el navegador Chrome. "
            "Verificá que Chrome esté instalado y que webdriver-manager esté actualizado."
        )

    return f"{prefijo}{msg}"


# ─────────────────────────────────────────────
# Herramienta 1: Verificar sesión
# ─────────────────────────────────────────────

@mcp.tool(
    name="mesa_virtual_verificar_sesion",
    annotations={
        "title": "Verificar Sesión de Mesa Virtual",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def mesa_virtual_verificar_sesion() -> str:
    """
    Verifica si existe una sesión activa guardada para Mesa Virtual de Entre Ríos.

    Comprueba si hay un archivo de sesión guardado (~/.mesa_virtual_sesion.pkl)
    y si esa sesión sigue siendo válida (no expirada).

    No recibe parámetros.

    Returns:
        str: JSON con el estado de la sesión:
        {
            "sesion_activa": bool,     # True si hay sesión válida lista para usar
            "archivo_existe": bool,    # True si existe el archivo de sesión guardado
            "mensaje": str             # Descripción del estado
        }

    Ejemplos de uso:
        - Antes de hacer una búsqueda, verificar si hay sesión disponible
        - Si sesion_activa=False, usar mesa_virtual_iniciar_sesion primero
    """
    def _verificar() -> dict:
        # Import local para evitar cargar Selenium si no es necesario
        from modulos.login import ClienteSelenium, crear_cliente_sesion

        cliente_temp = ClienteSelenium()
        archivo_existe = cliente_temp.sesion_existe()

        if not archivo_existe:
            return {
                "sesion_activa": False,
                "archivo_existe": False,
                "mensaje": (
                    "No hay sesión guardada. "
                    "Usá mesa_virtual_iniciar_sesion para hacer login."
                )
            }

        # Intentar cargar sesión en modo headless para verificar validez
        try:
            cliente = crear_cliente_sesion(usar_sesion_guardada=True, headless=True)
            if cliente:
                cliente.cerrar()
                return {
                    "sesion_activa": True,
                    "archivo_existe": True,
                    "mensaje": "Sesión activa y válida. Lista para operar."
                }
            else:
                return {
                    "sesion_activa": False,
                    "archivo_existe": True,
                    "mensaje": (
                        "El archivo de sesión existe pero está expirado. "
                        "Usá mesa_virtual_iniciar_sesion para renovar el login."
                    )
                }
        except Exception as e:
            return {
                "sesion_activa": False,
                "archivo_existe": True,
                "mensaje": f"No se pudo verificar la sesión: {e}. Intentá renovar el login."
            }

    try:
        resultado = await asyncio.to_thread(_verificar)
        return json.dumps(resultado, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "sesion_activa": False,
            "archivo_existe": False,
            "mensaje": _manejar_error(e, "verificar sesión")
        }, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Herramienta 2: Iniciar sesión
# ─────────────────────────────────────────────

@mcp.tool(
    name="mesa_virtual_iniciar_sesion",
    annotations={
        "title": "Iniciar Sesión en Mesa Virtual",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def mesa_virtual_iniciar_sesion() -> str:
    """
    Abre Chrome para que el usuario haga login manual en Mesa Virtual de Entre Ríos.

    Abre una ventana de Chrome visible y espera hasta 10 minutos a que el usuario
    complete el proceso de autenticación (incluyendo 2FA si corresponde).
    Una vez completado el login, guarda la sesión en ~/.mesa_virtual_sesion.pkl
    para que futuras operaciones funcionen en modo headless (sin ventana).

    IMPORTANTE: Este proceso requiere interacción humana en el navegador.
    El LLM debe avisarle al usuario que debe completar el login en la ventana de Chrome.

    No recibe parámetros.

    Returns:
        str: JSON con el resultado:
        {
            "exito": bool,
            "mensaje": str,
            "sesion_guardada": bool    # True si las cookies se guardaron correctamente
        }

    Cuándo usar:
        - Cuando mesa_virtual_verificar_sesion retorna sesion_activa=False
        - Cuando otras operaciones fallan con error de sesión expirada
    """
    def _iniciar() -> dict:
        from modulos.login import ClienteSelenium

        cliente = ClienteSelenium()
        logger.info("Abriendo navegador para login manual...")

        login_ok = cliente.abrir_navegador_y_loguearse(timeout_segundos=600)

        if not login_ok:
            return {
                "exito": False,
                "sesion_guardada": False,
                "mensaje": "El login no se completó (tiempo agotado o ventana cerrada)."
            }

        # Guardar sesión
        guardado = cliente.guardar_sesion()
        cliente.cerrar()

        if guardado:
            return {
                "exito": True,
                "sesion_guardada": True,
                "mensaje": (
                    "Login exitoso. Sesión guardada correctamente. "
                    "Ya podés usar las otras herramientas sin necesidad de volver a loguearte."
                )
            }
        else:
            return {
                "exito": False,
                "sesion_guardada": False,
                "mensaje": "Login completado pero no se pudo guardar la sesión. Intentá de nuevo."
            }

    try:
        resultado = await asyncio.to_thread(_iniciar)
        return json.dumps(resultado, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "exito": False,
            "sesion_guardada": False,
            "mensaje": _manejar_error(e, "iniciar sesión")
        }, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Herramienta 3: Buscar expediente
# ─────────────────────────────────────────────

@mcp.tool(
    name="mesa_virtual_buscar_expediente",
    annotations={
        "title": "Buscar Expediente en Mesa Virtual",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def mesa_virtual_buscar_expediente(params: BuscarExpedienteInput) -> str:
    """
    Busca un expediente en Mesa Virtual de Entre Ríos por número y retorna sus datos básicos.

    Requiere sesión activa (usar mesa_virtual_verificar_sesion primero).
    Solo busca y retorna datos — NO descarga archivos.

    Args:
        params (BuscarExpedienteInput): Parámetros de búsqueda:
            - numero (str): Número de expediente (ej: "21/24", "5289/23", "22066")
            - indice (Optional[int]): Índice del resultado a elegir si hay varios (default: 1)

    Returns:
        str: JSON con los datos del expediente encontrado:

        Éxito:
        {
            "encontrado": true,
            "expediente": {
                "numero": str,      # Número oficial del expediente
                "caratula": str,    # Nombre/carátula del expediente
                "tribunal": str,    # Tribunal o juzgado
                "estado": str       # Estado actual (activo, archivado, etc.)
            }
        }

        No encontrado:
        {
            "encontrado": false,
            "mensaje": str          # Descripción del error
        }

    Ejemplos de uso:
        - Verificar si existe un expediente antes de descargarlo
        - Obtener la carátula y tribunal de un expediente por número
        - No usar cuando se quiere descargar (usar mesa_virtual_descargar_expediente)
    """
    def _buscar(numero: str, indice: Optional[int]) -> dict:
        from modulos.login import crear_cliente_sesion
        from modulos.navegacion import BuscadorExpedientes

        cliente = crear_cliente_sesion(usar_sesion_guardada=True, headless=True)
        if not cliente:
            raise RuntimeError(
                "Sesión no disponible o expirada. "
                "Usá mesa_virtual_iniciar_sesion para renovar el login."
            )

        try:
            buscador = BuscadorExpedientes(cliente)
            resultado = buscador.buscar(numero, indice_expediente=indice)

            if not resultado:
                return {
                    "encontrado": False,
                    "mensaje": f"No se encontró el expediente '{numero}'. Verificá el número."
                }

            return {
                "encontrado": True,
                "expediente": {
                    "numero": resultado.get("numero", numero),
                    "caratula": resultado.get("caratula", ""),
                    "tribunal": resultado.get("tribunal", ""),
                    "estado": resultado.get("estado", ""),
                }
            }
        finally:
            cliente.cerrar()

    try:
        resultado = await asyncio.to_thread(_buscar, params.numero, params.indice)
        return json.dumps(resultado, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "encontrado": False,
            "mensaje": _manejar_error(e, f"buscar expediente '{params.numero}'")
        }, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Herramienta 4: Obtener movimientos
# ─────────────────────────────────────────────

@mcp.tool(
    name="mesa_virtual_obtener_movimientos",
    annotations={
        "title": "Obtener Movimientos de Expediente",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def mesa_virtual_obtener_movimientos(params: ObtenerMovimientosInput) -> str:
    """
    Obtiene la lista de movimientos procesales de un expediente en Mesa Virtual.

    Busca el expediente y lista todos sus movimientos (actuaciones, escritos, resoluciones).
    Soporta paginación automática para expedientes con muchos movimientos.
    NO descarga los archivos — solo lista la información disponible.

    Args:
        params (ObtenerMovimientosInput): Parámetros:
            - numero (str): Número de expediente (ej: "21/24", "5289/23")
            - max_movimientos (int): Máximo de movimientos a recuperar (default: 30, máximo: 100)

    Returns:
        str: JSON con los movimientos del expediente:

        Éxito:
        {
            "expediente": {
                "numero": str,
                "caratula": str,
                "tribunal": str
            },
            "total_movimientos": int,
            "movimientos": [
                {
                    "indice": int,          # Número de movimiento (1 = más reciente)
                    "descripcion": str,     # Descripción del movimiento
                    "pagina": int,          # Página donde aparece en Mesa Virtual
                    "tiene_archivos": bool  # True si tiene archivos descargables
                }
            ]
        }

        Error:
        {
            "error": true,
            "mensaje": str
        }

    Ejemplos de uso:
        - Ver qué actuaciones tiene un expediente antes de descargarlo
        - Contar cuántos movimientos tiene un expediente
        - No usar para descargar los archivos (usar mesa_virtual_descargar_expediente)
    """
    def _obtener(numero: str, max_movimientos: int) -> dict:
        from modulos.login import crear_cliente_sesion
        from modulos.navegacion import BuscadorExpedientes
        from modulos.descarga import DescargadorArchivos
        import config

        cliente = crear_cliente_sesion(usar_sesion_guardada=True, headless=True)
        if not cliente:
            raise RuntimeError(
                "Sesión no disponible o expirada. "
                "Usá mesa_virtual_iniciar_sesion para renovar el login."
            )

        try:
            # Buscar expediente
            buscador = BuscadorExpedientes(cliente)
            expediente = buscador.buscar(numero)

            if not expediente:
                return {
                    "error": True,
                    "mensaje": f"No se encontró el expediente '{numero}'."
                }

            # Obtener movimientos (usa carpeta temp del proyecto)
            carpeta_temp = Path(config.TEMP_DIR) / "movimientos_temp"
            carpeta_temp.mkdir(parents=True, exist_ok=True)

            descargador = DescargadorArchivos(cliente, carpeta_temp)
            movimientos = descargador.obtener_movimientos(numero, max_movimientos=max_movimientos)

            # Serializar movimientos de forma legible
            movimientos_serializados = []
            for m in (movimientos or []):
                mov_data = {
                    "indice": m.get("indice", 0),
                    "descripcion": m.get("descripcion", "Sin descripción"),
                    "pagina": m.get("pagina", 1),
                    "tiene_archivos": bool(m.get("enlaces_descarga"))
                }
                movimientos_serializados.append(mov_data)

            return {
                "expediente": {
                    "numero": expediente.get("numero", numero),
                    "caratula": expediente.get("caratula", ""),
                    "tribunal": expediente.get("tribunal", ""),
                },
                "total_movimientos": len(movimientos_serializados),
                "movimientos": movimientos_serializados
            }
        finally:
            cliente.cerrar()

    try:
        resultado = await asyncio.to_thread(_obtener, params.numero, params.max_movimientos)
        return json.dumps(resultado, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "mensaje": _manejar_error(e, f"obtener movimientos de '{params.numero}'")
        }, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Herramienta 5: Descargar expediente completo
# ─────────────────────────────────────────────

@mcp.tool(
    name="mesa_virtual_descargar_expediente",
    annotations={
        "title": "Descargar Expediente como PDF Unificado",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def mesa_virtual_descargar_expediente(params: DescargarExpedienteInput) -> str:
    """
    Descarga el expediente completo de Mesa Virtual y genera un PDF unificado.

    Ejecuta el pipeline completo:
    1. Autenticación con sesión guardada
    2. Búsqueda del expediente
    3. Descarga de todos los archivos (RTF/PDF) con paginación automática
    4. Conversión RTF → PDF (usando LibreOffice)
    5. Unificación de todos los PDFs en uno solo ordenado cronológicamente

    ADVERTENCIA: Esta operación puede tardar varios minutos dependiendo de la cantidad
    de archivos del expediente. Avisale al usuario que espere.

    Requiere sesión activa (usar mesa_virtual_verificar_sesion primero).

    Args:
        params (DescargarExpedienteInput): Parámetros:
            - numero (str): Número de expediente a descargar (ej: "21/24", "5289/23")

    Returns:
        str: JSON con el resultado de la descarga:

        Éxito:
        {
            "exito": true,
            "expediente": {
                "numero": str,
                "caratula": str,
                "tribunal": str
            },
            "pdf_final": str,               # Ruta absoluta al PDF generado
            "archivos_descargados": int,    # Cantidad de archivos procesados
            "total_movimientos": int        # Total de movimientos encontrados
        }

        Error:
        {
            "exito": false,
            "tipo_error": str,   # 'auth_failed', 'not_found', 'no_files', 'download_failed', etc.
            "mensaje": str       # Descripción del error con pasos para resolver
        }

    Cuándo usar:
        - Cuando se necesita el PDF completo de un expediente
        - Después de confirmar con mesa_virtual_buscar_expediente que el expediente existe
        - No usar para solo ver los movimientos (usar mesa_virtual_obtener_movimientos)

    Error handling:
        - auth_failed: Sesión expirada → usar mesa_virtual_iniciar_sesion
        - not_found: Expediente no existe → verificar número
        - no_files: Expediente sin archivos adjuntos
        - download_failed: Fallo en descarga → reintentar
    """
    def _descargar(numero: str) -> dict:
        from modulos.pipeline import PipelineDescargador

        pipeline = PipelineDescargador()
        resultado = pipeline.ejecutar(numero)

        if resultado.exito:
            return {
                "exito": True,
                "expediente": {
                    "numero": resultado.expediente.get("numero", numero) if resultado.expediente else numero,
                    "caratula": resultado.expediente.get("caratula", "") if resultado.expediente else "",
                    "tribunal": resultado.expediente.get("tribunal", "") if resultado.expediente else "",
                },
                "pdf_final": str(resultado.pdf_final),
                "archivos_descargados": resultado.archivos_descargados,
                "total_movimientos": len(resultado.movimientos) if resultado.movimientos else 0
            }
        else:
            # Mensajes de error accionables según el tipo
            mensajes_por_tipo = {
                "auth_failed": (
                    "Sesión expirada o no disponible. "
                    "Usá mesa_virtual_iniciar_sesion para renovar el login."
                ),
                "not_found": f"El expediente '{numero}' no fue encontrado. Verificá el número.",
                "no_files": f"El expediente '{numero}' no tiene archivos para descargar.",
                "download_failed": "Falló la descarga de archivos. Intentá de nuevo en unos minutos.",
                "conversion_failed": (
                    "Se descargaron los archivos pero falló la conversión RTF→PDF. "
                    "Verificá que LibreOffice esté instalado."
                ),
                "unification_failed": "Se convirtieron los archivos pero falló la unificación en PDF.",
            }

            tipo = resultado.tipo_error or "exception"
            mensaje = mensajes_por_tipo.get(tipo, resultado.error or "Error desconocido")

            return {
                "exito": False,
                "tipo_error": tipo,
                "mensaje": mensaje
            }

    try:
        resultado = await asyncio.to_thread(_descargar, params.numero)
        return json.dumps(resultado, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "exito": False,
            "tipo_error": "exception",
            "mensaje": _manejar_error(e, f"descargar expediente '{params.numero}'")
        }, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Iniciando MCP Server - Mesa Virtual de Entre Rios")
    mcp.run()  # Transporte stdio por defecto
