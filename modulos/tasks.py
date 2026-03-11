"""
Tareas Celery para descargas asincrónicas
==========================================

Las tareas se ejecutan en workers de Celery en background.
Permite que el servidor Flask siga respondiendo mientras se descargan expedientes.

Uso:
    celery -A modulos.celery_app worker --loglevel=info

    # En Flask, disparar una tarea:
    from modulos.celery_app import celery_app
    task = descargar_expediente_task.delay(user_id, numero_exp)
    print(task.id)  # ID para hacer polling
"""

from datetime import datetime
from typing import Dict, Any, Optional

from modulos.celery_app import celery_app
from modulos.database import db
from modulos.models import User, ExpedienteDescargado
from modulos.pipeline import PipelineDescargador
from modulos.selenium_pool import obtener_pool
from modulos.logger import crear_logger

import config

# Importar app para contexto de Flask en tareas
from servidor import app

logger = crear_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  TAREA: DESCARGAR EXPEDIENTE
# ═══════════════════════════════════════════════════════════════════════════


@celery_app.task(
    bind=True,
    name='tareas.descargar_expediente',
    max_retries=config.MAX_REINTENTOS,
    default_retry_delay=30  # Reintentar en 30 segundos
)
def descargar_expediente_task(
    self,
    user_id: int,
    numero_expediente: str,
    expediente_preseleccionado: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Tarea asincrónica para descargar un expediente.

    Pasos:
    1. Validar usuario y créditos
    2. Ejecutar pipeline de descarga
    3. Guardar resultado en BD
    4. Actualizar créditos del usuario

    Args:
        user_id: ID del usuario
        numero_expediente: Ej: "21/24"
        expediente_preseleccionado: Datos del expediente si ya fue seleccionado

    Returns:
        dict: {
            'exito': bool,
            'expediente_id': int,
            'pdf_url': str,
            'mensaje': str,
            'error': str (si exito=False)
        }

    Raises:
        Retries automáticos si falla (hasta MAX_REINTENTOS)
    """

    logger.info(f"📥 Iniciando descarga: Usuario {user_id}, Expediente {numero_expediente}")

    # Ejecutar dentro del contexto de Flask para acceder a la BD
    with app.app_context():
        try:
            # ═════════════════════════════════════════════════════════════════
            #  PASO 1: Validar usuario y créditos
            # ═════════════════════════════════════════════════════════════════

                usuario = User.query.get(user_id)
            if not usuario:
                return {
                    'exito': False,
                    'expediente_id': None,
                    'pdf_url': None,
                    'mensaje': 'Usuario no encontrado',
                    'error': f'Usuario {user_id} no existe'
                }

            # Verificar créditos
            if usuario.creditos_disponibles < 1:
                return {
                    'exito': False,
                    'expediente_id': None,
                    'pdf_url': None,
                    'mensaje': 'Créditos insuficientes',
                    'error': 'No tienes créditos disponibles. Compra más para continuar.'
                }

            # ═════════════════════════════════════════════════════════════════
            #  PASO 2: Ejecutar pipeline
            # ═════════════════════════════════════════════════════════════════

            logger.info(f"🔄 Ejecutando pipeline para: {numero_expediente}")

            # Obtener driver del pool
            pool = obtener_pool()
            # Nota: El PipelineDescargador crea su propio driver internamente
            # En FASE 4, integraríamos el pool aquí

            pipeline = PipelineDescargador()
            resultado_pipeline = pipeline.ejecutar(
                numero_expediente=numero_expediente,
                expediente_preseleccionado=expediente_preseleccionado,
                limpiar_temp=config.LIMPIAR_TEMP
            )

            # ═════════════════════════════════════════════════════════════════
            #  PASO 3: Guardar resultado en BD
            # ═════════════════════════════════════════════════════════════════

            if resultado_pipeline.exito:
                logger.info(f"✅ Descarga exitosa: {resultado_pipeline.pdf_final}")

                # Crear registro en BD
                expediente_db = ExpedienteDescargado(
                    user_id=user_id,
                    numero=numero_expediente,
                    caratula=resultado_pipeline.expediente.caratula if resultado_pipeline.expediente else None,
                    tribunal=resultado_pipeline.expediente.tribunal if resultado_pipeline.expediente else None,
                    pdf_ruta_temporal=str(resultado_pipeline.pdf_final),
                    estado='completed',
                    completado_en=datetime.utcnow()
                )
                db.session.add(expediente_db)

                # ═════════════════════════════════════════════════════════════
                #  PASO 4: Actualizar créditos
                # ═════════════════════════════════════════════════════════════

                usuario.creditos_disponibles -= 1
                usuario.creditos_usados_mes = (usuario.creditos_usados_mes or 0) + 1

                db.session.commit()

                logger.info(
                    f"💳 Crédito deducido: Usuario {usuario.email} "
                    f"ahora tiene {usuario.creditos_disponibles} créditos"
                )

                return {
                    'exito': True,
                    'expediente_id': expediente_db.id,
                    'pdf_url': f'/descargas/expediente/{expediente_db.id}/descargar',
                    'mensaje': f'Expediente {numero_expediente} descargado exitosamente',
                    'creditos_restantes': usuario.creditos_disponibles
                }

            else:
                logger.error(f"❌ Fallo en pipeline: {resultado_pipeline.error}")

                # Registrar intento fallido en BD
                expediente_db = ExpedienteDescargado(
                    user_id=user_id,
                    numero=numero_expediente,
                    estado='failed',
                    error_msg=str(resultado_pipeline.error),
                    completado_en=datetime.utcnow()
                )
                db.session.add(expediente_db)
                db.session.commit()

                # Reintentar si aún hay intentos
                if self.request.retries < self.max_retries:
                    logger.warning(
                        f"🔄 Reintentando descarga ({self.request.retries + 1}/"
                        f"{self.max_retries})..."
                    )
                    raise self.retry(exc=Exception(resultado_pipeline.error))

                return {
                    'exito': False,
                    'expediente_id': expediente_db.id,
                    'pdf_url': None,
                    'mensaje': f'Error al descargar: {resultado_pipeline.error}',
                    'error': str(resultado_pipeline.error)
                }

        except Exception as e:
            logger.error(f"💥 Error inesperado en tarea: {str(e)}", exc_info=True)

            # Reintentar si es posible
            if self.request.retries < self.max_retries:
                logger.warning(
                    f"🔄 Reintentando por error ({self.request.retries + 1}/"
                    f"{self.max_retries})..."
                )
                raise self.retry(exc=e)

            return {
                'exito': False,
                'expediente_id': None,
                'pdf_url': None,
                'mensaje': 'Error inesperado durante la descarga',
                'error': str(e)
            }


# ═══════════════════════════════════════════════════════════════════════════
#  TAREA: LIMPIAR DESCARGAS ANTIGUAS
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(name='tareas.limpiar_descargas_antiguas')
def limpiar_descargas_antiguas_task(dias: int = 7) -> Dict[str, Any]:
    """
    Limpia expedientes descargados hace más de X días.

    Tarea programada (ej: cada medianoche).

    Args:
        dias: Eliminar descargas más antiguas que X días

    Returns:
        dict: Cantidad de registros eliminados
    """
    from datetime import timedelta

    logger.info(f"🧹 Limpiando descargas más antiguas de {dias} días")

    with app.app_context():
        try:
            fecha_limite = datetime.utcnow() - timedelta(days=dias)

            # Buscar expedientes antiguos
            expedientes_viejos = ExpedienteDescargado.query.filter(
                ExpedienteDescargado.completado_en < fecha_limite
            ).all()

            cantidad = len(expedientes_viejos)

            # Eliminar archivos y registros
            for exp in expedientes_viejos:
                if exp.pdf_ruta_temporal:
                    try:
                        from pathlib import Path
                        Path(exp.pdf_ruta_temporal).unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning(f"⚠️ No se pudo eliminar {exp.pdf_ruta}: {e}")

                db.session.delete(exp)

            db.session.commit()

            logger.info(f"✅ {cantidad} descargas antiguas eliminadas")

            return {
                'exito': True,
                'cantidad_eliminada': cantidad
            }

        except Exception as e:
            logger.error(f"❌ Error al limpiar descargas: {e}")
            return {
                'exito': False,
                'error': str(e)
            }


# ═══════════════════════════════════════════════════════════════════════════
#  TAREA: LIMPIAR POOL DE SELENIUM
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(name='tareas.verificar_pool_selenium')
def verificar_pool_selenium_task() -> Dict[str, Any]:
    """
    Verifica el estado del pool de Selenium.

    Tarea de monitoreo que se puede ejecutar periódicamente.

    Returns:
        dict: Estado del pool
    """
    try:
        pool = obtener_pool()
        estado = pool.estado()
        logger.info(f"📊 Estado del pool Selenium: {estado}")
        return {
            'exito': True,
            'estado': estado
        }
    except Exception as e:
        logger.error(f"❌ Error al verificar pool: {e}")
        return {
            'exito': False,
            'error': str(e)
        }
