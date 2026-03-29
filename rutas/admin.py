# rutas/admin.py
"""
Panel de administración.
Solo accesible para usuarios con is_admin=True.

Funciones:
- Ver todos los usuarios y sus créditos
- Otorgar créditos gratuitos a cualquier cuenta
"""

import logging
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


@admin_bp.route('/subir-sesion', methods=['POST'])
@login_required
@requiere_admin
def subir_sesion():
    """
    Sube el archivo de sesión de Mesa Virtual (.pkl) al servidor.

    Permite renovar la sesión cuando vence, sin necesidad de redesplegar.
    Solo accesible para admins. El archivo se guarda en la ruta configurada
    por la variable de entorno MESA_VIRTUAL_SESSION_PATH (en Render: /data/sesion.pkl).
    """
    # Verificar que se envió un archivo
    if 'archivo' not in request.files:
        return jsonify({'exito': False, 'mensaje': 'No se recibió ningún archivo'}), 400

    archivo = request.files['archivo']

    if not archivo.filename:
        return jsonify({'exito': False, 'mensaje': 'No se seleccionó ningún archivo'}), 400

    # Solo aceptar archivos .pkl (el formato de sesión de Selenium)
    if not archivo.filename.endswith('.pkl'):
        return jsonify({'exito': False, 'mensaje': 'El archivo debe tener extensión .pkl'}), 400

    # Determinar dónde guardar la sesión
    # En Render: MESA_VIRTUAL_SESSION_PATH=/data/sesion.pkl
    # En local: ~/.mesa_virtual_sesion.pkl
    ruta_sesion = os.environ.get(
        'MESA_VIRTUAL_SESSION_PATH',
        str(Path.home() / '.mesa_virtual_sesion.pkl')
    )
    ruta_destino = Path(ruta_sesion)

    # Crear directorio si no existe (ej: /data/ en Render)
    ruta_destino.parent.mkdir(parents=True, exist_ok=True)

    # Guardar el archivo en el disco del servidor
    archivo.save(str(ruta_destino))
    tamaño = ruta_destino.stat().st_size

    logger.info(
        f"Admin {current_user.email} subió nueva sesión de Mesa Virtual "
        f"→ {ruta_destino} ({tamaño} bytes)"
    )

    return jsonify({
        'exito': True,
        'mensaje': f'Sesión guardada correctamente ({tamaño} bytes)',
        'ruta': str(ruta_destino),
        'tamaño': tamaño
    })

