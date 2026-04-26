"""
Extractor de Texto de Fallos
============================

Extrae texto de PDFs de fallos judiciales usando pdfplumber.
Identifica y extrae secciones de sumario.
"""

import logging
import re
import json
from pathlib import Path

from modulos.database import db
from modulos.models import Fallo, FalloTexto

logger = logging.getLogger(__name__)


class ExtractorFallos:
    """
    Extrae texto de PDFs de fallos judiciales.
    Identifica sumarios y voces del tesauro.
    """

    SUMARIO_INICIO = re.compile(
        r'(?i)(s\s*u\s*m\s*a\s*r\s*i\s*o|sumario\s*:?)',
        re.IGNORECASE
    )
    SUMARIO_FIN = re.compile(
        r'(?i)(fallo\s*:|resolucion\s*:|se\s+resuelve|ello\s+considerando)',
        re.IGNORECASE
    )

    def extraer_texto_completo(self, ruta_pdf: Path) -> str:
        """
        Extrae todo el texto del PDF con pdfplumber.

        Args:
            ruta_pdf: Path al archivo PDF

        Returns:
            str: Texto completo del PDF
        """
        try:
            import pdfplumber
        except (ImportError, Exception) as e:
            logger.error(f"pdfplumber no disponible: {e}")
            return ""

        if not ruta_pdf.exists():
            logger.error(f"PDF no existe: {ruta_pdf}")
            return ""

        try:
            with pdfplumber.open(ruta_pdf) as pdf:
                texto = ""
                for pagina in pdf.pages:
                    texto += pagina.extract_text() or ""
                    texto += "\n---PAGE_BREAK---\n"
                return texto.strip()
        except Exception as e:
            logger.error(f"Error extrayendo PDF {ruta_pdf}: {e}")
            return ""

    def extraer_sumarios(self, texto_completo: str) -> list:
        """
        Identifica bloques de sumario en el texto.
        Busca secciones que comienzan con "SUMARIO" y termina en "FALLO:"

        Args:
            texto_completo: Texto extraído del PDF

        Returns:
            list: Lista de sumarios (strings)
        """
        if not texto_completo:
            return []

        sumarios = []
        lineas = texto_completo.split('\n')

        i = 0
        while i < len(lineas):
            linea = lineas[i]

            # Buscar inicio de sumario
            if self.SUMARIO_INICIO.search(linea):
                # Acumular líneas hasta encontrar fin
                bloque = []
                i += 1
                while i < len(lineas):
                    linea = lineas[i]
                    if self.SUMARIO_FIN.search(linea):
                        break
                    bloque.append(linea.strip())
                    i += 1

                # Unir bloque y limpiar
                sumario = ' '.join(bloque).strip()
                if len(sumario) > 50:  # Filtrar sumarios muy cortos
                    sumarios.append(sumario)

            i += 1

        return sumarios if sumarios else [texto_completo[:500]]  # Fallback: primeras 500 chars

    def procesar_fallo(self, fallo_id: int) -> bool:
        """
        Orquesta la extracción completa de un Fallo.

        Args:
            fallo_id: ID del Fallo en la BD

        Returns:
            bool: True si exitoso
        """
        try:
            fallo = db.session.get(Fallo, fallo_id)
            if not fallo:
                logger.error(f"Fallo {fallo_id} no encontrado")
                return False

            # Marcar como procesando
            fallo.estado_extraccion = 'extrayendo'
            db.session.commit()

            # Extraer texto
            ruta_pdf = Path(fallo.ruta_pdf)
            texto = self.extraer_texto_completo(ruta_pdf)

            if not texto:
                fallo.estado_extraccion = 'error'
                fallo.error_extraccion = 'No se pudo extraer texto del PDF'
                db.session.commit()
                return False

            # Extraer sumarios
            sumarios = self.extraer_sumarios(texto)

            # Obtener voces del tesauro (si está disponible)
            voces = []
            from flask import current_app
            tesauro = current_app.config.get('TESAURO', {})
            if tesauro:
                from modulos.jurisprudencia.tesauro import obtener_voces_para_consulta
                voces = obtener_voces_para_consulta(texto[:1000], tesauro)

            # Guardar en BD
            falloTexto = FalloTexto(fallo_id=fallo_id)
            falloTexto.contenido_texto = texto
            falloTexto.set_sumarios(sumarios)
            falloTexto.set_voces_tesauro(voces)
            falloTexto.longitud_texto = len(texto)
            falloTexto.cantidad_sumarios = len(sumarios)

            fallo.estado_extraccion = 'indexado'
            fallo.error_extraccion = None

            db.session.add(falloTexto)
            db.session.commit()

            logger.info(f"[OK] Fallo {fallo_id}: {len(sumarios)} sumarios, {len(voces)} voces")
            return True

        except Exception as e:
            logger.error(f"Error procesando fallo {fallo_id}: {e}")
            fallo = db.session.get(Fallo, fallo_id)
            if fallo:
                fallo.estado_extraccion = 'error'
                fallo.error_extraccion = str(e)
                db.session.commit()
            return False

    def procesar_pendientes_en_lote(self, limite: int = 50) -> dict:
        """
        Procesa todos los Fallos con estado='pendiente'.

        Args:
            limite: Máximo de fallos a procesar

        Returns:
            dict: {'procesados': N, 'exitosos': M, 'errores': K}
        """
        try:
            fallos_pendientes = Fallo.query.filter_by(
                estado_extraccion='pendiente'
            ).limit(limite).all()

            procesados = 0
            exitosos = 0
            errores = 0

            for fallo in fallos_pendientes:
                if self.procesar_fallo(fallo.id):
                    exitosos += 1
                else:
                    errores += 1
                procesados += 1

            logger.info(f"Batch: {procesados} procesados, {exitosos} exitosos, {errores} errores")
            return {
                'procesados': procesados,
                'exitosos': exitosos,
                'errores': errores
            }

        except Exception as e:
            logger.error(f"Error en batch: {e}")
            return {
                'procesados': 0,
                'exitosos': 0,
                'errores': 0
            }
