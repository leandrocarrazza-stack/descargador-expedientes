# rutas/descargas.py
"""
Rutas para descarga de expedientes (FASE 3 v2 - Sincrónico).

Arquitectura simplificada: ejecución sincrónica sin Celery.
- Usuario solicita descarga
- Flask ejecuta pipeline bloqueante (~30-120 segundos)
- Retorna JSON con ruta del PDF o error

Modelo: Cada descarga cuesta $3.000 ARS de créditos prepagados
"""

import logging
import os
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, render_template
from flask_login import login_required, current_user

from modulos.pipeline import PipelineDescargador
from modulos.database import db
from modulos.models import ExpedienteDescargado
import config

logger = logging.getLogger(__name__)

# Crear blueprint
descargas_bp = Blueprint('descargas', __name__, url_prefix='/descargas')

PRECIO_DESCARGA = config.PRECIO_DESCARGA_ARS


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
        return render_template('descargar_expediente.html',
                             creditos=current_user.creditos_disponibles)

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

        # 4. EJECUTAR PIPELINE SINCRÓNICO (BLOQUEANTE)
        logger.info(f"Iniciando descarga sincrónica: Usuario {current_user.id}, Expediente {numero_expediente}, Índice {indice_expediente}")

        pipeline = PipelineDescargador()
        resultado = pipeline.ejecutar(
            numero_expediente=numero_expediente,
            limpiar_temp=config.LIMPIAR_TEMP,
            indice_expediente=indice_expediente
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

        # Descargar
        logger.info(f"Descargando PDF: Usuario {current_user.id}, Expediente {expediente.numero}")

        return send_file(
            expediente.pdf_ruta_temporal,
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
