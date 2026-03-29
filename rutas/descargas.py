# rutas/descargas.py
"""
Rutas para descarga de expedientes (FASE 3 v2 - Sincrónico).

Arquitectura simplificada: ejecución sincrónica sin Celery.
- Usuario solicita descarga
- Flask ejecuta pipeline bloqueante (~30-120 segundos)
- Retorna JSON con ruta del PDF o error

Modelo: Cada descarga cuesta $3.000 ARS de créditos prepagados

LIMPIEZA: El PDF final se borra del servidor después de que el usuario
lo descarga (after_this_request). Además, al iniciar la app se borran
PDFs con más de PDF_TTL_HOURS horas de antigüedad.
"""

import logging
import os
import time
import threading
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, render_template, after_this_request
from flask_login import login_required, current_user

from modulos.pipeline import PipelineDescargador
from modulos.database import db
from modulos.models import ExpedienteDescargado, SesionUsuarioMV
from modulos.auth_mv import obtener_cookies_usuario
import config

logger = logging.getLogger(__name__)

# Crear blueprint
descargas_bp = Blueprint('descargas', __name__, url_prefix='/descargas')

PRECIO_DESCARGA = config.PRECIO_DESCARGA_ARS

# Horas máximas que un PDF permanece en disco antes de ser borrado
PDF_TTL_HOURS = int(os.environ.get('PDF_TTL_HOURS', '24'))


def limpiar_pdfs_antiguos():
    """
    Borra PDFs del directorio output/ que tengan más de PDF_TTL_HOURS horas.
    Se llama al iniciar la app para evitar que el disco se llene.
    Es segura: si un archivo está en uso o no puede borrarse, lo ignora.
    """
    try:
        ahora = time.time()
        eliminados = 0
        for pdf in Path(config.OUTPUT_DIR).glob("*.pdf"):
            edad_horas = (ahora - pdf.stat().st_mtime) / 3600
            if edad_horas > PDF_TTL_HOURS:
                try:
                    pdf.unlink()
                    eliminados += 1
                except Exception:
                    pass  # Archivo en uso o sin permisos, ignorar
        if eliminados > 0:
            logger.info(f"[CLEANUP] {eliminados} PDF(s) antiguos eliminados de output/")
    except Exception as e:
        logger.warning(f"[CLEANUP] Error limpiando PDFs antiguos: {e}")


def _borrar_diferido(ruta: str, delay: int = 10):
    """
    Borra un archivo después de N segundos en un hilo background.
    Se usa para borrar el PDF después de que send_file() lo haya enviado.
    El delay da tiempo a que Flask termine de transmitir el archivo.
    """
    def borrar():
        time.sleep(delay)
        try:
            if os.path.exists(ruta):
                os.unlink(ruta)
                logger.info(f"[CLEANUP] PDF borrado tras descarga: {Path(ruta).name}")
        except Exception:
            pass  # No es crítico si no se borra ahora — el cleanup de startup lo atrapa
    t = threading.Thread(target=borrar, daemon=True)
    t.start()


@descargas_bp.route('/expediente', methods=['GET', 'POST'])
@login_required
def descargar_expediente_sync():
    """
    Descarga sincrónica de expediente.

    GET: Retorna formulario HTML para ingresar número de expediente
    POST: Ejecuta la descarga del expediente

    Request JSON (POST):
        {
            "numero_expediente": "21/24"
        }

    Response JSON (POST):
        {
            "exito": true,
            "expediente_id": 123,
            "pdf_url": "/descargas/expediente/123/descargar",
            "creditos_restantes": 4,
            "mensaje": "Expediente descargado exitosamente"
        }

    Errores posibles:
        - 400: Parámetros inválidos
        - 402: Créditos insuficientes
        - 500: Error en descarga (sesión expirada, expediente no encontrado, etc)
    """
    # Si es GET, mostrar formulario
    if request.method == 'GET':
        # Verificar si el usuario tiene sesión de Mesa Virtual guardada
        sesion_mv = SesionUsuarioMV.query.filter_by(user_id=current_user.id).first()
        return render_template(
            'descargar_expediente.html',
            creditos=current_user.creditos_disponibles,
            tiene_sesion_mv=sesion_mv is not None,
            mv_usuario=sesion_mv.mv_usuario if sesion_mv else None
        )

    # Si es POST, procesar descarga
    try:
        # 1. PARSEAR REQUEST
        data = request.get_json() or {}
        numero_expediente = data.get('numero_expediente', '').strip()
        indice_expediente = data.get('indice_expediente')  # Opcional: cuál elegir si hay múltiples
        if indice_expediente is not None:
            indice_expediente = int(indice_expediente)

        # 2. VALIDAR ENTRADA
        if not numero_expediente:
            logger.warning(f"Usuario {current_user.id} intentó descargar sin número")
            return jsonify({
                'exito': False,
                'mensaje': 'Número de expediente requerido'
            }), 400

        # 3. VALIDAR CRÉDITOS (los admins están exentos del límite)
        if not current_user.is_admin and current_user.creditos_disponibles < 1:
            logger.warning(f"Usuario {current_user.id} sin créditos (tiene {current_user.creditos_disponibles})")
            return jsonify({
                'exito': False,
                'mensaje': 'Créditos insuficientes. Compra créditos para continuar.',
                'creditos_necesarios': 1,
                'creditos_disponibles': current_user.creditos_disponibles
            }), 402

        # 4. VERIFICAR SESIÓN DE MESA VIRTUAL DEL USUARIO
        cookies_mv = obtener_cookies_usuario(current_user.id)
        if not cookies_mv:
            logger.info(f"Usuario {current_user.id} no tiene sesión MV → redirigir a login")
            return jsonify({
                'exito': False,
                'tipo_error': 'sesion_mv_requerida',
                'mensaje': 'Necesitás conectar tu cuenta de Mesa Virtual primero.',
                'login_url': f'/auth/mv-login?next=/descargas/expediente'
            }), 401

        # 5. EJECUTAR PIPELINE SINCRÓNICO (BLOQUEANTE)
        logger.info(f"Iniciando descarga sincrónica: Usuario {current_user.id}, Expediente {numero_expediente}, Índice {indice_expediente}")

        pipeline = PipelineDescargador()
        resultado = pipeline.ejecutar(
            numero_expediente=numero_expediente,
            limpiar_temp=config.LIMPIAR_TEMP,
            indice_expediente=indice_expediente,
            cookies_mv=cookies_mv  # Pasamos las cookies del usuario
        )

        # 5. PROCESAR RESULTADO DEL PIPELINE
        if resultado.exito:
            logger.info(f"Descarga exitosa: {numero_expediente}, PDF: {resultado.pdf_final}")

            # Guardar en base de datos
            expediente_db = ExpedienteDescargado(
                user_id=current_user.id,
                numero=numero_expediente,
                caratula=resultado.expediente.get('caratula') if resultado.expediente else None,
                tribunal=resultado.expediente.get('tribunal') if resultado.expediente else None,
                pdf_ruta_temporal=str(resultado.pdf_final) if resultado.pdf_final else None,
                estado='completed',
                error_msg=None
            )
            db.session.add(expediente_db)

            # Deducir créditos (los admins no gastan)
            if not current_user.is_admin:
                current_user.creditos_disponibles -= 1
            db.session.commit()

            logger.info(f"Descarga registrada: Usuario {current_user.id} (admin={current_user.is_admin}), créditos restantes: {current_user.creditos_disponibles}")

            return jsonify({
                'exito': True,
                'expediente_id': expediente_db.id,
                'pdf_url': f'/descargas/expediente/{expediente_db.id}/descargar',
                'creditos_restantes': current_user.creditos_disponibles,
                'mensaje': 'Expediente descargado exitosamente'
            }), 200

        elif resultado.tipo_error == 'multiples_opciones':
            # Hay múltiples expedientes: no se descuenta crédito, se devuelven las opciones
            logger.info(f"Múltiples resultados para {numero_expediente}: {len(resultado.opciones)} opciones")
            return jsonify({
                'exito': False,
                'tipo_error': 'multiples_opciones',
                'opciones': resultado.opciones,
                'mensaje': f'Se encontraron {len(resultado.opciones)} expedientes con ese número. Seleccioná cuál descargar.'
            }), 200

        else:
            # Si el pipeline falló por sesión expirada, informar para reconectar
            if resultado.tipo_error == 'auth_failed':
                logger.warning(f"Sesión MV expirada durante descarga para user {current_user.id}")
                return jsonify({
                    'exito': False,
                    'tipo_error': 'sesion_mv_requerida',
                    'mensaje': 'Tu sesión de Mesa Virtual expiró. Necesitás reconectarla.',
                    'login_url': f'/auth/mv-login?next=/descargas/expediente'
                }), 401

            # Error real en pipeline
            logger.error(f"Error en descarga: {numero_expediente} - {resultado.error}")

            # Guardar intento fallido en BD (sin deducir créditos)
            expediente_db = ExpedienteDescargado(
                user_id=current_user.id,
                numero=numero_expediente,
                estado='failed',
                error_msg=resultado.error,
                caratula=None,
                tribunal=None,
                pdf_ruta_temporal=None
            )
            db.session.add(expediente_db)
            db.session.commit()

            return jsonify({
                'exito': False,
                'mensaje': f'Error en descarga: {resultado.error}',
                'tipo_error': resultado.tipo_error or 'unknown',
                'creditos_disponibles': current_user.creditos_disponibles
            }), 500

    except Exception as e:
        logger.error(f"Excepción no manejada en descarga: {str(e)}", exc_info=True)
        return jsonify({
            'exito': False,
            'mensaje': 'Error interno del servidor',
            'error_tipo': type(e).__name__
        }), 500


@descargas_bp.route('/expediente/<int:expediente_id>/descargar', methods=['GET'])
@login_required
def descargar_pdf(expediente_id):
    """
    Descarga el PDF ya generado.

    Validaciones:
    - Solo el dueño del expediente puede descargarlo
    - El archivo debe existir
    """
    try:
        # Obtener expediente de BD
        expediente = ExpedienteDescargado.query.get(expediente_id)

        if not expediente:
            logger.warning(f"Usuario {current_user.id} intentó descargar expediente {expediente_id} inexistente")
            return render_template('error.html', mensaje='Expediente no encontrado'), 404

        # Validar propiedad
        if expediente.user_id != current_user.id:
            logger.warning(f"Usuario {current_user.id} intentó descargar expediente {expediente_id} de otro usuario")
            return render_template('error.html', mensaje='No tienes permiso para descargar este expediente'), 403

        # Validar que archivo exista
        if not expediente.pdf_ruta_temporal or not os.path.exists(expediente.pdf_ruta_temporal):
            logger.error(f"PDF no encontrado: {expediente.pdf_ruta_temporal}")
            return render_template('error.html', mensaje='Archivo PDF no encontrado'), 404

        # Descargar y programar limpieza del archivo
        logger.info(f"Descargando PDF: Usuario {current_user.id}, Expediente {expediente.numero}")

        pdf_path = expediente.pdf_ruta_temporal

        # Programar borrado del PDF 10 segundos después de enviarlo.
        # Esto libera disco en el servidor. El usuario ya tiene su copia.
        _borrar_diferido(pdf_path, delay=10)

        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=f"Expediente_{expediente.numero.replace('/', '_')}.pdf"
        )

    except Exception as e:
        logger.error(f"Error al descargar PDF {expediente_id}: {str(e)}", exc_info=True)
        return render_template('error.html', mensaje='Error al descargar el archivo'), 500


@descargas_bp.route('/historial', methods=['GET'])
@login_required
def historial_descargas():
    """
    Muestra el historial de descargas del usuario.
    """
    try:
        expedientes = ExpedienteDescargado.query.filter_by(
            user_id=current_user.id
        ).order_by(ExpedienteDescargado.creado_en.desc()).all()

        return render_template('historial_descargas.html', expedientes=expedientes)

    except Exception as e:
        logger.error(f"Error al mostrar historial: {str(e)}")
        return render_template('error.html', mensaje='Error al cargar el historial'), 500
