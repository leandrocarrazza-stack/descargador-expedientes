"""
Fixtures compartidas para la suite de tests.

La app arranca en modo testing (SQLite en memoria, StaticPool)
sin necesidad de servicios externos ni credenciales reales.
"""
import os

# Configurar ANTES de cualquier import del proyecto
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'ci-test-secret-not-for-production')

import pytest  # noqa: E402
from servidor import crear_app  # noqa: E402
from modulos.database import db as _db  # noqa: E402

_PASS = 'TestPass123!'

# Usuarios fijos compartidos entre todos los tests (creados una sola vez)
_USER_IDS = {}

EMAIL_NORMAL = 'normal@ci.test'
EMAIL_ADMIN = 'admin@ci.test'
EMAIL_BROKE = 'broke@ci.test'


@pytest.fixture(scope='session')
def app():
    """
    Crea la app Flask con SQLite en memoria (StaticPool) y usuarios de prueba.
    Session-scoped: se crea una sola vez para toda la suite.
    """
    application = crear_app()
    application.config['WTF_CSRF_ENABLED'] = False
    application.config['RATELIMIT_ENABLED'] = False  # Sin rate limiting en tests

    with application.app_context():
        _db.create_all()
        _crear_usuarios_base()

    yield application  # outside the app context so each request gets a clean g


def _crear_usuarios_base():
    """Crea los 3 usuarios de prueba en la BD. Idempotente."""
    from modulos.models import User

    definiciones = [
        (EMAIL_NORMAL, False, 5),
        (EMAIL_ADMIN, True, 0),
        (EMAIL_BROKE, False, 0),
    ]
    for email, is_admin, creditos in definiciones:
        if not User.query.filter_by(email=email).first():
            u = User(email=email, nombre='Test', is_admin=is_admin,
                     creditos_disponibles=creditos)
            u.establecer_password(_PASS)
            _db.session.add(u)

    _db.session.commit()

    # Guardar IDs para inyección de sesión
    for email, _, _ in definiciones:
        key = email.split('@')[0]  # 'normal', 'admin', 'broke'
        _USER_IDS[key] = User.query.filter_by(email=email).first().id


@pytest.fixture
def client(app):
    """Test client con sesión limpia para cada test."""
    with app.test_client() as c:
        yield c


def login_como(client, rol: str):
    """
    Inyecta la sesión de Flask-Login directamente (sin pasar por el endpoint HTTP).
    rol: 'normal' | 'admin' | 'broke'
    """
    with client.session_transaction() as sess:
        sess['_user_id'] = str(_USER_IDS[rol])
        sess['_fresh'] = True
        sess['_permanent'] = False


def login_http(client, email, password=_PASS):
    """Login vía el endpoint real de auth (para tests del login en sí)."""
    return client.post('/auth/login', json={'email': email, 'password': password})


def _hacer_usuario(app, email, is_admin=False, creditos=0):
    """Crea un usuario dinámico en la BD y devuelve su id. Útil en tests que necesitan IDs."""
    from modulos.models import User
    from modulos.database import db
    with app.app_context():
        u = User(email=email, nombre='Test', is_admin=is_admin,
                 creditos_disponibles=creditos)
        u.establecer_password(_PASS)
        db.session.add(u)
        db.session.commit()
        return u.id
