"""
Tareas Asincrónicas para Descargador de Expedientes
===================================================

Define tareas que se ejecutan en workers Celery de forma asincrónica.
Las tareas se ejecutan en background sin bloquear Flask.

Importante:
- Cada tarea necesita acceso a Flask app context para SQLAlchemy
- Implementar reintentos automáticos para fallos de sesión
- Loguear estados en detalle para debugging

Ejemplo de uso desde Flask:
    from modulos.tasks import descargar_expediente_task

    # Ejecutar en background (retorna inmediatamente)
    task = descargar_expediente_task.delay(
        user_id=1,
        numero_expediente="21/24"
    )

    # Obtener estado
    status = task.status  # 'PENDING', 'PROGRESS', 'SUCCESS', 'FAILURE'
    result = task.result  # resultado cuando esté listo
"""

import logging
from pathlib import Path

from celery import shared_task
from modulos.celery_app import celery_app
from modulos.pipeline import PipelineDescargador
from modulos.database import db
from modulos.models import ExpedienteDescargado, User

# NO importar aquí para evitar circular import
# from servidor import app
# Se importa lazy dentro de cada función

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  TAREA PRINCIPAL: DESCARGAR EXPEDIENTE
# ═══════════════════════════════════════════════════════════════════════════


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # Reintentar en 60 segundos
    time_limit=600,  # Timeout: 10 minutos
    acks_late=True,  # Confirmar después de ejecutar
    name='descargar_expediente_task'
)
def descargar_expediente_task(self, user_id, numero_expediente):
    """
    Descarga asincrónica de un expediente.

    Args:
        user_id (int): ID del usuario que solicita la descarga
        numero_expediente (str): Número de expediente (ej: "21/24")

    Returns:
        dict: {
            "exito": bool,
            "expediente_id": int (si exito),
            "pdf_url": str (si exito),
            "error": str (si falla),
            "tipo_error": str
        }

    Raises:
        Reintenta automáticamente si falla (max 3 intentos)
    """

    # Información de la tarea
    logger.info(f"TAREA INICIADA: {self.request.id}")
    logger.info(f"Usuario: {user_id}, Expediente: {numero_expediente}")

    try:
        # ═════════════════════════════════════════════════════════════════
        #  NECESARIO: Contexto de Flask para SQLAlchemy
        # ═════════════════════════════════════════════════════════════════
        # Import lazy para evitar circular import
        from servidor import app

        with app.app_context():

            # 1. VALIDAR USUARIO
            usuario = User.query.get(user_id)
            if not usuario:
                logger.error(f"Usuario {user_id} no encontrado")
                return {
                    "exito": False,
                    "error": "Usuario no encontrado",
                    "tipo_error": "user_not_found"
                }

            logger.info(f"Usuario validado: {usuario.email}")

            # 2. EJECUTAR PIPELINE DE DESCARGA
            logger.info(f"Iniciando pipeline...")
            pipeline = PipelineDescargador()
            resultado = pipeline.ejecutar(
                numero_expediente=numero_expediente,
                limpiar_temp=True
            )

            # 3. PROCESAR RESULTADO
            if resultado.exito:
                logger.info(f"Pipeline exitoso. PDF: {resultado.pdf_final}")

                # Guardar en BD
                expediente_db = ExpedienteDescargado(
                    user_id=user_id,
                    numero=numero_expediente,
                    caratula=resultado.expediente.get('caratula') if resultado.expediente else None,
                    tribunal=resultado.expediente.get('tribunal') if resultado.expediente else None,
                    pdf_ruta_temporal=str(resultado.pdf_final) if resultado.pdf_final else None,
                    estado='completed',
                    error_msg=None
                )
                db.session.add(expediente_db)

                # Deducir créditos (solo si éxito)
                usuario.creditos_disponibles -= 1
                db.session.commit()

                logger.info(f"Expediente guardado: {expediente_db.id}")
                logger.info(f"Créditos deducidos: {usuario.creditos_disponibles} restantes")

                return {
                    "exito": True,
                    "expediente_id": expediente_db.id,
                    "pdf_url": f"/descargas/expediente/{expediente_db.id}/descargar",
                    "numero_expediente": numero_expediente,
                    "creditos_restantes": usuario.creditos_disponibles
                }

            else:
                # Pipeline falló o necesita intervención
                logger.error(f"Pipeline falló: {resultado.error}")

                # ESPECIAL: Si hay múltiples expedientes, retornar lista para selector web
                if resultado.tipo_error == "multiple_results" and resultado.expedientes_disponibles:
                    logger.info(f"Múltiples expedientes encontrados, retornando lista para selección")
                    return {
                        "exito": False,
                        "error": resultado.error,
                        "tipo_error": "multiple_results",
                        "expedientes_disponibles": resultado.expedientes_disponibles,
                        "creditos_disponibles": usuario.creditos_disponibles
                    }

                # Guardar intento fallido (sin deducir créditos)
                expediente_db = ExpedienteDescargado(
                    user_id=user_id,
                    numero=numero_expediente,
                    estado='failed',
                    error_msg=resultado.error,
                    caratula=None,
                    tribunal=None,
                    pdf_ruta_temporal=None
                )
                db.session.add(expediente_db)
                db.session.commit()

                logger.info(f"Intento fallido guardado en BD")

                # Reintentar si es error de sesión (SesionNoValida, etc)
                if "sesion" in resultado.error.lower() or "expirado" in resultado.error.lower():
                    logger.warning(f"Sesión inválida, reintentando en 60s...")
                    # Celery reintentará automáticamente
                    raise Exception(f"Sesión inválida: {resultado.error}")

                # Otros errores no se retentan
                return {
                    "exito": False,
                    "error": resultado.error,
                    "tipo_error": resultado.tipo_error or "unknown",
                    "creditos_disponibles": usuario.creditos_disponibles
                }

    except Exception as exc:
        """
        Manejo de excepciones no esperadas.
        Reintenta automáticamente si se configura max_retries.
        """
        logger.error(f"Excepción en tarea: {str(exc)}", exc_info=True)

        # Contar reintentos
        retry_count = self.request.retries
        logger.warning(f"Reintento {retry_count} de {self.max_retries}")

        # Reintentar si no llegó al máximo
        if self.request.retries < self.max_retries:
            logger.info(f"Reintentando en 60 segundos...")
            raise self.retry(exc=exc, countdown=60)

        # Máx reintentos alcanzado
        logger.error(f"Máximo de reintentos alcanzado para {numero_expediente}")

        try:
            from servidor import app
            with app.app_context():
                usuario = User.query.get(user_id)
                return {
                    "exito": False,
                    "error": f"Error después de {self.max_retries} reintentos: {str(exc)}",
                    "tipo_error": "max_retries_exceeded",
                    "creditos_disponibles": usuario.creditos_disponibles if usuario else 0
                }
        except:
            return {
                "exito": False,
                "error": f"Error fatal: {str(exc)}",
                "tipo_error": "fatal_error",
                "creditos_disponibles": 0
            }


# ═══════════════════════════════════════════════════════════════════════════
#  TAREA AUXILIAR: LIMPIAR ARCHIVOS ANTIGUOS (Opcional)
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(
    bind=True,
    name='limpiar_archivos_antiguos_task'
)
def limpiar_archivos_antiguos_task(self):
    """
    Tarea periódica que limpia archivos PDF temporales más antiguos de 7 días.

    Se puede ejecutar con schedule periódico (ej: diariamente).

    Returns:
        dict: {"archivos_eliminados": int, "espacio_liberado_mb": float}
    """
    logger.info("Iniciando limpieza de archivos antiguos...")

    try:
        from datetime import datetime, timedelta
        import os
        from servidor import app

        with app.app_context():
            # Configurar carpeta de descargas
            descargas_dir = Path(__file__).parent.parent / "descargas"

            if not descargas_dir.exists():
                logger.warning(f"Carpeta {descargas_dir} no existe")
                return {"archivos_eliminados": 0, "espacio_liberado_mb": 0}

            # Buscar archivos más antiguos de 7 días
            ahora = datetime.now()
            siete_dias_atras = ahora - timedelta(days=7)
            archivos_eliminados = 0
            espacio_liberado = 0

            for archivo in descargas_dir.glob("Expediente_*.pdf"):
                tiempo_modificacion = datetime.fromtimestamp(archivo.stat().st_mtime)

                if tiempo_modificacion < siete_dias_atras:
                    tamaño_kb = archivo.stat().st_size / 1024
                    archivo.unlink()  # Eliminar
                    archivos_eliminados += 1
                    espacio_liberado += tamaño_kb

                    logger.info(f"Eliminado: {archivo.name} ({tamaño_kb:.2f} KB)")

            logger.info(f"Limpieza completada: {archivos_eliminados} archivos, {espacio_liberado / 1024:.2f} MB liberados")

            return {
                "archivos_eliminados": archivos_eliminados,
                "espacio_liberado_mb": espacio_liberado / 1024
            }

    except Exception as exc:
        logger.error(f"Error en limpieza: {str(exc)}", exc_info=True)
        return {
            "exito": False,
            "error": str(exc)
        }
