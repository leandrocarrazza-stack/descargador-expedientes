#!/usr/bin/env python3
"""
Tests del popup obligatorio de Términos (templates/_popup_terminos.html, incluido en base.html)
y de POST /terminos/aceptar. App real en modo testing (SQLite en memoria), login con `_user_id`.
`python tests/test_popup_terminos.py`
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
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
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    config.RESPONSABLE_NOMBRE = 'EJEMPLO Nombre'
    config.RESPONSABLE_CUIT = '20-00000000-0'
    config.RESPONSABLE_DOMICILIO = 'EJEMPLO 123'
    config.RESPONSABLE_EMAIL = 'ejemplo@example.com'
    config.TERMINOS_BORRADOR = False
    config.TERMINOS_VERSION = '2026-10'
    return app


def _usuario(app, email, version=None):
    with app.app_context():
        u = User(email=email, terminos_version=version)
        u.establecer_password('x')
        db.session.add(u)
        db.session.commit()
        return u.id


def _cliente(app, uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    return c


def _hay_popup(client, url='/planes'):
    return 'id="popupTerminos"' in client.get(url).get_data(as_text=True)


def test_popup():
    app = _app()
    check("visitante: sin popup", not _hay_popup(app.test_client(), '/'))

    cli = _cliente(app, _usuario(app, 'a@example.com'))
    html = cli.get('/planes').get_data(as_text=True)
    check("sin constancia: popup", 'id="popupTerminos"' in html)
    check("popup estático y sin cierre", 'data-bs-backdrop="static"' in html and 'data-bs-keyboard="false"' in html
          and 'popupTerminos' in html and 'data-bs-dismiss' not in html.split('id="popupTerminos"', 1)[1].split('<script>', 1)[0])

    cli = _cliente(app, _usuario(app, 'b@example.com', version='2026-10'))
    check("versión vigente: sin popup", not _hay_popup(cli))
    config.TERMINOS_VERSION = '2027-01'
    check("cambia la versión: vuelve el popup", _hay_popup(cli))
    config.TERMINOS_VERSION = '2026-10'

    config.TERMINOS_BORRADOR = True
    cli = _cliente(app, _usuario(app, 'c@example.com'))
    check("borrador: sin popup", not _hay_popup(cli))
    config.TERMINOS_BORRADOR = False


def test_aceptar():
    app = _app()
    check("sin sesión no acepta", app.test_client().post('/terminos/aceptar').status_code in (301, 302, 401))
    uid = _usuario(app, 'd@example.com')
    cli = _cliente(app, uid)
    r = cli.post('/terminos/aceptar', headers={'X-Forwarded-For': '203.0.113.9, 10.0.0.1'})
    check("acepta: 200", r.status_code == 200, r.status_code)
    with app.app_context():
        u = db.session.get(User, uid)
        check("graba versión", u.terminos_version == '2026-10')
        check("graba fecha", u.terminos_aceptados_en is not None)
        check("graba IP del cliente", u.terminos_aceptados_ip == '203.0.113.9', u.terminos_aceptados_ip)
    check("ya no aparece el popup", not _hay_popup(cli))
    config.TERMINOS_BORRADOR = True
    check("en borrador no acepta", cli.post('/terminos/aceptar').status_code == 409)
    config.TERMINOS_BORRADOR = False


if __name__ == '__main__':
    test_popup()
    test_aceptar()
    print("\nFALLAS: %s" % _fallos if _fallos else "\nTodo OK")
    sys.exit(1 if _fallos else 0)
