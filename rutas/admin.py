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
from modulos.models import User

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
    return render_template('admin_panel.html', usuarios=usuarios)


@admin_bp.route('/otorgar-creditos', methods=['POST'])
@login_required
@requiere_admin
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
