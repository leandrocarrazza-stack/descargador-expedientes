#!/usr/bin/env python3
"""
Servidor Flask para Descargador de Expedientes
==============================================

Aplicación principal que:
1. Inicializa Flask y extensiones (SQLAlchemy, Login, CORS)
2. Registra blueprints de rutas
3. Maneja errores y logging
4. Configura seguridad

Uso:
    # Desarrollo local
    python servidor.py

    # Producción
    gunicorn --bind 0.0.0.0:5000 servidor:app
"""

import os
import sys
from pathlib import Path

# Agregar proyecto a sys.path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, render_template
from flask_login import LoginManager, UserMixin
from flask_cors import CORS

import config
from modulos.database import db, migrate
from modulos.models import User
# Celery está deprecated - usamos threading en lugar de Celery para descargas
# from modulos.celery_app import init_celery_with_app
from modulos.extensions import limiter, csrf, mail

# Logger simple sin módulo externo
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  CREAR APLICACIÓN FLASK
# ═══════════════════════════════════════════════════════════════════════════


def crear_app(config_obj=None):
    """
    Factory pattern para crear la aplicación Flask.

    Args:
        config_obj: Objeto de configuración (por defecto: config.py)

    Returns:
        Flask: Aplicación configurada
    """
    app = Flask(__name__)

    # Cargar configuración
    app.config.from_object(config_obj or config)

    # ═════════════════════════════════════════════════════════════════════
    #  INICIALIZAR EXTENSIONES
    # ═════════════════════════════════════════════════════════════════════

    # Base de datos
    db.init_app(app)
    migrate.init_app(app, db)

    # Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'  # Redirige a login si no autenticado
    login_manager.login_message = ' Por favor inicia sesión'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def cargar_usuario(user_id):
        """Carga un usuario por ID (necesario para Flask-Login)."""
        return db.session.get(User, int(user_id))

    # CORS - solo orígenes configurados
    allowed_origins_raw = os.getenv('CORS_ALLOWED_ORIGINS', '')
    allowed_origins = [o.strip() for o in allowed_origins_raw.split(',') if o.strip()]
    if not allowed_origins:
        logger.warning("[SECURITY] CORS_ALLOWED_ORIGINS no configurado — bloqueando cross-origin")
    CORS(app, origins=allowed_origins or [], supports_credentials=True)

    # Inicializar rate limiter, CSRF y mail
    limiter.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)

    # ═════════════════════════════════════════════════════════════════════
    #  NOTA: Celery está deprecated
    # ═════════════════════════════════════════════════════════════════════
    # Usamos threading en lugar de Celery para descargas asincrónicas.
    # Los archivos de Celery se mantienen para compatibilidad con código antiguo.
    # init_celery_with_app(app)  ← Comentado - no es necesario
    logger.info("[OK] Tareas asincrónicas via threading (Celery deprecated)")

    # ═════════════════════════════════════════════════════════════════════
    #  CREAR TABLAS Y CONTEXTO DE APLICACIÓN
    # ═════════════════════════════════════════════════════════════════════

    with app.app_context():
        try:
            db.create_all()
            logger.info(f"[OK] Tablas de BD creadas (ambiente: {config.FLASK_ENV})")
        except Exception as e:
            # Con múltiples workers de Gunicorn puede haber condición de carrera
            # al crear tablas simultáneamente. Si ya existen, es seguro continuar.
            error_str = str(e)
            if "already exists" in error_str or "UniqueViolation" in error_str:
                logger.info("[OK] Tablas ya existentes (otro worker las creó primero)")
            else:
                raise

    # ═════════════════════════════════════════════════════════════════════
    #  CARGAR TESAURO DE JURISPRUDENCIA
    # ═════════════════════════════════════════════════════════════════════

    from modulos.jurisprudencia.tesauro import cargar_tesauros
    cargar_tesauros(app)

    # ═════════════════════════════════════════════════════════════════════
    #  REGISTRAR BLUEPRINTS
    # ═════════════════════════════════════════════════════════════════════

    from rutas.auth import auth_bp
    from rutas.pagos import pagos_bp
    from rutas.descargas import descargas_bp, limpiar_pdfs_antiguos
    from rutas.admin import admin_bp
    from rutas.contacto import contacto_bp
    from rutas.jurisprudencia import jurisprudencia_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pagos_bp)
    app.register_blueprint(descargas_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(contacto_bp)
    app.register_blueprint(jurisprudencia_bp)

    logger.info("[OK] Blueprints registrados (auth, pagos, descargas, admin, jurisprudencia)")

    # Limpiar PDFs antiguos del disco al iniciar la app
    # Evita que el disco del servidor se llene con descargas viejas
    limpiar_pdfs_antiguos()

    # ═════════════════════════════════════════════════════════════════════
    #  RUTAS PRINCIPALES
    # ═════════════════════════════════════════════════════════════════════

    @app.route('/')
    def index():
        """Página de inicio."""
        from flask_login import current_user

        if current_user.is_authenticated:
            return render_template('dashboard.html')
        else:
            return render_template('inicio.html')

    @app.route('/dashboard')
    def dashboard():
        """Dashboard del usuario (requiere login)."""
        from flask_login import login_required

        @login_required
        def _dashboard():
            from flask_login import current_user
            return render_template('dashboard.html', usuario=current_user)

        return _dashboard()

    # ═════════════════════════════════════════════════════════════════════
    #  SECURITY HEADERS
    # ═════════════════════════════════════════════════════════════════════

    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none';"
        )
        return response

    # ═════════════════════════════════════════════════════════════════════
    #  MANEJO DE ERRORES
    # ═════════════════════════════════════════════════════════════════════

    @app.errorhandler(404)
    def pagina_no_encontrada(error):
        """Error 404."""
        return render_template('error.html', error='Página no encontrada'), 404

    @app.errorhandler(500)
    def error_servidor(error):
        """Error 500."""
        logger.error(f"Error 500: {error}", exc_info=True)
        return render_template('error.html', error='Error interno del servidor'), 500

    @app.errorhandler(403)
    def sin_permiso(error):
        """Error 403 (forbidden)."""
        return render_template('error.html', error='No tienes permiso'), 403

    # ═════════════════════════════════════════════════════════════════════
    #  CONTEXT PROCESSORS (variables disponibles en todos los templates)
    # ═════════════════════════════════════════════════════════════════════

    @app.context_processor
    def inyectar_planes():
        """Inyecta planes en todos los templates."""
        return dict(planes=config.PLANES)

    @app.context_processor
    def inyectar_contacto():
        """Inyecta datos de contacto en todos los templates."""
        return dict(
            contact_email=config.CONTACT_EMAIL,
            contact_whatsapp=config.CONTACT_WHATSAPP,
        )

    # ═════════════════════════════════════════════════════════════════════
    #  LOGGING
    # ═════════════════════════════════════════════════════════════════════

    logger.info(f"[OK] Aplicación Flask inicializada (ambiente: {config.FLASK_ENV})")

    return app


# ═══════════════════════════════════════════════════════════════════════════
#  CREAR LA APP
# ═══════════════════════════════════════════════════════════════════════════

app = crear_app()

if __name__ == '__main__':
    """Ejecuta el servidor de desarrollo."""
    logger.info(" Iniciando servidor Flask...")

    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=config.DEBUG,
        use_reloader=config.DEBUG
    )
