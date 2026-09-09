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

CONCURRENCIA: las descargas se atienden en una cola FIFO (modulos/concurrencia.py)
en vez de rechazar con 409 a la segunda solicitud. Dos Chrome + LibreOffice al
mismo tiempo pueden agotar los 512 MB de RAM del plan Starter y tumbar la app
entera, así que la cola sigue limitando cuántos navegadores/conversiones
corren de verdad en simultáneo — pero ahora nadie es rechazado de entrada:
espera su turno y ve su posición ("Hay 2 descargas adelante"). Recién si la
cola misma se llena (MAX_COLA_DESCARGAS) se devuelve 409.

LIMPIEZA: El PDF final ya no se borra al descargarlo — queda en output/
como caché (con TTL de PDF_TTL_HOURS) y persiste en el storage
configurado (modulos/storage.py, R2 o local) hasta RETENCION_PDF_DIAS
días desde el último acceso, para que el historial y el enlace del
email de aviso sigan funcionando más allá de esa ventana.
"""

import csv
import io
import json
import logging
import os
import time
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from flask import Blueprint, request, jsonify, send_file, render_template, current_app, Response, redirect, url_for
from flask_login import login_required, current_user

from modulos.pipeline import PipelineDescargador
from modulos.database import db
from modulos.models import ExpedienteDescargado, SesionUsuarioMV
from modulos.auth_mv import obtener_cookies_usuario, invalidar_sesion_usuario
from modulos.extensions import csrf
from modulos.concurrencia import gestor, ErrorColaLlena, ErrorColaTimeout, ErrorCancelado
from modulos.storage import storage_pdf, key_pdf_usuario
import config

# ── Jobs en memoria ───────────────────────────────────────────────────────────
# Guarda el estado de cada descarga en curso.
# Como Gunicorn corre con 1 worker, este dict es compartido por todos los requests.
# Estructura: { job_id: { estado, user_id, timestamp, ... } }
_jobs: dict = {}
_job_events: dict = {}  # { job_id: threading.Event() } para long-polling
# Protege únicamente la secuencia "chequear que no haya un job en curso para
# este (usuario, expediente) + reservar la entrada en _jobs" en los dos POST
# que lanzan jobs (descargar_expediente_sync, actualizar_expediente). Sin
# esto esas dos operaciones no son atómicas — con gthread (6 threads, 1
# worker) dos POST casi simultáneos del mismo (usuario, expediente) pueden
# pasar ambos el chequeo antes de que cualquiera se registre, lanzando dos
# pipelines independientes para el mismo expediente.
_jobs_lock = threading.Lock()
JOB_TTL_SEGUNDOS = 600  # 10 minutos: tiempo máximo que vive en memoria un job YA TERMINADO
# Techo de seguridad para jobs que quedaron en 'procesando' (thread colgado/crasheado
# sin pasar por su finally). Expedientes con cientos de movimientos pueden tardar
# bastante más que JOB_TTL_SEGUNDOS en terminar de forma legítima: si se los borra
# por antigüedad mientras siguen corriendo, el long-poll que los está esperando
# revienta con KeyError -> 500 HTML -> "Unexpected token '<'" en el frontend.
JOB_TTL_PROCESANDO_SEGUNDOS = 3600  # 1 hora

# La cola FIFO y los permisos de navegador/conversión viven en
# modulos/concurrencia.py (singleton `gestor`, compartido por ser gunicorn
# de 1 solo worker — ver Dockerfile). Acá sólo se usa: encolar() en el POST,
# esperar_turno() dentro del thread del pipeline, y abandonar() si el
# thread nunca llega a arrancar.


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


def _hay_job_en_curso(user_id, numero_expediente):
    """
    True si ya hay un job 'procesando' de este usuario para este expediente
    (descarga completa o actualización incremental, no importa cuál).

    Evita que una descarga completa y una actualización incremental del
    MISMO expediente corran en simultáneo: además de duplicar contenido en
    el merge, la lógica de "un solo storage_key por expediente" (purgar el
    anterior tras subir el nuevo, ver _run_pipeline) asume que solo un job
    a la vez puede estar creando/reemplazando el registro más reciente —
    dos en simultáneo podrían purgarse el storage_key el uno al otro.
    """
    return any(
        j.get('estado') == 'procesando' and j.get('user_id') == user_id and j.get('numero') == numero_expediente
        for j in _jobs.values()
    )


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


def _run_pipeline(app, job_id, user_id, numero_expediente, indice_expediente, cookies_mv, entrada, notificar_email=False,
                   modo_actualizacion=None, actualizado_desde_id=None):
    """
    Ejecuta el pipeline completo en un thread separado.
    Necesita el objeto 'app' para poder usar el contexto de Flask (BD, config, etc.)
    fuera del hilo principal.

    `entrada` es la EntradaCola devuelta por gestor.encolar() en el POST: este
    thread espera su turno acá adentro (no bloquea el request que lo lanzó,
    que ya respondió 202 con el job_id).

    `notificar_email`: si el usuario activó el aviso por email (Mi cuenta o
    el checkbox del formulario), se manda un email al terminar el job, sea
    éxito o error (menos en 'multiples_opciones', que no es un estado
    terminal: el usuario todavía tiene que elegir una opción; ni en
    'sin_novedades', que no es realmente un error).

    `modo_actualizacion`/`actualizado_desde_id`: presentes sólo cuando este
    job viene de POST /descargas/expediente/<id>/actualizar (actualización
    incremental, ver PipelineDescargador.ejecutar). `modo_actualizacion` se
    pasa tal cual al pipeline; `actualizado_desde_id` es el id del
    ExpedienteDescargado que se está actualizando, para dejar registrada la
    cadena de actualizaciones en el nuevo registro.
    """
    log = logging.getLogger(__name__)
    control = None

    def _avisar_error(mensaje):
        """Envía el email de error si corresponde. No rompe el job si falla."""
        if not notificar_email:
            return
        from modulos.models import User
        from modulos.emails import enviar_email_error
        user = User.query.get(user_id)
        if user:
            enviar_email_error(user, numero_expediente, mensaje)

    with app.app_context():
        try:
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

            def _debe_cancelar():
                job = _jobs.get(job_id)
                return bool(job and job.get('cancelado'))

            log.info(f"[JOB {job_id[:8]}] En cola para expediente {numero_expediente}")
            try:
                control = gestor.esperar_turno(
                    entrada,
                    on_posicion=lambda puesto: _publicar_progreso({
                        'fase': 'en_cola', 'puesto': puesto,
                        'actual': 0, 'total': None, 'total_exacto': False,
                    }),
                    debe_cancelar=_debe_cancelar,
                )
            except ErrorColaTimeout:
                log.warning(f"[JOB {job_id[:8]}] Timeout esperando turno en la cola")
                mensaje = 'El servidor estuvo saturado demasiado tiempo. Probá de nuevo en unos minutos.'
                _guardar_intento_fallido(user_id, numero_expediente, mensaje)
                _actualizar_job(job_id, {
                    'estado': 'error',
                    'tipo_error': 'timeout_cola',
                    'mensaje': mensaje,
                })
                _avisar_error(mensaje)
                return
            except ErrorCancelado:
                log.info(f"[JOB {job_id[:8]}] Cancelado por el usuario en cola")
                _actualizar_job(job_id, {
                    'estado': 'cancelado',
                    'tipo_error': 'cancelado',
                    'mensaje': 'Descarga cancelada. No se descontó ningún crédito.',
                })
                return

            log.info(f"[JOB {job_id[:8]}] INICIANDO pipeline para expediente {numero_expediente}")

            pipeline = PipelineDescargador()
            log.info(f"[JOB {job_id[:8]}] Pipeline creado, llamando a ejecutar()...")

            resultado = pipeline.ejecutar(
                numero_expediente=numero_expediente,
                limpiar_temp=config.LIMPIAR_TEMP,
                indice_expediente=indice_expediente,
                cookies_mv=cookies_mv,
                on_progreso=_publicar_progreso,
                control=control,
                modo_actualizacion=modo_actualizacion,
                debe_cancelar=_debe_cancelar,
            )

            log.info(f"[JOB {job_id[:8]}] Pipeline completó con exito={resultado.exito}, error={resultado.tipo_error}")

            if resultado.exito:
                # Guardar en BD y descontar crédito
                log.info(f"[JOB {job_id[:8]}] Guardando en BD...")
                from modulos.models import User
                user = User.query.get(user_id)

                tribunal = resultado.expediente.get('tribunal') if resultado.expediente else None

                # total_archivos es acumulativo en una actualización incremental
                # (archivos de la descarga anterior + los nuevos), no sólo lo
                # bajado en ESTE job — así "Archivos" en el historial siempre
                # refleja el total real del PDF combinado.
                total_archivos = resultado.archivos_descargados
                if actualizado_desde_id:
                    previo = ExpedienteDescargado.query.get(actualizado_desde_id)
                    if previo and previo.total_archivos:
                        total_archivos += previo.total_archivos

                expediente_db = ExpedienteDescargado(
                    user_id=user_id,
                    numero=numero_expediente,
                    caratula=resultado.expediente.get('caratula') if resultado.expediente else None,
                    tribunal=tribunal,
                    pdf_ruta_temporal=str(resultado.pdf_final) if resultado.pdf_final else None,
                    estado='completed',
                    error_msg=None,
                    ultimo_acceso_en=datetime.utcnow(),
                    total_filas=resultado.total_filas,
                    total_archivos=total_archivos,
                    huellas_json=json.dumps(resultado.huellas) if resultado.huellas else None,
                    mv_expediente_href=(resultado.expediente.get('url') or None) if resultado.expediente else None,
                    es_actualizacion=bool(modo_actualizacion),
                    parcial=resultado.parcial,
                    actualizado_desde_id=actualizado_desde_id,
                )

                # Subir el PDF al storage persistente (R2 o local, ver
                # modulos/storage.py) para que el historial y el enlace del
                # email de aviso (lote 3) sigan funcionando después de que
                # output/ lo borre por TTL. El archivo local en output/ se
                # conserva igual como caché de la primera descarga.
                if resultado.pdf_final:
                    try:
                        key_nueva = key_pdf_usuario(user_id, numero_expediente, job_id)
                        storage_pdf().guardar(str(resultado.pdf_final), key_nueva)
                        expediente_db.storage_key = key_nueva
                    except Exception:
                        log.error(f"[JOB {job_id[:8]}] No se pudo subir el PDF al storage", exc_info=True)

                db.session.add(expediente_db)

                if user and not user.is_admin:
                    user.registrar_uso_credito(1)
                db.session.commit()

                # Un PDF por (usuario, expediente, tribunal): purgar del storage
                # la key de la descarga completa anterior del mismo expediente,
                # ahora que la nueva ya está commiteada y accesible.
                if expediente_db.storage_key:
                    anterior = ExpedienteDescargado.query.filter(
                        ExpedienteDescargado.user_id == user_id,
                        ExpedienteDescargado.numero == numero_expediente,
                        ExpedienteDescargado.tribunal == tribunal,
                        ExpedienteDescargado.estado == 'completed',
                        ExpedienteDescargado.id != expediente_db.id,
                        ExpedienteDescargado.storage_key.isnot(None),
                    ).order_by(ExpedienteDescargado.creado_en.desc()).first()
                    if anterior:
                        storage_pdf().borrar(anterior.storage_key)
                        anterior.storage_key = None
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

                if notificar_email and user:
                    from modulos.emails import enviar_email_descarga
                    enviar_email_descarga(user, expediente_db)

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
                invalidar_sesion_usuario(user_id)
                _actualizar_job(job_id, {
                    'estado': 'error',
                    'tipo_error': 'sesion_mv_requerida',
                    'mensaje': mensaje,
                    'login_url': '/auth/mv-login?next=/descargas/expediente',
                })
                _avisar_error(mensaje)

            elif resultado.tipo_error == 'cancelado':
                # Cancelado por el usuario (POST .../cancelar): no es una
                # falla, así que no deja intento fallido en el historial ni
                # manda el email de error aunque esté activado.
                log.info(f"[JOB {job_id[:8]}] Cancelado por el usuario")
                _actualizar_job(job_id, {
                    'estado': 'cancelado',
                    'tipo_error': 'cancelado',
                    'mensaje': 'Descarga cancelada. No se descontó ningún crédito.',
                })

            else:
                # 'sin_novedades' (actualización incremental sin movimientos
                # nuevos) no es realmente un error: no se cobra crédito, no
                # deja rastro de "fallo" en el historial, y no amerita un
                # email avisando que no pasó nada.
                es_sin_novedades = resultado.tipo_error == 'sin_novedades'
                nivel_log = log.info if es_sin_novedades else log.error
                nivel_log(f"[JOB {job_id[:8]}] {'Sin novedades' if es_sin_novedades else 'Error en pipeline'}: {resultado.error}")
                mensaje = resultado.error or 'Error desconocido en la descarga'
                if not es_sin_novedades:
                    _guardar_intento_fallido(user_id, numero_expediente, mensaje)
                _actualizar_job(job_id, {
                    'estado': 'error',
                    'tipo_error': resultado.tipo_error or 'unknown',
                    'mensaje': mensaje,
                })
                if not es_sin_novedades:
                    _avisar_error(mensaje)

        except Exception as e:
            log.error(f"[JOB {job_id[:8]}] EXCEPCIÓN en thread: {type(e).__name__}: {e}", exc_info=True)
            mensaje = f'Error: {type(e).__name__}'
            _guardar_intento_fallido(user_id, numero_expediente, mensaje)
            _actualizar_job(job_id, {
                'estado': 'error',
                'tipo_error': 'exception',
                'mensaje': mensaje,
            })
            _avisar_error(mensaje)

        finally:
            # Liberar los permisos de concurrencia SIEMPRE, sea cual sea el
            # resultado, para que la próxima descarga en cola pueda avanzar.
            # `control` puede ser None si el timeout de cola saltó antes de
            # ser admitido (gestor.esperar_turno ya sacó la entrada de la
            # cola por su cuenta en ese caso, ver su propio finally).
            if control is not None:
                control.liberar_todo()

            # El PDF de la descarga anterior se bajó del storage a un
            # archivo suelto en config.TEMP_DIR ANTES de que el pipeline
            # existiera (y por lo tanto antes de que tuviera su propia
            # carpeta temporal, que sí se autolimpia) — hay que borrarlo acá.
            if modo_actualizacion:
                pdf_previo = modo_actualizacion.get('pdf_previo_local')
                if pdf_previo:
                    try:
                        Path(pdf_previo).unlink(missing_ok=True)
                    except Exception:
                        pass

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


def limpiar_storage_antiguo():
    """
    Purga del storage persistente (R2 o local) los PDFs cuyo último acceso
    supera config.RETENCION_PDF_DIAS, y anula su storage_key en BD.

    A diferencia de limpiar_pdfs_antiguos() (disco efímero de output/, TTL en
    horas), esto libera el storage de larga duración que sostiene el botón
    "Descargar PDF" del historial y el enlace del email de aviso.
    """
    try:
        limite = datetime.utcnow() - timedelta(days=config.RETENCION_PDF_DIAS)
        vencidos = ExpedienteDescargado.query.filter(
            ExpedienteDescargado.storage_key.isnot(None),
            ExpedienteDescargado.ultimo_acceso_en.isnot(None),
            ExpedienteDescargado.ultimo_acceso_en < limite,
        ).all()
        for exp in vencidos:
            storage_pdf().borrar(exp.storage_key)
            exp.storage_key = None
        if vencidos:
            db.session.commit()
            logger.info(f"[CLEANUP] {len(vencidos)} PDF(s) purgados del storage por retención")
    except Exception as e:
        logger.warning(f"[CLEANUP] Error limpiando storage antiguo: {e}")
        db.session.rollback()


# Cada cuánto se repite limpiar_pdfs_antiguos() una vez arrancada la app.
INTERVALO_LIMPIEZA_PDFS_SEG = 3600  # 1 hora


def iniciar_limpieza_periodica_pdfs():
    """
    Repite limpiar_pdfs_antiguos() y limpiar_storage_antiguo() cada
    INTERVALO_LIMPIEZA_PDFS_SEG en un hilo de fondo, en vez de una sola vez
    al arrancar.

    Por qué: esta app puede seguir viva varios días sin reiniciarse (el
    último redeploy fue hace más de 5 días cuando se detectó esto). Sin
    repetición, los PDFs de más de PDF_TTL_HOURS se iban acumulando en
    output/ sin que nada los tocara hasta el próximo deploy o reinicio
    manual — disco silenciosamente lleno entre medio.
    """
    def _loop():
        while True:
            time.sleep(INTERVALO_LIMPIEZA_PDFS_SEG)
            limpiar_pdfs_antiguos()
            limpiar_storage_antiguo()

    threading.Thread(target=_loop, daemon=True).start()


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
            # Reenviar el query string original (ej. ?numero=... del link
            # "Descargar de nuevo" del historial, o ?actualizar=...): sin
            # esto se pierde en este redirect server-side, que ocurre antes
            # de que el JS de la página (localStorage) llegue a correr.
            next_url = url_for('descargas.descargar_expediente_sync')
            if request.query_string:
                next_url += '?' + request.query_string.decode('utf-8')
            return redirect(url_for('auth.mv_login') + '?' + urlencode({'next': next_url}))

        ultimas = ExpedienteDescargado.query.filter_by(
            user_id=current_user.id
        ).order_by(ExpedienteDescargado.creado_en.desc()).limit(5).all()

        return render_template(
            'descargar_expediente.html',
            creditos=current_user.creditos_disponibles,
            tiene_sesion_mv=True,
            mv_usuario=sesion_mv.mv_usuario,
            notificar_email=bool(current_user.notificar_email),
            ultimas=ultimas,
        )

    # POST → iniciar descarga asincrónica
    try:
        _limpiar_jobs_viejos()

        data = request.get_json() or {}
        numero_expediente = data.get('numero_expediente', '').strip()
        indice_expediente = data.get('indice_expediente')
        if indice_expediente is not None:
            indice_expediente = int(indice_expediente)
        notificar_email = bool(data.get('notificar_email', current_user.notificar_email))

        if not numero_expediente:
            return jsonify({'exito': False, 'mensaje': 'Número de expediente requerido'}), 400

        # Chequear-y-reservar en una sola operación atómica bajo _jobs_lock:
        # ver el comentario junto a _jobs_lock. Se pre-siembra la clave
        # 'progreso' completa (no es cosmético): estado_descarga() hace
        # jsonify(job), que ITERA este dict. Si el thread del pipeline
        # insertara 'progreso' por primera vez justo durante esa iteración,
        # CPython tiraría "dictionary changed size during iteration" -> 500
        # en HTML -> el "Unexpected token '<'" del frontend. Creándola acá,
        # el conjunto de claves nunca cambia: sólo se reasigna su valor.
        job_id = str(uuid.uuid4())
        with _jobs_lock:
            if _hay_job_en_curso(current_user.id, numero_expediente):
                return jsonify({
                    'exito': False,
                    'tipo_error': 'job_en_curso',
                    'mensaje': 'Ya hay una descarga en curso para este expediente.',
                }), 409
            _jobs[job_id] = {
                'estado': 'procesando',
                'user_id': current_user.id,
                'numero': numero_expediente,  # usado por el chequeo de job duplicado en /actualizar
                'timestamp': time.time(),
                'progreso': {'fase': 'en_cola', 'puesto': None, 'actual': 0, 'total': None, 'total_exacto': False},
            }

        if not current_user.is_admin and current_user.creditos_disponibles < 1:
            _jobs.pop(job_id, None)
            return jsonify({
                'exito': False,
                'tipo_error': 'creditos_insuficientes',
                'mensaje': 'Créditos insuficientes. Comprá créditos para continuar.',
            }), 402

        cookies_mv = obtener_cookies_usuario(current_user.id)
        if not cookies_mv:
            _jobs.pop(job_id, None)
            return jsonify({
                'exito': False,
                'tipo_error': 'sesion_mv_requerida',
                'mensaje': 'Necesitás conectar tu cuenta de Mesa Virtual primero.',
                'login_url': '/auth/mv-login?next=/descargas/expediente'
            }), 401

        # Encolar: ya no se rechaza de entrada como antes (_semaforo_descarga
        # non-blocking + 409 inmediato). Ahora se hace lugar en la cola FIFO y
        # el job espera su turno DENTRO del thread — recién si la cola misma
        # está llena (MAX_COLA_DESCARGAS) corresponde un 409.
        try:
            entrada, puesto = gestor.encolar(job_id)
        except ErrorColaLlena:
            logger.warning(f"Cola llena: user {current_user.id}, expediente {numero_expediente}")
            _jobs.pop(job_id, None)
            return jsonify({
                'exito': False,
                'tipo_error': 'cola_llena',
                'mensaje': 'Hay muchas descargas en este momento. Esperá unos minutos e intentá de nuevo.',
            }), 409
        _jobs[job_id]['progreso']['puesto'] = puesto

        app = current_app._get_current_object()
        try:
            t = threading.Thread(
                target=_run_pipeline,
                args=(app, job_id, current_user.id, numero_expediente, indice_expediente, cookies_mv, entrada),
                kwargs={'notificar_email': notificar_email},
                daemon=True
            )
            t.start()
        except Exception:
            # El thread nunca arrancó, así que _run_pipeline no va a sacarla
            # de la cola en su finally: hay que hacerlo acá para no dejar a
            # los que siguen esperando detrás de una entrada fantasma.
            gestor.abandonar(entrada)
            _jobs.pop(job_id, None)
            raise

        logger.info(f"[JOB {job_id[:8]}] Lanzado para user {current_user.id}, expediente {numero_expediente}")
        return jsonify({'job_id': job_id}), 202

    except Exception as e:
        logger.error(f"Error iniciando descarga: {e}", exc_info=True)
        return jsonify({'exito': False, 'mensaje': 'Error interno del servidor'}), 500


@descargas_bp.route('/expediente/<job_id>/cancelar', methods=['POST'])
@login_required
@csrf.exempt
def cancelar_descarga(job_id):
    """
    Marca un job propio como cancelado. El thread del pipeline lo nota en
    el próximo chequeo periódico (en cola, o dentro del loop de páginas/
    archivos — ver debe_cancelar en _run_pipeline) y aborta ahí, sin
    cobrar crédito ni mandar el email de error.
    """
    job = _jobs.get(job_id)
    if not job or job.get('user_id') != current_user.id:
        return jsonify({'exito': False, 'mensaje': 'Descarga no encontrada'}), 404

    if job.get('estado') != 'procesando':
        return jsonify({'exito': False, 'mensaje': 'Esta descarga ya terminó'}), 400

    job['cancelado'] = True
    logger.info(f"[JOB {job_id[:8]}] Cancelación solicitada por el usuario")
    return jsonify({'exito': True}), 200


@descargas_bp.route('/expediente/<int:expediente_id>/actualizar', methods=['POST'])
@login_required
@csrf.exempt
def actualizar_expediente(expediente_id):
    """
    Actualización incremental (planes Estudio/Matrícula): baja solo los
    movimientos nuevos desde la última descarga de este expediente y los
    une al PDF anterior. Mismo patrón de job asincrónico + long-poll que
    POST /descargas/expediente.

    LIMITACIÓN CONOCIDA: si el número de expediente no es único en Mesa
    Virtual, esta ruta no tiene forma de reproducir cuál de las opciones
    se eligió en la descarga original (no hay búsqueda por href todavía) —
    usa la misma selección "inteligente" por defecto que una descarga
    nueva. En la práctica el número de expediente casi siempre alcanza
    para identificarlo sin ambigüedad.
    """
    try:
        expediente = ExpedienteDescargado.query.get_or_404(expediente_id)

        if expediente.user_id != current_user.id:
            return jsonify({'exito': False, 'mensaje': 'No tenés permiso sobre este expediente'}), 403

        if expediente.estado != 'completed':
            return jsonify({'exito': False, 'mensaje': 'Esta descarga no está completa'}), 400

        if not expediente.storage_key or expediente.total_filas is None:
            return jsonify({
                'exito': False,
                'tipo_error': 'actualizacion_no_disponible',
                'mensaje': 'Esta descarga es previa a la actualización incremental. Hacé una descarga completa para habilitarla.',
            }), 400

        if not current_user.is_admin and current_user.plan_max_comprado not in ('estudio', 'matricula'):
            return jsonify({
                'exito': False,
                'tipo_error': 'plan_requerido',
                'mensaje': 'La actualización incremental requiere el plan Estudio o Matrícula.',
            }), 403

        if not current_user.is_admin and current_user.creditos_disponibles < 1:
            return jsonify({
                'exito': False,
                'tipo_error': 'creditos_insuficientes',
                'mensaje': 'Créditos insuficientes. Comprá créditos para continuar.',
            }), 402

        # Chequear-y-reservar en una sola operación atómica bajo _jobs_lock
        # (ver el comentario junto a su declaración): evita dos
        # descargas/actualizaciones simultáneas del mismo expediente, que
        # duplicarían contenido en el merge o se pisarían el storage_key
        # entre sí.
        job_id = str(uuid.uuid4())
        with _jobs_lock:
            if _hay_job_en_curso(current_user.id, expediente.numero):
                return jsonify({
                    'exito': False,
                    'tipo_error': 'job_en_curso',
                    'mensaje': 'Ya hay una descarga en curso para este expediente.',
                }), 409
            _jobs[job_id] = {
                'estado': 'procesando',
                'user_id': current_user.id,
                'numero': expediente.numero,
                'timestamp': time.time(),
                'progreso': {'fase': 'en_cola', 'puesto': None, 'actual': 0, 'total': None, 'total_exacto': False},
            }

        cookies_mv = obtener_cookies_usuario(current_user.id)
        if not cookies_mv:
            _jobs.pop(job_id, None)
            return jsonify({
                'exito': False,
                'tipo_error': 'sesion_mv_requerida',
                'mensaje': 'Necesitás conectar tu cuenta de Mesa Virtual primero.',
                'login_url': '/auth/mv-login?next=/descargas/expediente'
            }), 401

        # Bajar el PDF de la descarga anterior del storage a un archivo
        # suelto (streaming): todavía no existe la carpeta temp del pipeline,
        # que se crea recién dentro de PipelineDescargador.ejecutar(). Se
        # borra en el finally de _run_pipeline.
        pdf_previo_local = Path(config.TEMP_DIR) / f"previo_{job_id}.pdf"
        if not storage_pdf().descargar(expediente.storage_key, str(pdf_previo_local)):
            _jobs.pop(job_id, None)
            return jsonify({
                'exito': False,
                'mensaje': 'No se pudo recuperar el PDF de la descarga anterior. Hacé una descarga completa.',
            }), 500

        try:
            huellas_previas = json.loads(expediente.huellas_json) if expediente.huellas_json else []
        except (ValueError, TypeError):
            huellas_previas = []

        try:
            entrada, puesto = gestor.encolar(job_id)
        except ErrorColaLlena:
            logger.warning(f"Cola llena: user {current_user.id}, actualización {expediente.numero}")
            pdf_previo_local.unlink(missing_ok=True)
            _jobs.pop(job_id, None)
            return jsonify({
                'exito': False,
                'tipo_error': 'cola_llena',
                'mensaje': 'Hay muchas descargas en este momento. Esperá unos minutos e intentá de nuevo.',
            }), 409
        _jobs[job_id]['progreso']['puesto'] = puesto

        notificar_email = bool((request.get_json(silent=True) or {}).get('notificar_email', current_user.notificar_email))

        app = current_app._get_current_object()
        modo_actualizacion = {
            'total_filas_previo': expediente.total_filas,
            'huellas_previas': huellas_previas,
            'pdf_previo_local': str(pdf_previo_local),
        }
        try:
            t = threading.Thread(
                target=_run_pipeline,
                args=(app, job_id, current_user.id, expediente.numero, None, cookies_mv, entrada),
                kwargs={
                    'notificar_email': notificar_email,
                    'modo_actualizacion': modo_actualizacion,
                    'actualizado_desde_id': expediente.id,
                },
                daemon=True
            )
            t.start()
        except Exception:
            gestor.abandonar(entrada)
            _jobs.pop(job_id, None)
            raise

        logger.info(f"[JOB {job_id[:8]}] Actualización incremental lanzada para user {current_user.id}, expediente {expediente.numero}")
        return jsonify({'job_id': job_id}), 202

    except Exception as e:
        logger.error(f"Error iniciando actualización: {e}", exc_info=True)
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

        pdf_path = expediente.pdf_ruta_temporal

        # El archivo local en output/ se borra por TTL (limpiar_pdfs_antiguos);
        # si ya no está pero hay una copia en el storage persistente, se trae
        # de vuelta a output/ antes de servirla. Se reusa la ruta determinística
        # `recuperado_<id>.pdf` si ya se había recuperado antes (evita pegarle
        # al storage de nuevo en cada descarga repetida del mismo expediente).
        if not pdf_path or not os.path.exists(pdf_path):
            pdf_path = None
            if expediente.storage_key:
                candidato = str(config.OUTPUT_DIR / f"recuperado_{expediente.id}.pdf")
                if os.path.exists(candidato) or storage_pdf().descargar(expediente.storage_key, candidato):
                    pdf_path = candidato

        if not pdf_path or not os.path.exists(pdf_path):
            logger.error(f"PDF no encontrado: expediente {expediente_id}")
            return render_template('error.html', mensaje='Archivo PDF no encontrado'), 404

        logger.info(f"Descargando PDF: Usuario {current_user.id}, Expediente {expediente.numero}")

        expediente.pdf_ruta_temporal = pdf_path
        expediente.ultimo_acceso_en = datetime.utcnow()
        db.session.commit()

        # El PDF YA NO se borra tras servirlo (antes: _borrar_diferido a los
        # 10s). Queda en output/ como caché hasta su TTL normal, y en el
        # storage persistente para futuras descargas/actualizaciones.
        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=f"Expediente_{expediente.numero.replace('/', '_')}.pdf"
        )

    except Exception as e:
        logger.error(f"Error al descargar PDF {expediente_id}: {str(e)}", exc_info=True)
        return render_template('error.html', mensaje='Error al descargar el archivo'), 500


@descargas_bp.route('/expediente/<int:expediente_id>/listo', methods=['GET'])
@login_required
def descarga_lista(expediente_id):
    """
    Página de confirmación a la que apunta el link del email de aviso: da
    contexto (qué expediente, cuándo) antes de bajar el PDF, en vez de
    disparar la descarga directo (silenciosa y sin contexto).
    """
    expediente = ExpedienteDescargado.query.get(expediente_id)

    if not expediente:
        return render_template('error.html', mensaje='Expediente no encontrado'), 404

    if expediente.user_id != current_user.id:
        return render_template('error.html', mensaje='No tenés permiso para ver este expediente'), 403

    return render_template('descarga_lista.html', expediente=expediente)


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
