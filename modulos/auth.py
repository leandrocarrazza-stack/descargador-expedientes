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
import logging
from email_validator import validate_email, EmailNotValidError
from modulos.database import db
from modulos.models import User

logger = logging.getLogger(__name__)


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
        logger.warning(f"Email inválido: {email} - {error_email}")
        return None, f"Email inválido: {error_email}"

    # Validar contraseña
    password_valida, error_password = validar_password(password)
    if not password_valida:
        logger.warning(f"Contraseña inválida para {email}: {error_password}")
        return None, error_password

    # Verificar que email no exista
    usuario_existente = User.query.filter_by(email=email_normalizado).first()
    if usuario_existente:
        logger.warning(f"Intento de registrar email duplicado: {email_normalizado}")
        return None, "El email ya está registrado"

    try:
        # Crear usuario
        usuario = User(
            email=email_normalizado,
            nombre=nombre,
            plan=plan,
            creditos_disponibles=2 if plan == 'free' else 0  # Free tier: 2 descargas
        )
        usuario.establecer_password(password)

        db.session.add(usuario)
        db.session.commit()

        logger.info(f" Usuario creado: {email_normalizado} (plan: {plan})")
        return usuario, None

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error al crear usuario {email}: {e}")
        return None, "Error al crear la cuenta. Intenta nuevamente."


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
        logger.warning(f"Intento de login con email inexistente: {email_normalizado}")
        return None, "Email o contraseña incorrectos"

    # Verificar contraseña
    if not usuario.verificar_password(password):
        logger.warning(f"Intento de login con contraseña incorrecta: {email_normalizado}")
        return None, "Email o contraseña incorrectos"

    logger.info(f" Login exitoso: {email_normalizado}")
    return usuario, None
