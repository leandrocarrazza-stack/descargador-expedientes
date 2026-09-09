#!/usr/bin/env python3
"""
Tests de las rutas de "Mi cuenta" (lote 2): nombre, contraseña,
notificaciones. `python test_rutas_cuenta.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
# Ver tests/test_rutas_admin.py: hay que pisar esto ANTES de importar
# servidor (que instancia una app a nivel de módulo con la config real).
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.models import User
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _app_y_cliente():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _crear_usuario(email='user@foja.com', password='Password123!'):
    user = User(email=email, nombre='Original')
    user.establecer_password(password)
    db.session.add(user)
    db.session.commit()
    return user.id


def test_cambiar_nombre():
    app = _app_y_cliente()
    with app.app_context():
        user_id = _crear_usuario()

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/cuenta/nombre', json={'nombre': 'Nuevo Nombre'})
    check("cambiar nombre -> 200", resp.status_code == 200, resp.status_code)

    with app.app_context():
        user = db.session.get(User, user_id)
        check("el nombre se guardó", user.nombre == 'Nuevo Nombre', user.nombre)

    resp = client.post('/cuenta/nombre', json={'nombre': 'a'})
    check("nombre muy corto -> 400", resp.status_code == 400, resp.status_code)


def test_cambiar_password_actual_incorrecta():
    app = _app_y_cliente()
    with app.app_context():
        user_id = _crear_usuario()

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/cuenta/password', json={'actual': 'mala', 'nueva': 'NuevaPass123!'})
    check("password actual incorrecta -> 400", resp.status_code == 400, resp.status_code)

    with app.app_context():
        user = db.session.get(User, user_id)
        check("la contraseña vieja sigue funcionando", user.verificar_password('Password123!'))


def test_cambiar_password_debil_rechazada():
    app = _app_y_cliente()
    with app.app_context():
        user_id = _crear_usuario()

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/cuenta/password', json={'actual': 'Password123!', 'nueva': 'debil'})
    check("password nueva débil -> 400", resp.status_code == 400, resp.status_code)


def test_cambiar_password_ok_y_permite_login():
    app = _app_y_cliente()
    with app.app_context():
        user_id = _crear_usuario()

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/cuenta/password', json={'actual': 'Password123!', 'nueva': 'OtraPass456!'})
    check("cambio de password -> 200", resp.status_code == 200, resp.status_code)

    with app.app_context():
        user = db.session.get(User, user_id)
        check("la contraseña nueva verifica", user.verificar_password('OtraPass456!'))
        check("la contraseña vieja ya no verifica", not user.verificar_password('Password123!'))


def test_toggle_notificaciones_persiste():
    app = _app_y_cliente()
    with app.app_context():
        user_id = _crear_usuario()
        user = db.session.get(User, user_id)
        check("notificar_email arranca en False/None", not user.notificar_email)

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/cuenta/notificaciones', json={'notificar_email': True})
    check("activar notificaciones -> 200", resp.status_code == 200, resp.status_code)

    with app.app_context():
        user = db.session.get(User, user_id)
        check("notificar_email quedó en True", user.notificar_email is True)

    resp = client.post('/cuenta/notificaciones', json={'notificar_email': False})
    with app.app_context():
        user = db.session.get(User, user_id)
        check("notificar_email vuelve a False", user.notificar_email is False)


def test_requiere_login():
    app = _app_y_cliente()
    client = app.test_client()
    resp = client.get('/cuenta/')
    check("sin login -> redirige (302)", resp.status_code == 302, resp.status_code)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE RUTAS MI CUENTA")
    print("=" * 70)

    test_cambiar_nombre()
    test_cambiar_password_actual_incorrecta()
    test_cambiar_password_debil_rechazada()
    test_cambiar_password_ok_y_permite_login()
    test_toggle_notificaciones_persiste()
    test_requiere_login()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
