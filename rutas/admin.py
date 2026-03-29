# rutas/admin.py
"""
Panel de administración.
Solo accesible para usuarios con is_admin=True.

Funciones:
- Ver todos los usuarios y sus créditos
- Otorgar créditos gratuitos a cualquier cuenta
"""

import logging
import base64
import os
from pathlib import Path
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


# ─────────────────────────────────────────────────────────────────────────────
#  RUTA TEMPORAL: subir sesion.pkl via web (protegida por token)
#  Usar UNA VEZ para inicializar la sesión en producción, luego eliminar.
# ─────────────────────────────────────────────────────────────────────────────

_SETUP_TOKEN = 'leandro-setup-sesion-2026'


@admin_bp.route('/set-sesion', methods=['GET', 'POST'])
def set_sesion():
    """
    Ruta temporal para subir sesion.pkl desde el browser.
    Requiere token secreto en la URL: /admin/set-sesion?token=...
    """
    token = request.args.get('token', '')
    if token != _SETUP_TOKEN:
        return 'Token inválido', 403

    if request.method == 'POST':
        b64 = request.form.get('sesion_b64', '').strip()
        try:
            data = base64.b64decode(b64)
            sesion_path = os.environ.get('MESA_VIRTUAL_SESSION_PATH', '/data/sesion.pkl')
            Path(sesion_path).parent.mkdir(parents=True, exist_ok=True)
            Path(sesion_path).write_bytes(data)
            return f'<h2>✅ OK — {len(data)} bytes guardados en {sesion_path}</h2>'
        except Exception as e:
            return f'<h2>❌ Error: {e}</h2>', 400

    return '''
    <html><body style="font-family:monospace;padding:2rem">
    <h2>Subir sesion.pkl</h2>
    <form method="post">
        <textarea name="sesion_b64" rows="6" cols="80"
            placeholder="Pegá aquí el contenido base64 de sesion.pkl"></textarea>
        <br><br>
        <button type="submit" style="padding:.5rem 2rem;font-size:1rem">Guardar sesión</button>
    </form>
    </body></html>
    '''
