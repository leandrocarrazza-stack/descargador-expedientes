# rutas/plazos.py
"""
Contador de plazos judiciales (Poder Judicial de Entre Ríos).

- GET  /plazos/              → pantalla de la calculadora
- POST /plazos/api/calcular  → cálculo (JSON). Se hace en el servidor para
                               que el calendario (feriados, inhábiles, ferias)
                               viva en un solo lugar.

Acceso: usuarios con algún plan pago (plan_max_comprado) y admins.
"""

import logging
from datetime import date, datetime

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

import config
from modulos.extensions import limiter
from modulos.plazos.calculo import calcular, ErrorPlazo, TIPOS_NOTIFICACION, MODOS
from modulos.plazos.calendario import cargar_calendario, FUEROS
from modulos.plazos.evento import datos_calendario
from modulos.plazos.localidades import DEPARTAMENTOS, localidad_oficial

logger = logging.getLogger(__name__)

plazos_bp = Blueprint('plazos', __name__, url_prefix='/plazos')


def tiene_acceso(usuario):
    """Admins y cualquier usuario que haya comprado alguno de los planes."""
    return bool(usuario.is_admin or usuario.plan_max_comprado)


def _sin_acceso():
    return jsonify({
        'exito': False,
        'tipo_error': 'plan_requerido',
        'mensaje': 'El contador de plazos está disponible para quienes tienen un plan de Foja.',
    }), 403


MAX_BODY = 8 * 1024   # la API solo recibe unos pocos campos cortos


@plazos_bp.route('/', methods=['GET'])
@login_required
def calculadora():
    """Pantalla de la calculadora. Sin plan, muestra la invitación a comprar uno."""
    from datetime import timedelta
    from modulos.models import CalendarioSyncLog
    sync_fecha, sync_ok = None, True
    if tiene_acceso(current_user):
        # Estado de la fuente principal (la página de feriados e inhábiles): si no se pudo leer,
        # el usuario no debe creer que el calendario está al día
        ultima = (CalendarioSyncLog.query.filter_by(fuente='pagina')
                  .order_by(CalendarioSyncLog.fecha.desc()).first())
        if ultima:
            sync_fecha = ultima.fecha - timedelta(hours=3)   # hora de Argentina
            sync_ok = ultima.resultado in ('ok', 'sin_cambios')
    return render_template(
        'plazos.html',
        acceso=tiene_acceso(current_user),
        departamentos=DEPARTAMENTOS,
        fueros=FUEROS,
        tipos_notificacion=TIPOS_NOTIFICACION,
        modos=MODOS,
        sync_fecha=sync_fecha,
        sync_ok=sync_ok,
    )


def _entero(valor):
    """Entero estricto: acepta int o texto de dígitos; rechaza bool, float, listas, etc."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.strip().isdigit() and len(valor.strip()) <= 6:
        return int(valor.strip())
    return None


def _texto(valor, default=''):
    """Texto o `default`; None si el valor no es un string (listas, objetos, números)."""
    if valor is None:
        return default
    return valor if isinstance(valor, str) else None


def _clave_usuario():
    # Límite por usuario (no por IP: detrás del proxy de Render todas las IP pueden ser la misma)
    return f'plazos:{current_user.get_id()}'


@plazos_bp.route('/api/calcular', methods=['POST'])
@login_required
@limiter.limit("60 per minute", key_func=_clave_usuario)
def api_calcular():
    """
    Body JSON:
        { "tipo_notificacion": "cedula|sne|sne_urgente", "fecha": "2026-10-05",
          "dias": 10, "modo": "habiles|corridos", "localidad": "Paraná"|"",
          "fuero": "civil|laboral|..."|"", "nombre": "opcional (para el evento de calendario)" }
    """
    if not tiene_acceso(current_user):
        return _sin_acceso()

    request.max_content_length = MAX_BODY   # Werkzeug responde 413 si el cuerpo es más grande
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'exito': False, 'mensaje': 'Solicitud inválida'}), 400

    fecha_txt = _texto(data.get('fecha'))
    try:
        fecha = datetime.strptime(fecha_txt or '', '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'exito': False, 'mensaje': 'Fecha inválida'}), 400
    if not (date(2015, 1, 1) <= fecha <= date(2100, 12, 31)):
        return jsonify({'exito': False, 'mensaje': 'La fecha está fuera del rango admitido'}), 400

    dias = _entero(data.get('dias'))
    if dias is None:
        return jsonify({'exito': False, 'mensaje': 'La cantidad de días debe ser un número entero'}), 400

    localidad_txt = _texto(data.get('localidad'))
    fuero = _texto(data.get('fuero')) or None
    tipo = _texto(data.get('tipo_notificacion'), 'cedula')
    modo = _texto(data.get('modo'), 'habiles')
    nombre = _texto(data.get('nombre'))
    if None in (localidad_txt, tipo, modo, nombre) or (fuero is None and data.get('fuero')):
        return jsonify({'exito': False, 'mensaje': 'Solicitud inválida'}), 400

    localidad = None
    if localidad_txt:
        localidad = localidad_oficial(localidad_txt)
        if not localidad:
            return jsonify({'exito': False, 'mensaje': 'Localidad desconocida'}), 400
    if fuero and fuero not in FUEROS:
        return jsonify({'exito': False, 'mensaje': 'Fuero inválido'}), 400

    try:
        resultado = calcular(
            cargar_calendario(),
            tipo_notificacion=tipo, fecha=fecha, dias=dias, modo=modo,
            localidad=localidad, fuero=fuero, hora_apertura=config.PLAZOS_HORA_APERTURA,
        )
    except ErrorPlazo as e:
        return jsonify({'exito': False, 'mensaje': str(e)}), 400

    # Evento para el calendario personal (Google Calendar / archivo .ics)
    datos = datos_calendario(
        resultado, nombre=nombre[:200], localidad=localidad,
        fuero_texto=FUEROS.get(fuero), url_app=f'{config.BASE_URL.rstrip("/")}/plazos/')
    return jsonify({'exito': True, 'resultado': resultado.a_dict(), 'calendario': datos})
