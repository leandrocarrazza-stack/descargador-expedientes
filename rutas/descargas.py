# rutas/descargas.py
"""
Rutas para descarga de expedientes (con polling asincrónico).

Arquitectura: el pipeline corre en un thread background.
- POST /descargas/expediente  → valida, lanza thread, devuelve job_id (respuesta inmediata)
- GET  /descargas/estado/<id> → el frontend consulta el estado cada 3s (polling)

Esto evita el timeout de ~60s del proxy de Render en expedientes extensos.

Modelo: Cada descarga cuesta 1 crédito prepagado.

LIMPIEZA: El PDF final se borra del servidor después de que el usuario
lo descarga. Además, al iniciar la app se borran PDFs con más de
PDF_TTL_HOURS horas de antigüedad.
"""

import logging
import os
import time
import threading
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, render_template, current_app
from flask_login import login_required, current_user

from modulos.pipeline import PipelineDescargador
from modulos.database import db
from modulos.models import ExpedienteDescargado, SesionUsuarioMV
from modulos.auth_mv import obtener_cookies_usuario
import config

# ── Jobs en memoria ───────────────────────────────────────────────────────────
# Guarda el estado de cada descarga en curso.
# Como Gunicorn corre con 1 worker, este dict es compartido por todos los requests.
# Estructura: { job_id: { estado, user_id, timestamp, ... } }
_jobs: dict = {}
_job_events: dict = {}  # { job_id: threading.Event() } para long-polling
JOB_TTL_SEGUNDOS = 600  # 10 minutos: tiempo máximo para que un job viva en memoria


def _limpiar_jobs_viejos():
    """Elimina jobs de más de 10 minutos para no acumular memoria."""
    ahora = time.time()
    ids_viejos = [jid for jid, j in list(_jobs.items()) if ahora - j.get('timestamp', 0) > JOB_TTL_SEGUNDOS]
    for jid in ids_viejos:
        _jobs.pop(jid, None)
        _job_events.pop(jid, None)


def _run_pipeline(app, job_id, user_id, numero_expediente, indice_expediente, cookies_mv):
    """
    Ejecuta el pipeline completo en un thread separado.
    Necesita el objeto 'app' para poder usar el contexto de Flask (BD, config, etc.)
    fuera del hilo principal.
    """
    log = logging.getLogger(__name__)

    with app.app_context():
        try:
            log.info(f"[JOB {job_id[:8]}] INICIANDO pipeline para expediente {numero_expediente}")

            pipeline = PipelineDescargador()
            log.info(f"[JOB {job_id[:8]}] Pipeline creado, llamando a ejecutar()...")

            resultado = pipeline.ejecutar(
                numero_expediente=numero_expediente,
                limpiar_temp=config.LIMPIAR_TEMP,
                indice_expediente=indice_expediente,
                cookies_mv=cookies_mv
            )

            log.info(f"[JOB {job_id[:8]}] Pipeline completó con exito={resultado.exito}, error={resultado.tipo_error}")

            if resultado.exito:
                # Guardar en BD y descontar crédito
                log.info(f"[JOB {job_id[:8]}] Guardando en BD...")
                from modulos.models import User
                user = User.query.get(user_id)

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

                if user and not user.is_admin:
                    user.creditos_disponibles -= 1
                db.session.commit()

                creditos_restantes = user.creditos_disponibles if user else 0
                log.info(
                    f"[JOB {job_id[:8]}] Descarga OK: {numero_expediente}, créditos restantes: {creditos_restantes}"
                )
                _jobs[job_id].update({
                    'estado': 'completo',
                    'expediente_id': expediente_db.id,
                    'pdf_url': f'/descargas/expediente/{expediente_db.id}/descargar',
                    'creditos_restantes': creditos_restantes,
                })

            elif resultado.tipo_error == 'multiples_opciones':
                log.info(f"[JOB {job_id[:8]}] Múltiples opciones encontradas")
                _jobs[job_id].update({
                    'estado': 'multiples_opciones',
                    'opciones': resultado.opciones,
                })

            elif resultado.tipo_error == 'auth_failed':
                log.warning(f"[JOB {job_id[:8]}] Sesión MV expirada")
                _jobs[job_id].update({
                    'estado': 'error',
                    'tipo_error': 'sesion_mv_requerida',
                    'mensaje': 'Tu sesión de Mesa Virtual expiró. Reconectá tu cuenta.',
                    'login_url': '/auth/mv-login?next=/descargas/expediente',
                })

            else:
                log.error(f"[JOB {job_id[:8]}] Error en pipeline: {resultado.error}")
                _jobs[job_id].update({
                    'estado': 'error',
                    'tipo_error': resultado.tipo_error or 'unknown',
                    'mensaje': resultado.error or 'Error desconocido en la descarga',
                })

        except Exception as e:
            log.error(f"[JOB {job_id[:8]}] EXCEPCIÓN en thread: {type(e).__name__}: {e}", exc_info=True)
            _jobs[job_id].update({
                'estado': 'error',
                'tipo_error': 'exception',
                'mensaje': f'Error: {type(e).__name__}',
            })

        finally:
            # Despertar cualquier request de long-polling que esté esperando este job
            if job_id in _job_events:
                log.info(f"[JOB {job_id[:8]}] Despertando long-polling")
                _job_events[job_id].set()

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
    GET:  Muestra el formulario de descarga.
    POST: Valida la solicitud, lanza el pipeline en un thread y devuelve
          un job_id de forma inmediata (HTTP 202). El cliente hace polling
          a /descargas/estado/<job_id> para saber cuándo terminó.

    Esto evita el timeout de ~60s del proxy de Render en expedientes extensos.
    """
    # GET → mostrar formulario
    if request.method == 'GET':
        sesion_mv = SesionUsuarioMV.query.filter_by(user_id=current_user.id).first()
        return render_template(
            'descargar_expediente.html',
            creditos=current_user.creditos_disponibles,
            tiene_sesion_mv=sesion_mv is not None,
            mv_usuario=sesion_mv.mv_usuario if sesion_mv else None
        )

    # POST → iniciar descarga asincrónica
    try:
        _limpiar_jobs_viejos()

        data = request.get_json() or {}
        numero_expediente = data.get('numero_expediente', '').strip()
        indice_expediente = data.get('indice_expediente')
        if indice_expediente is not None:
            indice_expediente = int(indice_expediente)

        if not numero_expediente:
            return jsonify({'exito': False, 'mensaje': 'Número de expediente requerido'}), 400

        if not current_user.is_admin and current_user.creditos_disponibles < 1:
            return jsonify({
                'exito': False,
                'tipo_error': 'creditos_insuficientes',
                'mensaje': 'Créditos insuficientes. Comprá créditos para continuar.',
            }), 402

        cookies_mv = obtener_cookies_usuario(current_user.id)
        if not cookies_mv:
            return jsonify({
                'exito': False,
                'tipo_error': 'sesion_mv_requerida',
                'mensaje': 'Necesitás conectar tu cuenta de Mesa Virtual primero.',
                'login_url': '/auth/mv-login?next=/descargas/expediente'
            }), 401

        # Registrar job y lanzar thread
        job_id = str(uuid.uuid4())
        _jobs[job_id] = {
            'estado': 'procesando',
            'user_id': current_user.id,
            'timestamp': time.time(),
        }

        app = current_app._get_current_object()
        t = threading.Thread(
            target=_run_pipeline,
            args=(app, job_id, current_user.id, numero_expediente, indice_expediente, cookies_mv),
            daemon=True
        )
        t.start()

        logger.info(f"[JOB {job_id[:8]}] Lanzado para user {current_user.id}, expediente {numero_expediente}")
        return jsonify({'job_id': job_id}), 202

    except Exception as e:
        logger.error(f"Error iniciando descarga: {e}", exc_info=True)
        return jsonify({'exito': False, 'mensaje': 'Error interno del servidor'}), 500


@descargas_bp.route('/estado/<job_id>', methods=['GET'])
@login_required
def estado_descarga(job_id):
    """
    Long-polling endpoint: el frontend hace UN request que espera hasta que
    el job complete (máx 5 minutos).

    El servidor retiene la request hasta que:
    - El job complete (devuelve el estado final)
    - Pasen 5 minutos (devuelve estado actual)
    - El job sea inválido/expirado (devuelve 404)

    Respuestas posibles:
      { estado: 'completo', pdf_url, creditos_restantes } → éxito
      { estado: 'multiples_opciones', opciones: [...] }  → pedir selección
      { estado: 'error', tipo_error, mensaje }           → mostrar error
      { estado: 'procesando' }                           → timeout (seguir esperando)
      { estado: 'no_encontrado' }                        → job inválido/expirado
    """
    job = _jobs.get(job_id)

    if not job:
        return jsonify({'estado': 'no_encontrado'}), 404

    # Sólo el dueño del job puede consultarlo
    if job.get('user_id') != current_user.id:
        return jsonify({'estado': 'no_encontrado'}), 404

    # Si el job ya terminó (no está en "procesando"), devolver inmediatamente
    if job['estado'] != 'procesando':
        return jsonify(job), 200

    # Job sigue procesando: esperar con long-polling (máx 5 minutos)
    # Crear o reutilizar el evento para este job
    event = _job_events.get(job_id)
    if not event:
        event = threading.Event()
        _job_events[job_id] = event

    # Esperar 5 minutos a que el job complete (el thread lo despierta con .set())
    logger.info(f"[LONG-POLL] Request esperando el job {job_id[:8]}")
    event.wait(timeout=300)  # 5 minutos máximo
    logger.info(f"[LONG-POLL] Request despertado o timeout para {job_id[:8]}")

    # Devolver el estado actual (puede ser completo o sigue procesando si hubo timeout)
    return jsonify(_jobs[job_id]), 200


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
    from datetime import datetime
    try:
        expedientes = ExpedienteDescargado.query.filter_by(
            user_id=current_user.id
        ).order_by(ExpedienteDescargado.creado_en.desc()).all()

        return render_template('historial_descargas.html', expedientes=expedientes, now=datetime.now())

    except Exception as e:
        logger.error(f"Error al mostrar historial: {str(e)}")
        return render_template('error.html', mensaje='Error al cargar el historial'), 500
