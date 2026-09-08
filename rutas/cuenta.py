"""
Rutas de "Mi cuenta"
=====================

Autogestión básica de la cuenta: nombre, contraseña y preferencia de aviso
por email al terminar una descarga. Sigue el mismo patrón JSON que
rutas/auth.py (formularios servidos por GET, POST con JSON), con la
protección CSRF activa: el template ya manda X-CSRFToken en cada fetch.

Fuera de alcance (a pedido explícito): cambio de email, borrado de cuenta,
gestión de sesiones activas — ver el plan del lote 2.
"""

import logging
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user

from modulos.auth import validar_password
from modulos.database import db
from modulos.models import SesionUsuarioMV
from modulos.extensions import limiter

logger = logging.getLogger(__name__)

cuenta_bp = Blueprint('cuenta', __name__, url_prefix='/cuenta')


@cuenta_bp.route('/', methods=['GET'])
@login_required
def mi_cuenta():
    """Muestra la página de Mi cuenta."""
    sesion_mv = SesionUsuarioMV.query.filter_by(user_id=current_user.id).first()
    return render_template(
        'cuenta.html',
        sesion_mv=sesion_mv,
    )


@cuenta_bp.route('/nombre', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def cambiar_nombre():
    """
    Body JSON: { "nombre": "Juan Pérez" }
    """
    datos = request.get_json() or {}
    nombre = datos.get('nombre', '').strip()

    if len(nombre) < 2 or len(nombre) > 120:
        return jsonify({'exito': False, 'mensaje': 'El nombre debe tener entre 2 y 120 caracteres'}), 400

    current_user.nombre = nombre
    db.session.commit()

    logger.info(f"Usuario {current_user.id} actualizó su nombre")
    return jsonify({'exito': True, 'nombre': nombre}), 200


@cuenta_bp.route('/password', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def cambiar_password():
    """
    Body JSON: { "actual": "...", "nueva": "..." }
    """
    datos = request.get_json() or {}
    actual = datos.get('actual', '')
    nueva = datos.get('nueva', '')

    if not actual or not nueva:
        return jsonify({'exito': False, 'mensaje': 'Contraseña actual y nueva son requeridas'}), 400

    if not current_user.verificar_password(actual):
        return jsonify({'exito': False, 'mensaje': 'La contraseña actual es incorrecta'}), 400

    valida, error = validar_password(nueva)
    if not valida:
        return jsonify({'exito': False, 'mensaje': error}), 400

    current_user.establecer_password(nueva)
    db.session.commit()

    logger.info(f"Usuario {current_user.id} cambió su contraseña")
    return jsonify({'exito': True, 'mensaje': 'Contraseña actualizada correctamente'}), 200


@cuenta_bp.route('/notificaciones', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def cambiar_notificaciones():
    """
    Body JSON: { "notificar_email": true|false }
    """
    datos = request.get_json() or {}
    if 'notificar_email' not in datos:
        return jsonify({'exito': False, 'mensaje': 'Falta notificar_email'}), 400

    current_user.notificar_email = bool(datos.get('notificar_email'))
    db.session.commit()

    return jsonify({'exito': True, 'notificar_email': current_user.notificar_email}), 200
