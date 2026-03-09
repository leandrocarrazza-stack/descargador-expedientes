"""
Configuración de Base de Datos con SQLAlchemy
==============================================

Inicializa la BD y proporciona la instancia de SQLAlchemy
para usar en toda la aplicación.

Uso:
    from modulos.database import db
    from modulos.models import User

    usuario = User.query.filter_by(email='test@example.com').first()
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# Instancia global de SQLAlchemy
db = SQLAlchemy()

# Instancia global de Migrate (para alembic)
migrate = Migrate()


def init_db(app):
    """
    Inicializa la BD con la aplicación Flask.

    Args:
        app: Instancia de Flask
    """
    db.init_app(app)
    migrate.init_app(app, db)

    # Crear tablas si no existen
    with app.app_context():
        db.create_all()


def reset_db(app):
    """
    Resetea la BD (SOLO para desarrollo/testing).

    PELIGRO: Borra todos los datos.

    Args:
        app: Instancia de Flask
    """
    with app.app_context():
        db.drop_all()
        db.create_all()
        print("✅ Base de datos reseteada")
