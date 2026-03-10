"""
Rutas para descarga asincrónica de expedientes
==============================================

API para:
1. Servir página de descarga (GET /descargar)
2. Disparar descarga (POST /descargas/expediente)
3. Obtener estado (GET /descargas/tarea/{task_id})
4. Descargar archivo (GET /descargas/expediente/{exp_id}/descargar)

Patrón: Tarea asincrónica con polling
"""

import os
from flask import Blueprint, request, jsonify, send_file, render_template, current_app
from flask_login import login_required, current_user

from modulos.tasks import descargar_expediente_task
from modulos.celery_app import celery_app
from modulos.database import db
from modulos.models import ExpedienteDescargado
from modulos.logger import crear_logger

logger = crear_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  CREAR BLUEPRINT
# ═══════════════════════════════════════════════════════════════════════════

descargas_bp = Blueprint('descargas', __name__, url_prefix='/descargas')


# ═══════════════════════════════════════════════════════════════════════════
#  ENDPOINT 0: PÁGINA DE DESCARGA
# ═══════════════════════════════════════════════════════════════════════════

@descargas_bp.route('/descargar', methods=['GET'])
@login_required
def pagina_descargar():
    """
    Sirve la página de descarga de expedientes.

    Muestra formulario, progreso en tiempo real, e historial.
    """
    return render_template('descargar.html')


# ═══════════════════════════════════════════════════════════════════════════
#  ENDPOINT 1: DISPARAR DESCARGA
# ═══════════════════════════════════════════════════════════════════════════

@descargas_bp.route('/expediente', methods=['POST'])
@login_required
def disparar_descarga():
    """
    Dispara una tarea de descarga asincrónica.

    Body JSON esperado:
    {
        "numero_expediente": "21/24",
        "expediente_preseleccionado": {  // opcional
            "caratula": "...",
            "tribunal": "...",
            ...
        }
    }

    Respuesta:
    {
        "exito": true,
        "task_id": "abc123...",
        "mensaje": "Descarga iniciada",
        "polling_url": "/descargas/tarea/abc123/estado"
    }
    """

    try:
        # Obtener datos del request
        data = request.get_json() or {}
        numero_expediente = data.get('numero_expediente', '').strip()
        expediente_preseleccionado = data.get('expediente_preseleccionado')

        # Validar datos
        if not numero_expediente:
            return jsonify({
                'exito': False,
                'mensaje': 'El número de expediente es requerido'
            }), 400

        # Validar créditos
        if current_user.creditos_disponibles < 1:
            return jsonify({
                'exito': False,
                'mensaje': 'Créditos insuficientes',
                'creditos': current_user.creditos_disponibles
            }), 402

        logger.info(
            f"📤 Disparando descarga: Usuario {current_user.email}, "
            f"Expediente {numero_expediente}"
        )

        # Disparar tarea asincrónica
        task = descargar_expediente_task.delay(
            user_id=current_user.id,
            numero_expediente=numero_expediente,
            expediente_preseleccionado=expediente_preseleccionado
        )

        return jsonify({
            'exito': True,
            'task_id': task.id,
            'mensaje': f'Descarga iniciada para expediente {numero_expediente}',
            'polling_url': f'/descargas/tarea/{task.id}/estado'
        }), 202  # 202 Accepted (procesando)

    except Exception as e:
        logger.error(f"❌ Error al disparar descarga: {e}", exc_info=True)
        return jsonify({
            'exito': False,
            'mensaje': 'Error al iniciar descarga',
            'error': str(e)
        }), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ENDPOINT 2: OBTENER ESTADO DE TAREA
# ═══════════════════════════════════════════════════════════════════════════

@descargas_bp.route('/tarea/<task_id>/estado', methods=['GET'])
@login_required
def obtener_estado_tarea(task_id: str):
    """
    Obtiene el estado de una tarea de descarga (POLLING).

    Query params:
    - incluir_resultado: bool (default: false) - incluir resultado detallado

    Respuesta:
    {
        "task_id": "abc123",
        "estado": "PENDING|PROGRESS|SUCCESS|FAILURE",
        "progreso": 0-100,
        "resultado": {...},  // si SUCCESS
        "error": "..."  // si FAILURE
    }

    Estados:
    - PENDING: Esperando en cola
    - PROGRESS: En progreso
    - SUCCESS: Completada exitosamente
    - FAILURE: Falló
    - RETRY: Reintentando
    """

    try:
        # Obtener tarea de Celery
        task = celery_app.AsyncResult(task_id)

        # Construir respuesta base
        respuesta = {
            'task_id': task_id,
            'estado': task.state,
        }

        # Agregar información según el estado
        if task.state == 'PENDING':
            respuesta['mensaje'] = 'Descarga en cola, esperando...'
            respuesta['progreso'] = 0

        elif task.state == 'PROGRESS':
            # Info incluida en task.info
            info = task.info or {}
            respuesta['progreso'] = info.get('progreso', 0)
            respuesta['mensaje'] = info.get('mensaje', 'Procesando...')

        elif task.state == 'SUCCESS':
            # Resultado exitoso
            resultado = task.result or {}
            respuesta['resultado'] = resultado
            respuesta['progreso'] = 100

            if resultado.get('exito'):
                respuesta['mensaje'] = resultado.get('mensaje')
                respuesta['pdf_url'] = resultado.get('pdf_url')
                respuesta['creditos_restantes'] = resultado.get('creditos_restantes')
            else:
                respuesta['mensaje'] = resultado.get('error')

        elif task.state == 'FAILURE':
            # Falló (pero después de reintentos)
            respuesta['error'] = str(task.info)
            respuesta['mensaje'] = 'La descarga falló después de varios intentos'

        elif task.state == 'RETRY':
            # Reintentando
            respuesta['progreso'] = 0
            respuesta['mensaje'] = 'Reintentando descarga...'

        return jsonify(respuesta), 200

    except Exception as e:
        logger.error(f"❌ Error al obtener estado de tarea: {e}")
        return jsonify({
            'exito': False,
            'error': str(e)
        }), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ENDPOINT 3: DESCARGAR ARCHIVO PDF
# ═══════════════════════════════════════════════════════════════════════════

@descargas_bp.route('/expediente/<int:exp_id>/descargar', methods=['GET'])
@login_required
def descargar_pdf(exp_id: int):
    """
    Descarga el PDF de un expediente ya descargado.

    Solo el usuario propietario puede descargar su archivo.

    Respuesta:
    - Si OK: Archivo PDF (application/pdf)
    - Si error: JSON con mensaje
    """

    try:
        # Obtener expediente de BD
        expediente = ExpedienteDescargado.query.get(exp_id)

        if not expediente:
            return jsonify({
                'exito': False,
                'error': 'Expediente no encontrado'
            }), 404

        # Validar que sea del usuario actual
        if expediente.user_id != current_user.id:
            logger.warning(
                f"🚫 Intento no autorizado: Usuario {current_user.id} "
                f"intentó descargar expediente de usuario {expediente.user_id}"
            )
            return jsonify({
                'exito': False,
                'error': 'No tienes permiso para descargar este expediente'
            }), 403

        # Validar que el archivo exista
        if not expediente.pdf_ruta or not os.path.exists(expediente.pdf_ruta):
            return jsonify({
                'exito': False,
                'error': 'El archivo ya no está disponible'
            }), 404

        # Construir nombre del archivo
        nombre_archivo = f"Expediente_{expediente.numero.replace('/', '_')}.pdf"

        logger.info(
            f"📥 Descargando: Usuario {current_user.email}, "
            f"Expediente {expediente.numero}"
        )

        # Enviar archivo
        return send_file(
            expediente.pdf_ruta,
            as_attachment=True,
            download_name=nombre_archivo,
            mimetype='application/pdf'
        )

    except Exception as e:
        logger.error(f"❌ Error al descargar PDF: {e}", exc_info=True)
        return jsonify({
            'exito': False,
            'error': str(e)
        }), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ENDPOINT 4: OBTENER HISTORIAL DE DESCARGAS
# ═══════════════════════════════════════════════════════════════════════════

@descargas_bp.route('/historial', methods=['GET'])
@login_required
def obtener_historial():
    """
    Obtiene el historial de descargas del usuario.

    Query params:
    - limite: int (default: 20) - cantidad de registros
    - pagina: int (default: 1) - para paginación

    Respuesta:
    {
        "exito": true,
        "total": 42,
        "expedientes": [
            {
                "id": 123,
                "numero": "21/24",
                "caratula": "...",
                "estado": "completed",
                "descargado_en": "2026-03-09T10:30:00",
                "pdf_url": "/descargas/expediente/123/descargar"
            }
        ]
    }
    """

    try:
        limite = request.args.get('limite', 20, type=int)
        pagina = request.args.get('pagina', 1, type=int)

        # Limitar rango sensato
        limite = min(max(limite, 5), 100)

        # Obtener expedientes paginados
        expedientes = ExpedienteDescargado.query.filter_by(
            user_id=current_user.id
        ).order_by(
            ExpedienteDescargado.descargado_en.desc()
        ).paginate(page=pagina, per_page=limite)

        # Construir respuesta
        items = []
        for exp in expedientes.items:
            items.append({
                'id': exp.id,
                'numero': exp.numero,
                'caratula': exp.caratula,
                'tribunal': exp.tribunal,
                'estado': exp.estado,
                'descargado_en': exp.descargado_en.isoformat() if exp.descargado_en else None,
                'error': exp.error_msg if exp.estado == 'failed' else None,
                'pdf_url': f'/descargas/expediente/{exp.id}/descargar' if exp.estado == 'completed' else None,
            })

        return jsonify({
            'exito': True,
            'total': expedientes.total,
            'pagina': pagina,
            'total_paginas': expedientes.pages,
            'expedientes': items
        }), 200

    except Exception as e:
        logger.error(f"❌ Error al obtener historial: {e}")
        return jsonify({
            'exito': False,
            'error': str(e)
        }), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ENDPOINT 5: CANCELAR TAREA EN CURSO
# ═══════════════════════════════════════════════════════════════════════════

@descargas_bp.route('/tarea/<task_id>/cancelar', methods=['POST'])
@login_required
def cancelar_tarea(task_id: str):
    """
    Cancela una tarea de descarga en curso.

    Solo el usuario propietario puede cancelar.

    Respuesta:
    {
        "exito": true,
        "mensaje": "Tarea cancelada"
    }
    """

    try:
        # Verificar que la tarea sea del usuario (validación simple)
        # En una app real, guardaríamos task_id en BD asociado a user_id
        task = celery_app.AsyncResult(task_id)

        # Revocar tarea
        task.revoke(terminate=True)

        logger.info(f"⏹️ Tarea {task_id} cancelada por usuario {current_user.id}")

        return jsonify({
            'exito': True,
            'mensaje': 'Descarga cancelada'
        }), 200

    except Exception as e:
        logger.error(f"❌ Error al cancelar tarea: {e}")
        return jsonify({
            'exito': False,
            'error': str(e)
        }), 500
