"""
Motor de Búsqueda Híbrido
=========================

Búsqueda full-text + voces del tesauro.
Adapta la query a SQLite (LIKE) o PostgreSQL (tsvector).
"""

import logging
from typing import List, Dict
from sqlalchemy import or_, and_, func, text
from modulos.database import db
from modulos.models import Fallo, FalloTexto

logger = logging.getLogger(__name__)


class BuscadorJurisprudencia:
    """
    Motor de búsqueda híbrido: full-text + voces del tesauro.
    Adapta la query a SQLite (LIKE) o PostgreSQL (tsvector).
    """

    def __init__(self, db_dialect: str = 'sqlite'):
        """
        Args:
            db_dialect: 'sqlite' | 'postgresql'
        """
        self.dialect = db_dialect

    def buscar(self, terminos: List[str], voces_tesauro: List[str], limite: int = 10) -> List[Dict]:
        """
        Búsqueda híbrida: full-text + voces del tesauro.

        Args:
            terminos: Términos de búsqueda libres
            voces_tesauro: Voces jurídicas del tesauro
            limite: Máximo de resultados

        Returns:
            list: Dicts con {fallo_id, score, sumarios[], metadatos}
        """
        try:
            if self.dialect == 'postgresql':
                resultados = self._buscar_postgresql(terminos, voces_tesauro, limite)
            else:
                resultados = self._buscar_sqlite(terminos, voces_tesauro, limite)

            return self.formatear_resultados(resultados)

        except Exception as e:
            logger.error(f"Error en búsqueda: {e}")
            return []

    def _buscar_sqlite(self, terminos: List[str], voces: List[str], limite: int) -> List:
        """Búsqueda en SQLite con LIKE queries."""
        try:
            # Construir condiciones OR para términos y voces
            condiciones = []

            # Buscar términos en contenido de texto
            for termino in terminos:
                if termino:
                    condiciones.append(
                        FalloTexto.contenido_texto.ilike(f"%{termino}%")
                    )

            # Buscar voces en el JSON de voces_tesauro
            for voz in voces:
                if voz:
                    condiciones.append(
                        FalloTexto.voces_tesauro_json.contains(voz)
                    )

            if not condiciones:
                return []

            # Query: JOIN con Fallo, filtrar por condiciones, ordena por fecha
            query = db.session.query(
                Fallo.id.label('fallo_id'),
                Fallo.nombre_archivo,
                Fallo.tribunal,
                Fallo.materia,
                Fallo.fecha_fallo,
                FalloTexto.sumarios_json,
                FalloTexto.voces_tesauro_json,
                func.length(FalloTexto.contenido_texto).label('longitud')
            ).outerjoin(FalloTexto).filter(
                or_(*condiciones)
            ).order_by(
                Fallo.fecha_fallo.desc()
            ).limit(limite)

            return query.all()

        except Exception as e:
            logger.error(f"Error en _buscar_sqlite: {e}")
            return []

    def _buscar_postgresql(self, terminos: List[str], voces: List[str], limite: int) -> List:
        """Búsqueda en PostgreSQL con tsvector."""
        try:
            # En producción, usar tsvector + GIN index
            # Por ahora, usar ILIKE como fallback compatible
            condiciones = []

            for termino in terminos:
                if termino:
                    condiciones.append(
                        FalloTexto.contenido_texto.ilike(f"%{termino}%")
                    )

            for voz in voces:
                if voz:
                    condiciones.append(
                        FalloTexto.voces_tesauro_json.contains(voz)
                    )

            if not condiciones:
                return []

            query = db.session.query(
                Fallo.id.label('fallo_id'),
                Fallo.nombre_archivo,
                Fallo.tribunal,
                Fallo.materia,
                Fallo.fecha_fallo,
                FalloTexto.sumarios_json,
                FalloTexto.voces_tesauro_json,
                func.length(FalloTexto.contenido_texto).label('longitud')
            ).outerjoin(FalloTexto).filter(
                or_(*condiciones)
            ).order_by(
                Fallo.fecha_fallo.desc()
            ).limit(limite)

            return query.all()

        except Exception as e:
            logger.error(f"Error en _buscar_postgresql: {e}")
            return []

    def formatear_resultados(self, resultados: List) -> List[Dict]:
        """
        Prepara resultados para respuesta al cliente.

        Args:
            resultados: Resultados brutos de la búsqueda

        Returns:
            list: Resultados formateados
        """
        try:
            formateados = []

            for row in resultados:
                fallo_id, nombre_archivo, tribunal, materia, fecha_fallo, sumarios_json, voces_json, longitud = row

                # Deserializar JSON
                import json
                try:
                    sumarios = json.loads(sumarios_json) if sumarios_json else []
                except:
                    sumarios = []

                try:
                    voces = json.loads(voces_json) if voces_json else []
                except:
                    voces = []

                formateados.append({
                    'fallo_id': fallo_id,
                    'nombre_archivo': nombre_archivo,
                    'tribunal': tribunal,
                    'materia': materia,
                    'fecha_fallo': fecha_fallo.isoformat() if fecha_fallo else None,
                    'sumarios': sumarios,
                    'voces': voces,
                    'longitud_texto': longitud or 0
                })

            return formateados

        except Exception as e:
            logger.error(f"Error en formatear_resultados: {e}")
            return []
