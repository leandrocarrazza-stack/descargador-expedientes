# rutas/admin.py
"""
Panel de administración.
Solo accesible para usuarios con is_admin=True.

Funciones:
- Ver todos los usuarios y sus créditos
- Otorgar créditos gratuitos a cualquier cuenta
"""

import logging
from functools import wraps
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user

from modulos.database import db
from modulos.models import User, MensajeContacto, DiaInhabil, CalendarioSyncLog
from modulos.extensions import csrf, limiter

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def requiere_admin(f):
    """Decorador: rechaza el acceso si el usuario no es admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return render_template('error.html', mensaje='Acceso denegado'), 403
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/', methods=['GET'])
@login_required
@requiere_admin
def panel():
    """Panel principal: lista de usuarios con sus créditos."""
    usuarios = User.query.order_by(User.creado_en.desc()).all()
    mensajes = MensajeContacto.query.order_by(MensajeContacto.creado_en.desc()).all()
    no_leidos = sum(1 for m in mensajes if not m.leido)
    return render_template('admin_panel.html', usuarios=usuarios, mensajes=mensajes, no_leidos=no_leidos)


@admin_bp.route('/mensajes/<int:mensaje_id>/leido', methods=['POST'])
@login_required
@requiere_admin
def marcar_leido(mensaje_id):
    """Marca un mensaje de contacto como leído."""
    mensaje = MensajeContacto.query.get_or_404(mensaje_id)
    mensaje.leido = True
    db.session.commit()
    return jsonify({'exito': True})


@admin_bp.route('/mensajes/<int:mensaje_id>', methods=['DELETE'])
@login_required
@requiere_admin
def eliminar_mensaje(mensaje_id):
    """
    Borra un mensaje de contacto ya leído.

    Por defecto rechaza borrar un mensaje sin leer (evita perder algo que
    nadie vio todavía); ?forzar=1 lo permite igual.
    """
    mensaje = MensajeContacto.query.get_or_404(mensaje_id)
    if not mensaje.leido and request.args.get('forzar') != '1':
        return jsonify({'exito': False, 'mensaje': 'El mensaje no está leído todavía'}), 400

    db.session.delete(mensaje)
    db.session.commit()
    return jsonify({'exito': True})


@admin_bp.route('/mensajes/eliminar-leidos', methods=['POST'])
@login_required
@requiere_admin
def eliminar_mensajes_leidos():
    """Borra todos los mensajes de contacto ya leídos. Devuelve cuántos borró."""
    cantidad = MensajeContacto.query.filter_by(leido=True).delete()
    db.session.commit()
    return jsonify({'exito': True, 'cantidad': cantidad})


@admin_bp.route('/otorgar-creditos', methods=['POST'])
@login_required
@requiere_admin
@limiter.limit("20 per minute")
@csrf.exempt
def otorgar_creditos():
    """
    Otorga créditos gratuitos a un usuario.

    Body JSON:
        { "email": "usuario@ejemplo.com", "creditos": 5, "motivo": "compensación" }
    """
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    creditos = int(data.get('creditos', 0))
    motivo = data.get('motivo', 'otorgado por admin')

    if not email or creditos <= 0:
        return jsonify({'exito': False, 'mensaje': 'Email y cantidad de créditos requeridos'}), 400

    usuario = User.query.filter_by(email=email).first()
    if not usuario:
        return jsonify({'exito': False, 'mensaje': f'Usuario {email} no encontrado'}), 404

    usuario.creditos_disponibles += creditos
    db.session.commit()

    logger.info(f"Admin {current_user.email} otorgó {creditos} créditos a {email} ({motivo})")

    return jsonify({
        'exito': True,
        'mensaje': f'+{creditos} créditos otorgados a {email}',
        'creditos_nuevos': usuario.creditos_disponibles
    })


# ═══════════════════════════════════════════════════════════════════════════
#  CALENDARIO JUDICIAL (contador de plazos)
# ═══════════════════════════════════════════════════════════════════════════

TIPOS_DIA = {
    'feriado_nacional': 'Feriado nacional',
    'feriado_provincial': 'Feriado provincial',
    'no_laborable': 'Día no laborable',
    'inhabil_judicial': 'Inhábil judicial',
    'feria_julio': 'Feria de julio',
    'feriado_local': 'Feriado local',
    'suspension': 'Suspensión de plazos',
}


@admin_bp.route('/calendario', methods=['GET'])
@login_required
@requiere_admin
def calendario():
    """Calendario judicial: lo que cargó el sincronizador y lo cargado a mano."""
    from datetime import date
    from modulos.plazos.localidades import DEPARTAMENTOS
    from modulos.plazos.calendario import FUEROS
    from modulos.plazos.calculo import hoy_argentina
    try:
        anio = int(request.args.get('anio', hoy_argentina().year))
    except ValueError:
        anio = hoy_argentina().year
    if not 2015 <= anio <= 2100:
        anio = hoy_argentina().year
    dias = (DiaInhabil.query
            .filter(DiaInhabil.fecha_desde <= date(anio, 12, 31), DiaInhabil.fecha_hasta >= date(anio, 1, 1))
            .order_by(DiaInhabil.fecha_desde, DiaInhabil.id).all())
    logs = CalendarioSyncLog.query.order_by(CalendarioSyncLog.fecha.desc()).limit(25).all()
    return render_template('admin_calendario.html', anio=anio, dias=dias, logs=logs, tipos=TIPOS_DIA,
                           departamentos=DEPARTAMENTOS, fueros=FUEROS)


@admin_bp.route('/calendario/sincronizar', methods=['POST'])
@login_required
@requiere_admin
@limiter.limit("6 per hour")
def calendario_sincronizar():
    """Corre la sincronización con jusentrerios.gov.ar ahora mismo."""
    from flask import current_app
    from modulos.plazos import sync_stjer
    # En segundo plano: leer el sitio del STJER puede tardar más que el límite del proxy.
    # El resultado queda en "Últimas sincronizaciones" (recargá la página en un minuto).
    sync_stjer.lanzar_sincronizacion(current_app._get_current_object())
    flash('Sincronización iniciada. Mirá el resultado en "Últimas sincronizaciones" en un minuto.', 'info')
    return redirect(url_for('admin.calendario'))


@admin_bp.route('/calendario/nuevo', methods=['POST'])
@login_required
@requiere_admin
def calendario_nuevo():
    """Carga manual de un día o rango inhábil (el sincronizador no lo pisa)."""
    from datetime import datetime
    from modulos.plazos.calendario import FUEROS
    from modulos.plazos.localidades import departamento_de, departamento_oficial, localidad_oficial

    f = request.form
    try:
        desde = datetime.strptime(f.get('desde', ''), '%Y-%m-%d').date()
        hasta = datetime.strptime(f.get('hasta') or f.get('desde', ''), '%Y-%m-%d').date()
    except ValueError:
        flash('Fechas inválidas', 'error')
        return redirect(url_for('admin.calendario'))
    motivo = (f.get('motivo') or '').strip()
    tipo = f.get('tipo')
    if hasta < desde or (hasta - desde).days > 62 or not motivo or tipo not in TIPOS_DIA \
            or not 2015 <= desde.year <= 2100:
        flash('Revisá los datos: rango de hasta 62 días, motivo y tipo son obligatorios', 'error')
        return redirect(url_for('admin.calendario'))

    alcance, localidad, departamento = f.get('alcance', 'provincia'), None, None
    if alcance == 'localidad':
        localidad = localidad_oficial(f.get('localidad', ''))
        departamento = departamento_de(localidad) if localidad else None
        if not localidad:
            flash('Elegí una localidad válida', 'error')
            return redirect(url_for('admin.calendario'))
    elif alcance == 'departamento':
        departamento = departamento_oficial(f.get('departamento', ''))
        if not departamento:
            flash('Elegí un departamento válido', 'error')
            return redirect(url_for('admin.calendario'))
    else:
        alcance = 'provincia'
    fuero = f.get('fuero') or None
    if fuero and fuero not in FUEROS:
        fuero = None

    fuente_url = (f.get('fuente_url') or '').strip()[:500]
    if fuente_url and not fuente_url.lower().startswith('https://'):
        flash('El link de la fuente tiene que empezar con https://', 'error')
        return redirect(url_for('admin.calendario'))
    db.session.add(DiaInhabil(
        fecha_desde=desde, fecha_hasta=hasta, tipo=tipo, motivo=motivo[:500], alcance=alcance,
        localidad=localidad, departamento=departamento, fuero=fuero, descuenta=True, fuente='manual',
        fuente_url=fuente_url or None))
    db.session.commit()
    logger.info(f"Admin {current_user.email} cargó inhábil manual {desde}..{hasta} ({motivo})")
    flash('Día cargado', 'success')
    return redirect(url_for('admin.calendario', anio=desde.year))


@admin_bp.route('/calendario/<int:dia_id>/activo', methods=['POST'])
@login_required
@requiere_admin
def calendario_activar(dia_id):
    """Activa/desactiva un registro (útil para anular algo que cargó el sincronizador)."""
    dia = DiaInhabil.query.get_or_404(dia_id)
    dia.activo = not dia.activo
    db.session.commit()
    return redirect(url_for('admin.calendario', anio=dia.fecha_desde.year))


@admin_bp.route('/calendario/<int:dia_id>/eliminar', methods=['POST'])
@login_required
@requiere_admin
def calendario_eliminar(dia_id):
    """Borra un registro cargado a mano (los del sincronizador se desactivan, no se borran)."""
    dia = DiaInhabil.query.get_or_404(dia_id)
    anio = dia.fecha_desde.year
    if dia.fuente != 'manual':
        flash('Los registros automáticos no se borran: desactivalos.', 'error')
    else:
        db.session.delete(dia)
        db.session.commit()
    return redirect(url_for('admin.calendario', anio=anio))
