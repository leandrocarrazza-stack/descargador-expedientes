# rutas/descargas.py
"""
Rutas para descarga de expedientes (con polling asincrónico).

Arquitectura: el pipeline corre en un thread background.
- POST /descargas/expediente  → valida, lanza thread, devuelve job_id (respuesta inmediata)
- GET  /descargas/estado/<id> → long-poll corto: cada request espera hasta ~25s en el
  servidor por una respuesta, y si el job sigue en curso el frontend vuelve a pedir
  de inmediato (ver estado_descarga()).

Esto evita el timeout de ~60s del proxy de Render en expedientes extensos: ese límite
es el que manda sobre cualquier request individual (no el timeout de gunicorn, que es
mucho más laxo), así que ningún GET a /estado puede acercarse a los 60s aunque el job
completo tarde varios minutos.

Modelo: Cada descarga cuesta 1 crédito prepagado.

CONCURRENCIA: sólo corre una descarga a la vez en toda la instancia
(_semaforo_descarga) — dos Chrome + LibreOffice al mismo tiempo pueden
agotar los 512 MB de RAM del plan Starter y tumbar la app entera. Una
segunda solicitud mientras hay una en curso recibe 409 de inmediato.

LIMPIEZA: El PDF final se borra del servidor después de que el usuario
lo descarga. Además, al iniciar la app se borran PDFs con más de
PDF_TTL_HOURS horas de antigüedad.
"""

import csv
import io
import logging
import os
import time
import threading
import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, render_template, current_app, Response, redirect, url_for
from flask_login import login_required, current_user

from modulos.pipeline import PipelineDescargador
from modulos.database import db
from modulos.models import ExpedienteDescargado, SesionUsuarioMV
from modulos.auth_mv import obtener_cookies_usuario
from modulos.extensions import csrf
import config

# ── Jobs en memoria ───────────────────────────────────────────────────────────
# Guarda el estado de cada descarga en curso.
# Como Gunicorn corre con 1 worker, este dict es compartido por todos los requests.
# Estructura: { job_id: { estado, user_id, timestamp, ... } }
_jobs: dict = {}
_job_events: dict = {}  # { job_id: threading.Event() } para long-polling
JOB_TTL_SEGUNDOS = 600  # 10 minutos: tiempo máximo que vive en memoria un job YA TERMINADO
# Techo de seguridad para jobs que quedaron en 'procesando' (thread colgado/crasheado
# sin pasar por su finally). Expedientes con cientos de movimientos pueden tardar
# bastante más que JOB_TTL_SEGUNDOS en terminar de forma legítima: si se los borra
# por antigüedad mientras siguen corriendo, el long-poll que los está esperando
# revienta con KeyError -> 500 HTML -> "Unexpected token '<'" en el frontend.
JOB_TTL_PROCESANDO_SEGUNDOS = 3600  # 1 hora

# Límite de descargas simultáneas. Cada descarga abre su propio Chrome +
# LibreOffice; el plan Starter de Render tiene 512 MB de RAM en total, así
# que dos pipelines corriendo a la vez (dos usuarios, o el mismo usuario dos
# veces) son un candidato real a agotar la memoria y tumbar toda la instancia
# — no solo la descarga que se queda sin RAM, sino la app entera para todos
# los usuarios. _run_pipeline() libera el permiso en su finally, así que
# queda tomado durante todo el pipeline (Chrome + conversión + unificación),
# no solo mientras arranca.
MAX_DESCARGAS_SIMULTANEAS = 1
_semaforo_descarga = threading.Semaphore(MAX_DESCARGAS_SIMULTANEAS)


def _limpiar_jobs_viejos():
    """
    Elimina jobs viejos para no acumular memoria indefinidamente.

    Un job 'procesando' NO se borra por antigüedad salvo que supere el techo
    de seguridad JOB_TTL_PROCESANDO_SEGUNDOS (thread realmente colgado): borrar
    la entrada de un job que sigue corriendo tira abajo su long-polling con un
    KeyError en cuanto el thread (o el propio /estado) intente leerla.
    """
    ahora = time.time()
    ids_viejos = [
        jid for jid, j in list(_jobs.items())
        if ahora - j.get('timestamp', 0) > (
            JOB_TTL_PROCESANDO_SEGUNDOS if j.get('estado') == 'procesando' else JOB_TTL_SEGUNDOS
        )
    ]
    for jid in ids_viejos:
        _jobs.pop(jid, None)
        _job_events.pop(jid, None)


def _actualizar_job(job_id, cambios):
    """
    Actualiza el estado en memoria de un job, si todavía existe.

    Usar esto en vez de `_jobs[job_id].update(...)` directo: si la entrada
    ya no está (limpieza por TTL, reinicio del proceso), evita un KeyError
    sin capturar dentro del thread de `_run_pipeline` (que además dejaría
    sin despertar al long-polling que sigue esperando ese job).
    """
    job = _jobs.get(job_id)
    if job is not None:
        job.update(cambios)


def _guardar_intento_fallido(user_id, numero_expediente, mensaje):
    """
    Registra en BD un intento de descarga que terminó en error.

    Antes esto sólo quedaba en el dict en memoria _jobs, que se borra a los
    10 minutos (JOB_TTL_SEGUNDOS) o al reiniciarse el proceso: una descarga
    fallida no dejaba ningún rastro. Ahora también queda una fila en
    ExpedienteDescargado (estado='failed'), visible en el Historial del
    usuario igual que una descarga completada.
    """
    try:
        db.session.add(ExpedienteDescargado(
            user_id=user_id,
            numero=numero_expediente,
            estado='failed',
            error_msg=mensaje,
        ))
        db.session.commit()
    except Exception:
        logging.getLogger(__name__).error(
            f"No se pudo guardar el intento fallido de '{numero_expediente}' en el historial",
            exc_info=True
        )
        db.session.rollback()


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

            def _publicar_progreso(datos: dict):
                """
                Publica el avance real del pipeline para que el frontend muestre
                "archivo N de TOTAL".

                Corre SOLO en este thread. Reasigna un dict nuevo y completo a la
                clave 'progreso' (que ya existe desde que se creó el job) en vez
                de mutar el publicado: reasignar una clave existente no cambia el
                tamaño del dict, así que un request que esté serializando el job
                en paralelo nunca ve un estado a medias ni revienta con
                "dictionary changed size during iteration".
                """
                datos['actualizado'] = time.time()
                _actualizar_job(job_id, {'progreso': datos})

            pipeline = PipelineDescargador()
            log.info(f"[JOB {job_id[:8]}] Pipeline creado, llamando a ejecutar()...")

            resultado = pipeline.ejecutar(
                numero_expediente=numero_expediente,
                limpiar_temp=config.LIMPIAR_TEMP,
                indice_expediente=indice_expediente,
                cookies_mv=cookies_mv,
                on_progreso=_publicar_progreso
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
                    user.creditos_usados_mes += 1
                db.session.commit()

                creditos_restantes = user.creditos_disponibles if user else 0
                log.info(
                    f"[JOB {job_id[:8]}] Descarga OK: {numero_expediente}, créditos restantes: {creditos_restantes}"
                )
                _actualizar_job(job_id, {
                    'estado': 'completo',
                    'expediente_id': expediente_db.id,
                    'pdf_url': f'/descargas/expediente/{expediente_db.id}/descargar',
                    'creditos_restantes': creditos_restantes,
                })

            elif resultado.tipo_error == 'multiples_opciones':
                log.info(f"[JOB {job_id[:8]}] Múltiples opciones encontradas")
                _actualizar_job(job_id, {
                    'estado': 'multiples_opciones',
                    'opciones': resultado.opciones,
                })

            elif resultado.tipo_error == 'auth_failed':
                log.warning(f"[JOB {job_id[:8]}] Sesión MV expirada")
                mensaje = 'Tu sesión de Mesa Virtual expiró. Reconectá tu cuenta.'
                _guardar_intento_fallido(user_id, numero_expediente, mensaje)
                _actualizar_job(job_id, {
                    'estado': 'error',
                    'tipo_error': 'sesion_mv_requerida',
                    'mensaje': mensaje,
                    'login_url': '/auth/mv-login?next=/descargas/expediente',
                })

            else:
                log.error(f"[JOB {job_id[:8]}] Error en pipeline: {resultado.error}")
                mensaje = resultado.error or 'Error desconocido en la descarga'
                _guardar_intento_fallido(user_id, numero_expediente, mensaje)
                _actualizar_job(job_id, {
                    'estado': 'error',
                    'tipo_error': resultado.tipo_error or 'unknown',
                    'mensaje': mensaje,
                })

        except Exception as e:
            log.error(f"[JOB {job_id[:8]}] EXCEPCIÓN en thread: {type(e).__name__}: {e}", exc_info=True)
            mensaje = f'Error: {type(e).__name__}'
            _guardar_intento_fallido(user_id, numero_expediente, mensaje)
            _actualizar_job(job_id, {
                'estado': 'error',
                'tipo_error': 'exception',
                'mensaje': mensaje,
            })

        finally:
            # Liberar el permiso de concurrencia SIEMPRE, sea cual sea el resultado,
            # para que la próxima descarga en espera pueda arrancar.
            _semaforo_descarga.release()

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
@csrf.exempt
def descargar_expediente_sync():
    """
    GET:  Muestra el formulario de descarga.
    POST: Valida la solicitud, lanza el pipeline en un thread y devuelve
          un job_id de forma inmediata (HTTP 202). El cliente hace polling
          a /descargas/estado/<job_id> para saber cuándo terminó.

    Esto evita el timeout de ~60s del proxy de Render en expedientes extensos.
    """
    # GET → mostrar formulario (o redirigir a login MV si no hay sesión)
    if request.method == 'GET':
        sesion_mv = SesionUsuarioMV.query.filter_by(user_id=current_user.id).first()
        if not sesion_mv:
            return redirect(url_for('auth.mv_login') + '?next=' + url_for('descargas.descargar_expediente_sync'))
        return render_template(
            'descargar_expediente.html',
            creditos=current_user.creditos_disponibles,
            tiene_sesion_mv=True,
            mv_usuario=sesion_mv.mv_usuario
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

        # Límite de concurrencia: si ya hay una descarga corriendo, rechazar
        # rápido en vez de arrancar un segundo Chrome que puede tumbar la
        # instancia entera (ver comentario junto a _semaforo_descarga).
        if not _semaforo_descarga.acquire(blocking=False):
            logger.warning(f"Descarga rechazada por concurrencia: user {current_user.id}, expediente {numero_expediente}")
            return jsonify({
                'exito': False,
                'tipo_error': 'descarga_en_curso',
                'mensaje': 'Ya hay otra descarga en curso en el servidor. Esperá un momento e intentá de nuevo.',
            }), 409

        # Registrar job y lanzar thread
        job_id = str(uuid.uuid4())
        _jobs[job_id] = {
            'estado': 'procesando',
            'user_id': current_user.id,
            'timestamp': time.time(),
            # Se pre-siembra la clave (no es cosmético): estado_descarga() hace
            # jsonify(job), que ITERA este dict. Si el thread del pipeline
            # insertara 'progreso' por primera vez justo durante esa iteración,
            # CPython tiraría "dictionary changed size during iteration" -> 500
            # en HTML -> el "Unexpected token '<'" del frontend. Creándola acá,
            # el conjunto de claves nunca cambia: sólo se reasigna su valor.
            'progreso': {'fase': 'iniciando', 'actual': 0, 'total': None, 'total_exacto': False},
        }

        app = current_app._get_current_object()
        try:
            t = threading.Thread(
                target=_run_pipeline,
                args=(app, job_id, current_user.id, numero_expediente, indice_expediente, cookies_mv),
                daemon=True
            )
            t.start()
        except Exception:
            # El thread nunca arrancó, así que _run_pipeline no va a liberar
            # el permiso en su finally: hay que soltarlo acá para no dejar
            # la concurrencia bloqueada para siempre.
            _semaforo_descarga.release()
            raise

        logger.info(f"[JOB {job_id[:8]}] Lanzado para user {current_user.id}, expediente {numero_expediente}")
        return jsonify({'job_id': job_id}), 202

    except Exception as e:
        logger.error(f"Error iniciando descarga: {e}", exc_info=True)
        return jsonify({'exito': False, 'mensaje': 'Error interno del servidor'}), 500


@descargas_bp.route('/estado/<job_id>', methods=['GET'])
@login_required
def estado_descarga(job_id):
    """
    Long-polling endpoint: cada request espera hasta ~25s a que el job
    complete; si no llegó a completar, devuelve el estado actual y el
    frontend vuelve a pedir de inmediato (ver longPolling() en el script de
    templates/descargar_expediente.html). Para un job de varios minutos esto es
    una cadena de varios requests cortos, no uno solo sostenido.

    El avance archivo-por-archivo NO viaja por acá: va por /descargas/progreso,
    que responde en el acto (ver progreso_descarga()).

    Por qué 25s y no más: el proxy de Render corta cualquier request de
    más de ~60s (fue la causa original del Error 502 en expedientes
    extensos, ver .planning/STATE.md). 25s deja margen de sobra bajo ese
    límite aunque haya latencia de red o el servidor tarde un poco en
    responder. El timeout de gunicorn (330s, ver Dockerfile) no es la
    referencia acá: el proxy corta mucho antes de llegar a eso.

    El servidor retiene la request hasta que:
    - El job complete (devuelve el estado final)
    - Pase el timeout de 25s (devuelve estado actual, el frontend vuelve a pedir)
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

    # Job sigue procesando: esperar con long-polling corto (máx ~25s)
    # Crear o reutilizar el evento para este job
    event = _job_events.get(job_id)
    if not event:
        event = threading.Event()
        _job_events[job_id] = event

    # Esperar a que el job complete (el thread lo despierta con .set()).
    # IMPORTANTE: este timeout debe quedar por debajo del límite real del
    # proxy de Render (~60s) para que ESTE request responda por sí mismo
    # antes de que el proxy lo corte. El frontend vuelve a pedir de
    # inmediato si el job sigue en curso, así que un job largo se resuelve
    # con varios requests cortos en vez de uno sostenido cerca del límite.
    logger.info(f"[LONG-POLL] Request esperando el job {job_id[:8]}")
    event.wait(timeout=25)
    logger.info(f"[LONG-POLL] Request despertado o timeout para {job_id[:8]}")

    # Devolver el estado actual (puede ser completo o sigue procesando si hubo timeout).
    # Se relee con .get() en vez de indexar directo: si el job se limpió mientras
    # esta request esperaba (proceso reiniciado, TTL de seguridad, etc.), evita un
    # KeyError sin capturar que el errorhandler 500 global convertiría en HTML
    # ("Unexpected token '<'" en el frontend) en vez de la respuesta JSON esperada.
    job_actual = _jobs.get(job_id)
    if not job_actual:
        return jsonify({'estado': 'no_encontrado'}), 404
    return jsonify(job_actual), 200


@descargas_bp.route('/progreso/<job_id>', methods=['GET'])
@login_required
def progreso_descarga(job_id):
    """
    Progreso REAL de la descarga: cuántos archivos tiene el expediente y cuántos
    van bajados hasta ahora.

    A diferencia de /estado, este endpoint NO hace long-polling: responde en el
    acto con lo último que publicó el thread del pipeline. Es una lectura de un
    dict en memoria, así que el frontend lo puede consultar cada 2 segundos sin
    costo mientras el long-poll de /estado sigue esperando el resultado final.

    Por qué un endpoint aparte y no despertar el long-poll: /estado espera sobre
    un único threading.Event por job que se hace set() exactamente una vez, al
    terminar el pipeline. Reusarlo para progreso obligaría a set()/clear() por
    archivo, y un request que caiga en esa ventana podría perderse el aviso
    FINAL y quedarse 25s colgado después de que el job ya terminó. El progreso
    es lossy por diseño (sólo importa el último valor); el fin del job tiene que
    entregarse exactamente una vez. No van por el mismo canal.

    Respuestas:
      { estado, progreso: {fase, actual, total, total_exacto, ...} }  → 200
      { estado: 'no_encontrado' }                                    → 404
    """
    job = _jobs.get(job_id)

    # Se colapsan "no existe" y "no es tuyo" en la misma respuesta para que no
    # se puedan enumerar job_ids ajenos.
    if not job or job.get('user_id') != current_user.id:
        return jsonify({'estado': 'no_encontrado'}), 404

    # Se arma un dict chico en vez de jsonify(job): no filtra user_id ni
    # timestamp, y sobre todo NO itera el job vivo mientras el thread del
    # pipeline lo está actualizando.
    return jsonify({
        'estado': job.get('estado', 'procesando'),
        'progreso': job.get('progreso') or {},
    }), 200


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


@descargas_bp.route('/exportar-historial', methods=['GET'])
@login_required
def exportar_historial():
    """
    Exporta el historial de descargas del usuario como CSV.
    """
    try:
        expedientes = ExpedienteDescargado.query.filter_by(
            user_id=current_user.id
        ).order_by(ExpedienteDescargado.creado_en.desc()).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Número', 'Carátula', 'Tribunal', 'Fecha descarga', 'Estado', 'Error'])
        for exp in expedientes:
            writer.writerow([
                exp.numero,
                exp.caratula or '',
                exp.tribunal or '',
                exp.creado_en.strftime('%d/%m/%Y %H:%M') if exp.creado_en else '',
                exp.estado,
                exp.error_msg or '',
            ])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=historial_descargas.csv'},
        )

    except Exception as e:
        logger.error(f"Error al exportar historial: {str(e)}")
        return jsonify({'error': 'Error al exportar el historial'}), 500
