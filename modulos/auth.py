"""
Módulo de Autenticación
=======================

Proporciona funciones para:
- Validar email
- Validar contraseña
- Hash seguro de contraseñas
- Crear y verificar usuarios

Uso:
    from modulos.auth import validar_email, validar_password, crear_usuario
    from modulos.models import User

    # Crear usuario
    usuario = crear_usuario('test@example.com', 'Juan', 'password123')

    # Validar
    usuario = User.query.filter_by(email='test@example.com').first()
    if usuario and usuario.verificar_password('password123'):
        print("Login exitoso")
"""

import re
import secrets
import logging
from datetime import datetime, timedelta
from email_validator import validate_email, EmailNotValidError
from modulos.database import db
from modulos.models import User, TokenResetPassword

logger = logging.getLogger(__name__)


def _redact_email(email: str) -> str:
    """Redacta email para logs: user@example.com → us**@example.com"""
    if '@' not in email or len(email) < 3:
        return '***'
    local, domain = email.split('@', 1)
    redacted = local[:2] + '**@' + domain if len(local) > 2 else '**@' + domain
    return redacted


def validar_email(email):
    """
    Valida que el email sea correcto.

    Args:
        email (str): Email a validar

    Returns:
        tuple: (válido: bool, email_normalizado: str, error: str)
    """
    try:
        # Validar formato
        email_valido = validate_email(email)
        email_normalizado = email_valido.email
        return True, email_normalizado, None
    except EmailNotValidError as e:
        return False, None, str(e)


def validar_password(password):
    """
    Valida que la contraseña cumpla requisitos de seguridad.

    Requisitos:
    - Mínimo 8 caracteres
    - Al menos 1 mayúscula
    - Al menos 1 minúscula
    - Al menos 1 número
    - Al menos 1 carácter especial (opcional)

    Args:
        password (str): Contraseña a validar

    Returns:
        tuple: (válida: bool, error: str)
    """
    if not password:
        return False, "La contraseña no puede estar vacía"

    if len(password) < 8:
        return False, "La contraseña debe tener mínimo 8 caracteres"

    if not re.search(r'[A-Z]', password):
        return False, "La contraseña debe contener al menos 1 mayúscula"

    if not re.search(r'[a-z]', password):
        return False, "La contraseña debe contener al menos 1 minúscula"

    if not re.search(r'\d', password):
        return False, "La contraseña debe contener al menos 1 número"

    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\;\'`~/]', password):
        return False, "La contraseña debe contener al menos 1 carácter especial (!@#$%...)"

    return True, None


def crear_usuario(email, nombre, password, plan='free'):
    """
    Crea un nuevo usuario en la BD.

    Args:
        email (str): Email del usuario
        nombre (str): Nombre completo
        password (str): Contraseña (será hasheada)
        plan (str): Plan inicial (por defecto: free)

    Returns:
        tuple: (usuario: User o None, error: str o None)
    """
    # Validar email
    email_valido, email_normalizado, error_email = validar_email(email)
    if not email_valido:
        logger.warning(f"Email inválido: {_redact_email(email)} - {error_email}")
        return None, f"Email inválido: {error_email}"

    # Validar contraseña
    password_valida, error_password = validar_password(password)
    if not password_valida:
        logger.warning(f"Contraseña inválida para {_redact_email(email)}: {error_password}")
        return None, error_password

    # Verificar que email no exista
    usuario_existente = User.query.filter_by(email=email_normalizado).first()
    if usuario_existente:
        logger.warning(f"Intento de registrar email duplicado: {_redact_email(email_normalizado)}")
        return None, "El email ya está registrado"

    try:
        # Crear usuario
        usuario = User(
            email=email_normalizado,
            nombre=nombre,
            plan=plan,
            creditos_disponibles=2 if plan == 'free' else 0  # Free tier: 2 descargas de prueba
        )
        usuario.establecer_password(password)

        db.session.add(usuario)
        db.session.commit()

        logger.info(f" Usuario creado: {_redact_email(email_normalizado)} (plan: {plan})")
        return usuario, None

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error al crear usuario {_redact_email(email)}: {e}")
        return None, "Error al crear la cuenta. Intenta nuevamente."


def generar_token_reset(email):
    """
    Genera un token de reset de contraseña para el email dado.

    Para evitar enumeración de emails, siempre retorna éxito aunque el email
    no exista. Solo devuelve el token real si el usuario existe (para enviar email).

    Returns:
        tuple: (token: str o None, error: str o None)
            - token es None si el email no está registrado (respuesta genérica)
    """
    email_valido, email_normalizado, error = validar_email(email)
    if not email_valido:
        return None, "Email inválido"

    usuario = User.query.filter_by(email=email_normalizado).first()
    if not usuario:
        return None, None  # No revelar que el email no existe

    # Invalidar tokens anteriores no usados
    TokenResetPassword.query.filter_by(user_id=usuario.id, usado=False).update({'usado': True})

    token = secrets.token_urlsafe(48)
    expira = datetime.utcnow() + timedelta(hours=1)

    reset_token = TokenResetPassword(
        user_id=usuario.id,
        token=token,
        expira_en=expira
    )
    db.session.add(reset_token)
    db.session.commit()

    logger.info(f"Token de reset generado para {_redact_email(email_normalizado)}")
    return token, None


def resetear_password(token, nueva_password):
    """
    Valida el token y cambia la contraseña del usuario.

    Args:
        token (str): Token recibido por email
        nueva_password (str): Nueva contraseña

    Returns:
        tuple: (éxito: bool, error: str o None)
    """
    reset_token = TokenResetPassword.query.filter_by(token=token).first()

    if not reset_token or not reset_token.es_valido():
        return False, "El enlace de recuperación es inválido o ya expiró"

    valida, error = validar_password(nueva_password)
    if not valida:
        return False, error

    usuario = db.session.get(User, reset_token.user_id)
    if not usuario:
        return False, "Usuario no encontrado"

    usuario.establecer_password(nueva_password)
    reset_token.usado = True
    db.session.commit()

    logger.info(f"Contraseña reseteada para user_id={usuario.id}")
    return True, None


def obtener_usuario(email):
    """
    Obtiene un usuario por email.

    Args:
        email (str): Email del usuario

    Returns:
        User o None
    """
    return User.query.filter_by(email=email).first()


def verificar_credenciales(email, password):
    """
    Verifica email y contraseña.

    Args:
        email (str): Email del usuario
        password (str): Contraseña

    Returns:
        tuple: (usuario: User o None, error: str o None)
    """
    # Normalizar email
    email_valido, email_normalizado, error = validar_email(email)
    if not email_valido:
        return None, "Email inválido"

    # Buscar usuario
    usuario = obtener_usuario(email_normalizado)
    if not usuario:
        logger.warning(f"Intento de login con email inexistente: {_redact_email(email_normalizado)}")
        return None, "Email o contraseña incorrectos"

    # Verificar contraseña
    if not usuario.verificar_password(password):
        logger.warning(f"Intento de login con contraseña incorrecta: {_redact_email(email_normalizado)}")
        return None, "Email o contraseña incorrectos"

    logger.info(f" Login exitoso: {_redact_email(email_normalizado)}")
    return usuario, None
