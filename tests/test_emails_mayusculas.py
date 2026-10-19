#!/usr/bin/env python3
"""
Tests: el email no distingue mayúsculas/minúsculas en registro, login,
reseteo de contraseña, verificar-email y admin. Incluye un usuario viejo
guardado con mayúsculas (como los que ya existen en producción).
`python test_emails_mayusculas.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
# Ver tests/test_rutas_admin.py: pisar esto ANTES de importar servidor.
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.models import User
from modulos.auth import crear_usuario, verificar_credenciales, generar_token_reset
from servidor import crear_app

_fallos = []
PASSWORD = 'Password123!'


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _usuario_viejo(email='Leo@Gmail.com'):
    """Inserta directo, sin pasar por crear_usuario: simula datos previos al fix."""
    u = User(email=email, nombre='Viejo')
    u.establecer_password(PASSWORD)
    db.session.add(u)
    db.session.commit()
    return u.id


def test_registro_guarda_en_minusculas():
    app = _app()
    with app.app_context():
        usuario, error = crear_usuario('Nuevo.Usuario@Gmail.COM', 'N', PASSWORD)
        check("registro OK", usuario is not None, error)
        check("se guarda en minúsculas", usuario.email == 'nuevo.usuario@gmail.com', usuario.email)


def test_login_cualquier_variante():
    app = _app()
    with app.app_context():
        crear_usuario('Ana@Foja.com', 'Ana', PASSWORD)
        for variante in ('ana@foja.com', 'ANA@FOJA.COM', 'Ana@Foja.com'):
            usuario, error = verificar_credenciales(variante, PASSWORD)
            check(f"login con '{variante}'", usuario is not None, error)


def test_usuario_viejo_con_mayusculas_puede_entrar():
    app = _app()
    with app.app_context():
        _usuario_viejo('Leo@Gmail.com')
        for variante in ('leo@gmail.com', 'Leo@Gmail.com', 'LEO@GMAIL.COM'):
            usuario, error = verificar_credenciales(variante, PASSWORD)
            check(f"usuario viejo entra con '{variante}'", usuario is not None, error)


def test_no_se_puede_duplicar_con_otra_grafia():
    app = _app()
    with app.app_context():
        _usuario_viejo('Leo@Gmail.com')
        usuario, error = crear_usuario('leo@gmail.com', 'Otro', PASSWORD)
        check("registro duplicado rechazado", usuario is None and 'registrado' in (error or ''), error)
        check("sigue habiendo 1 usuario", User.query.count() == 1, User.query.count())


def test_reseteo_encuentra_usuario_viejo():
    app = _app()
    with app.app_context():
        _usuario_viejo('Leo@Gmail.com')
        token, error = generar_token_reset('leo@gmail.com')
        check("reseteo genera token para usuario viejo", token is not None and error is None, error)


def test_verificar_email_endpoint():
    app = _app()
    with app.app_context():
        _usuario_viejo('Leo@Gmail.com')
    client = app.test_client()
    resp = client.post('/auth/verificar-email', json={'email': 'leo@gmail.com'})
    check("verificar-email detecta cuenta existente (409)", resp.status_code == 409, resp.status_code)
    resp = client.post('/auth/verificar-email', json={'email': 'libre@gmail.com'})
    check("verificar-email email libre (200)", resp.status_code == 200, resp.status_code)


def test_login_http_usuario_viejo():
    app = _app()
    with app.app_context():
        _usuario_viejo('Leo@Gmail.com')
    client = app.test_client()
    resp = client.post('/auth/login', json={'email': 'leo@gmail.com', 'password': PASSWORD})
    check("POST /auth/login con minúsculas -> 200", resp.status_code == 200, resp.status_code)
    resp = client.post('/auth/login', json={'email': 'leo@gmail.com', 'password': 'Incorrecta1!'})
    check("contraseña mala sigue dando 401", resp.status_code == 401, resp.status_code)


def test_admin_otorga_creditos_a_usuario_viejo():
    app = _app()
    with app.app_context():
        _usuario_viejo('Leo@Gmail.com')
        admin = User(email='admin@foja.com', nombre='A', is_admin=True)
        admin.establecer_password(PASSWORD)
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True
    resp = client.post('/admin/otorgar-creditos', json={'email': 'Leo@Gmail.com', 'creditos': 3})
    check("admin encuentra cuenta con mayúsculas -> 200", resp.status_code == 200, resp.status_code)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE EMAIL SIN DISTINGUIR MAYÚSCULAS")
    print("=" * 70)

    test_registro_guarda_en_minusculas()
    test_login_cualquier_variante()
    test_usuario_viejo_con_mayusculas_puede_entrar()
    test_no_se_puede_duplicar_con_otra_grafia()
    test_reseteo_encuentra_usuario_viejo()
    test_verificar_email_endpoint()
    test_login_http_usuario_viejo()
    test_admin_otorga_creditos_a_usuario_viejo()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
