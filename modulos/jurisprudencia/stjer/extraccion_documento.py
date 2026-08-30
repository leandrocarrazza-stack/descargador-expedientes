"""
Extraccion de texto de escritos subidos por el usuario (PDF / Word)
=====================================================================

A diferencia de pdf_extractor.ExtractorFallos (que busca la seccion
"sumario" de un fallo YA cosechado del STJER), esto extrae todo el texto
plano de CUALQUIER pdf/docx que suba un usuario -una demanda, un recurso,
etc.- para que chat.ChatSTJER lo interprete.
"""

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

EXTENSIONES_SOPORTADAS = {".pdf", ".docx"}


class ErrorExtraccion(Exception):
    """El archivo no se pudo leer: formato no soportado o vacio."""


def extraer_texto(datos: bytes, nombre_archivo: str) -> str:
    """Extrae texto plano de un PDF o DOCX subido en memoria."""
    extension = Path(nombre_archivo or "").suffix.lower()

    if extension == ".pdf":
        return _extraer_pdf(datos)
    if extension == ".docx":
        return _extraer_docx(datos)
    if extension == ".doc":
        raise ErrorExtraccion(
            "El formato .doc (Word antiguo) no esta soportado. "
            "Guardalo como .docx o .pdf y volve a intentar."
        )
    raise ErrorExtraccion(
        f"Formato {extension or '(sin extension)'} no soportado. "
        f"Usa: {', '.join(sorted(EXTENSIONES_SOPORTADAS))}"
    )


def _extraer_pdf(datos: bytes) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(datos)) as pdf:
            return "\n".join(pagina.extract_text() or "" for pagina in pdf.pages)
    except Exception as e:
        raise ErrorExtraccion(f"No se pudo leer el PDF: {e}") from e


def _extraer_docx(datos: bytes) -> str:
    import docx

    try:
        documento = docx.Document(io.BytesIO(datos))
    except Exception as e:
        raise ErrorExtraccion(f"No se pudo leer el DOCX: {e}") from e
    return "\n".join(p.text for p in documento.paragraphs)
