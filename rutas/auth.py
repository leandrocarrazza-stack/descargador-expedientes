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

import logging
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from modulos.auth import crear_usuario, verificar_credenciales, validar_email, generar_token_reset, resetear_password
from modulos.models import User, TokenResetPassword
from modulos.database import db
from modulos.extensions import limiter, csrf, mail

logger = logging.getLogger(__name__)

# Crear blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _safe_next(url: str, default: str = '/descargas/expediente') -> str:
    """Solo permite URLs relativas para prevenir open redirects."""
    if not url:
        return default
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return default
    return url


@auth_bp.route('/signup', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per hour")
@csrf.exempt
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
@limiter.limit("10 per minute; 50 per hour")
@csrf.exempt
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

    # Redirigir al login en lugar de devolver JSON
    return redirect(url_for('auth.login'))


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
@csrf.exempt
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


def _enviar_email_reset(email, reset_url):
    """Envía el email con el link de recuperación. Loguea el link si el mail no está configurado."""
    try:
        msg = Message(
            subject='Recuperación de contraseña · Foja',
            recipients=[email],
            html=f"""
            <p>Hola,</p>
            <p>Recibimos una solicitud para restablecer tu contraseña en <strong>Foja</strong>.</p>
            <p>Hacé clic en el siguiente enlace para crear una nueva contraseña (válido por 1 hora):</p>
            <p><a href="{reset_url}" style="background:#b8860b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;">
               Restablecer contraseña
            </a></p>
            <p>Si no solicitaste este cambio, podés ignorar este mensaje.</p>
            <p>El equipo de Foja</p>
            """,
            body=f"Restablecé tu contraseña en: {reset_url}\n\nEl enlace expira en 1 hora."
        )
        mail.send(msg)
        logger.info(f"Email de reset enviado a {email[:2]}**")
    except Exception as e:
        logger.warning(f"No se pudo enviar email de reset (¿MAIL_USERNAME configurado?): {e}")
        logger.info(f"[DEV] Link de reset: {reset_url}")


@auth_bp.route('/olvide-contrasena', methods=['GET', 'POST'])
@limiter.limit("3 per minute; 10 per hour")
@csrf.exempt
def olvide_contrasena():
    """
    Muestra formulario para ingresar email (GET) o envía el link de reset (POST).

    POST Body (JSON):
        - email: Email del usuario

    Returns:
        GET: HTML del formulario
        POST: 200 siempre (para no revelar si el email existe o no)
    """
    if request.method == 'GET':
        return render_template('olvide_contrasena.html')

    try:
        datos = request.get_json()
        if not datos:
            return jsonify({'error': 'Datos vacíos'}), 400

        email = datos.get('email', '').strip()
        if not email:
            return jsonify({'error': 'Email requerido'}), 400

        token, error = generar_token_reset(email)

        if error:
            return jsonify({'error': error}), 400

        # Si el usuario existe, enviar el email con el link
        if token:
            reset_url = url_for('auth.reset_contrasena', token=token, _external=True)
            _enviar_email_reset(email, reset_url)

        # Respuesta genérica para no revelar si el email existe
        return jsonify({
            'mensaje': 'Si el email está registrado, recibirás un enlace para restablecer tu contraseña.'
        }), 200

    except Exception as e:
        logger.error(f"Error en olvide_contrasena: {e}", exc_info=True)
        return jsonify({'error': 'Error interno del servidor'}), 500


@auth_bp.route('/reset-contrasena/<token>', methods=['GET', 'POST'])
@csrf.exempt
def reset_contrasena(token):
    """
    Muestra formulario para ingresar nueva contraseña (GET) o la actualiza (POST).

    GET: Valida el token y muestra el formulario o un error si expiró.

    POST Body (JSON):
        - password: Nueva contraseña

    Returns:
        GET: HTML del formulario
        POST: 200 con mensaje de éxito o 400 con error
    """
    if request.method == 'GET':
        reset_token = TokenResetPassword.query.filter_by(token=token).first()
        if not reset_token or not reset_token.es_valido():
            return render_template('reset_contrasena.html', token_invalido=True)
        return render_template('reset_contrasena.html', token=token)

    try:
        datos = request.get_json()
        if not datos:
            return jsonify({'error': 'Datos vacíos'}), 400

        nueva_password = datos.get('password', '').strip()
        if not nueva_password:
            return jsonify({'error': 'Contraseña requerida'}), 400

        exito, error = resetear_password(token, nueva_password)

        if not exito:
            return jsonify({'error': error}), 400

        return jsonify({'mensaje': 'Contraseña actualizada correctamente. Ya podés iniciar sesión.'}), 200

    except Exception as e:
        logger.error(f"Error en reset_contrasena: {e}", exc_info=True)
        return jsonify({'error': 'Error interno del servidor'}), 500


# ── Rutas de Login Relay con Mesa Virtual ─────────────────────────────────────

@auth_bp.route('/mv-login', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per minute")
@csrf.exempt
def mv_login():
    """
    GET:  Muestra el formulario para conectar Mesa Virtual.
    POST: Inicia el login en Mesa Virtual con usuario + contraseña.

    POST Body (JSON):
        - mv_usuario: Usuario de Mesa Virtual
        - mv_password: Contraseña de Mesa Virtual (NO se guarda)
        - next: URL a la que redirigir después del login (opcional)

    Response JSON:
        - {'estado': '2fa_requerido', 'session_id': '...'} → pedir código
        - {'estado': 'ok'} → login sin 2FA, listo para descargar
        - {'estado': 'error', 'mensaje': '...'} → algo falló
    """
    from modulos.auth_mv import iniciar_login_mv, guardar_sesion_usuario

    if request.method == 'GET':
        # Mostrar formulario de conexión con Mesa Virtual
        next_url = _safe_next(request.args.get('next', ''))
        return render_template('mv_login.html', next_url=next_url)

    # POST: iniciar login
    try:
        datos = request.get_json() or {}
        mv_usuario = datos.get('mv_usuario', '').strip()
        mv_password = datos.get('mv_password', '').strip()
        next_url = _safe_next(datos.get('next', ''))

        if not mv_usuario or not mv_password:
            return jsonify({'estado': 'error', 'mensaje': 'Usuario y contraseña son requeridos'}), 400

        logger.info(f"[MV-LOGIN] User {current_user.id} iniciando login en MV como '{mv_usuario}'")

        resultado = iniciar_login_mv(mv_usuario, mv_password)

        # Si el login fue directo (sin 2FA), guardar las cookies ya
        if resultado.get('estado') == 'ok':
            guardar_sesion_usuario(
                user_id=current_user.id,
                cookies=resultado['cookies'],
                mv_usuario=resultado.get('mv_usuario', mv_usuario)
            )
            return jsonify({'estado': 'ok', 'next': next_url}), 200

        return jsonify(resultado), 200

    except Exception as e:
        logger.error(f"[MV-LOGIN] Error: {e}", exc_info=True)
        return jsonify({'estado': 'error', 'mensaje': 'Error interno. Intentá de nuevo.'}), 500


@auth_bp.route('/mv-2fa', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
@csrf.exempt
def mv_2fa():
    """
    Completa el login de Mesa Virtual con el código 2FA.

    POST Body (JSON):
        - session_id: ID de la sesión pendiente (de /auth/mv-login)
        - codigo: Código de 6 dígitos del autenticador
        - next: URL a redirigir después del login (opcional)

    Response JSON:
        - {'estado': 'ok', 'next': '...'} → login completo, listo para descargar
        - {'estado': 'error', 'mensaje': '...'} → código incorrecto u otro error
    """
    from modulos.auth_mv import completar_login_mv, guardar_sesion_usuario

    try:
        datos = request.get_json() or {}
        session_id = datos.get('session_id', '').strip()
        codigo = datos.get('codigo', '').strip()
        next_url = _safe_next(datos.get('next', ''))

        if not session_id or not codigo:
            return jsonify({'estado': 'error', 'mensaje': 'Faltan datos requeridos'}), 400

        if len(codigo) != 6 or not codigo.isdigit():
            return jsonify({'estado': 'error', 'mensaje': 'El código debe tener 6 dígitos numéricos'}), 400

        logger.info(f"[MV-2FA] User {current_user.id} verificando código 2FA")

        resultado = completar_login_mv(session_id, codigo)

        if resultado.get('estado') == 'ok':
            # Guardar cookies en BD asociadas al usuario
            guardar_sesion_usuario(
                user_id=current_user.id,
                cookies=resultado['cookies'],
                mv_usuario=resultado.get('mv_usuario', '')
            )
            logger.info(f"[MV-2FA] Login completo para user {current_user.id}")
            return jsonify({'estado': 'ok', 'next': next_url}), 200

        return jsonify(resultado), 200

    except Exception as e:
        logger.error(f"[MV-2FA] Error: {e}", exc_info=True)
        return jsonify({'estado': 'error', 'mensaje': 'Error interno. Intentá de nuevo.'}), 500


@auth_bp.route('/mv-estado', methods=['GET'])
@login_required
def mv_estado():
    """
    Informa si el usuario tiene una sesión activa de Mesa Virtual.
    No verifica con Selenium (rápido), solo mira si hay cookies en BD.
    """
    from modulos.models import SesionUsuarioMV

    sesion = SesionUsuarioMV.query.filter_by(user_id=current_user.id).first()
    if sesion:
        return jsonify({
            'tiene_sesion': True,
            'mv_usuario': sesion.mv_usuario,
            'actualizado_en': sesion.actualizado_en.isoformat()
        }), 200

    return jsonify({'tiene_sesion': False}), 200
