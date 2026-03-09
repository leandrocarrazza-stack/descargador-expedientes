"""
Rutas de Autenticación
======================

Endpoints para:
- POST /auth/signup: Crear cuenta
- POST /auth/login: Iniciar sesión
- GET /auth/logout: Cerrar sesión
- GET /auth/user: Obtener datos del usuario actual

Uso:
    POST /auth/signup
    {
        "email": "user@example.com",
        "nombre": "Juan Pérez",
        "password": "SecurePass123"
    }

    POST /auth/login
    {
        "email": "user@example.com",
        "password": "SecurePass123"
    }
"""

from flask import Blueprint, request, jsonify, session, render_template
from flask_login import login_user, logout_user, login_required, current_user
from modulos.auth import crear_usuario, verificar_credenciales, validar_email
from modulos.models import User
from modulos.database import db
from modulos.logger import crear_logger

logger = crear_logger(__name__)

# Crear blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    """
    Muestra formulario de signup (GET) o crea cuenta (POST).

    GET: Retorna formulario HTML

    POST Body (JSON):
        - email: Email del usuario
        - nombre: Nombre completo
        - password: Contraseña (mín. 8 chars, mayús, minús, número)

    Returns:
        GET: HTML del formulario
        POST: 200 con datos del usuario o 400 con error
    """
    # Si es GET, retornar el formulario
    if request.method == 'GET':
        return render_template('signup.html')

    # Si es POST, crear la cuenta
    try:
        datos = request.get_json()

        if not datos:
            return jsonify({'error': 'Datos vacíos'}), 400

        email = datos.get('email', '').strip()
        nombre = datos.get('nombre', '').strip()
        password = datos.get('password', '').strip()

        # Validar que no estén vacíos
        if not email or not nombre or not password:
            return jsonify({'error': 'Email, nombre y contraseña son requeridos'}), 400

        # Crear usuario
        usuario, error = crear_usuario(email, nombre, password, plan='free')

        if error:
            logger.warning(f"Error en signup: {error}")
            return jsonify({'error': error}), 400

        # Login automático después de crear cuenta
        login_user(usuario, remember=False)

        logger.info(f"✅ Usuario registrado y logueado: {email}")

        return jsonify({
            'mensaje': '✅ Cuenta creada exitosamente',
            'usuario': usuario.obtener_info()
        }), 200

    except Exception as e:
        logger.error(f"Error en signup: {e}", exc_info=True)
        return jsonify({'error': 'Error interno del servidor'}), 500


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Muestra formulario de login (GET) o inicia sesión (POST).

    GET: Retorna formulario HTML

    POST Body (JSON):
        - email: Email del usuario
        - password: Contraseña

    Returns:
        GET: HTML del formulario
        POST: 200 con datos del usuario o 401/400 con error
    """
    # Si es GET, retornar el formulario
    if request.method == 'GET':
        return render_template('login.html')

    # Si es POST, iniciar sesión
    try:
        datos = request.get_json()

        if not datos:
            return jsonify({'error': 'Datos vacíos'}), 400

        email = datos.get('email', '').strip()
        password = datos.get('password', '').strip()

        if not email or not password:
            return jsonify({'error': 'Email y contraseña requeridos'}), 400

        # Verificar credenciales
        usuario, error = verificar_credenciales(email, password)

        if error:
            return jsonify({'error': error}), 401

        # Iniciar sesión
        login_user(usuario, remember=datos.get('recuerdame', False))

        logger.info(f"✅ Login exitoso: {email}")

        return jsonify({
            'mensaje': '✅ Login exitoso',
            'usuario': usuario.obtener_info()
        }), 200

    except Exception as e:
        logger.error(f"Error en login: {e}", exc_info=True)
        return jsonify({'error': 'Error interno del servidor'}), 500


@auth_bp.route('/logout', methods=['GET'])
@login_required
def logout():
    """
    Cierra la sesión del usuario actual.

    Returns:
        200: Logout exitoso
    """
    email = current_user.email
    logout_user()

    logger.info(f"✅ Logout: {email}")

    return jsonify({'mensaje': '✅ Sesión cerrada'}), 200


@auth_bp.route('/user', methods=['GET'])
@login_required
def obtener_usuario():
    """
    Obtiene datos del usuario actualmente logueado.

    Requiere:
        - Usuario autenticado (header Authorization o sesión)

    Returns:
        200: Datos del usuario
        401: No autenticado
    """
    try:
        return jsonify({
            'usuario': current_user.obtener_info()
        }), 200

    except Exception as e:
        logger.error(f"Error en /user: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500


@auth_bp.route('/verificar-email', methods=['POST'])
def verificar_email_disponible():
    """
    Verifica si un email está disponible (para validación en signup).

    Body (JSON):
        - email: Email a verificar

    Returns:
        200: Email disponible
        409: Email ya registrado
    """
    try:
        datos = request.get_json()
        email = datos.get('email', '').strip()

        if not email:
            return jsonify({'error': 'Email requerido'}), 400

        # Validar formato de email
        valido, _, error = validar_email(email)
        if not valido:
            return jsonify({'error': f'Email inválido: {error}'}), 400

        # Verificar si existe
        usuario = User.query.filter_by(email=email).first()

        if usuario:
            return jsonify({'disponible': False, 'error': 'Email ya registrado'}), 409

        return jsonify({'disponible': True}), 200

    except Exception as e:
        logger.error(f"Error en verificar-email: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500
