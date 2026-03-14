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
    login_manager.login_message = '🔐 Por favor inicia sesión'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def cargar_usuario(user_id):
        """Carga un usuario por ID (necesario para Flask-Login)."""
        return User.query.get(int(user_id))

    # Hacer que User sea compatible con Flask-Login
    User.is_authenticated = property(lambda self: True)
    User.is_active = property(lambda self: True)
    User.is_anonymous = property(lambda self: False)

    def obtener_id(self):
        return str(self.id)
    User.get_id = obtener_id

    # CORS (para llamadas AJAX desde frontend)
    CORS(app, supports_credentials=True)

    # ═════════════════════════════════════════════════════════════════════
    #  CREAR TABLAS Y CONTEXTO DE APLICACIÓN
    # ═════════════════════════════════════════════════════════════════════

    with app.app_context():
        db.create_all()
        logger.info(f"✅ Tablas de BD creadas (ambiente: {config.FLASK_ENV})")

    # ═════════════════════════════════════════════════════════════════════
    #  REGISTRAR BLUEPRINTS
    # ═════════════════════════════════════════════════════════════════════

    from rutas.auth import auth_bp
    from rutas.pagos import pagos_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pagos_bp)

    logger.info("✅ Blueprints registrados")

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

    # ═════════════════════════════════════════════════════════════════════
    #  LOGGING
    # ═════════════════════════════════════════════════════════════════════

    logger.info(f"✅ Aplicación Flask inicializada (ambiente: {config.FLASK_ENV})")

    return app


# ═══════════════════════════════════════════════════════════════════════════
#  CREAR LA APP
# ═══════════════════════════════════════════════════════════════════════════

app = crear_app()

if __name__ == '__main__':
    """Ejecuta el servidor de desarrollo."""
    logger.info("🚀 Iniciando servidor Flask...")

    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=config.DEBUG,
        use_reloader=config.DEBUG
    )
