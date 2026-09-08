#!/usr/bin/env python3
"""
Tests del lote 4 (página principal unificada): / y /dashboard redirigen a
Descargar, el historial no ofrece "Descargar PDF" para expedientes sin
storage_key ni archivo local, y no queda el botón "Reintentar" muerto.
`python test_rutas_pagina_principal.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.models import User, ExpedienteDescargado, SesionUsuarioMV
from servidor import crear_app

_fallos = []


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


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def test_index_y_dashboard_redirigen_a_descargar():
    app = _app()
    with app.app_context():
        user = User(email='u1@foja.com')
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)

    r = client.get('/', follow_redirects=False)
    check("/ (autenticado) redirige a /descargas/expediente",
          r.status_code == 302 and r.headers['Location'].endswith('/descargas/expediente'), r.headers.get('Location'))

    r = client.get('/dashboard', follow_redirects=False)
    check("/dashboard redirige 301 a /descargas/expediente",
          r.status_code == 301 and r.headers['Location'].endswith('/descargas/expediente'), r.headers.get('Location'))


def test_historial_no_ofrece_pdf_sin_archivo():
    app = _app()
    with app.app_context():
        user = User(email='u2@foja.com')
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

        # completed pero sin storage_key ni archivo local: no debería ofrecer descarga
        db.session.add(ExpedienteDescargado(user_id=user_id, numero='111/24', estado='completed'))
        # completed con storage_key: sí debería ofrecerla
        db.session.add(ExpedienteDescargado(user_id=user_id, numero='222/24', estado='completed', storage_key='k'))
        # failed: debería ofrecer el link de "descargar de nuevo", no un botón muerto
        db.session.add(ExpedienteDescargado(user_id=user_id, numero='333/24', estado='failed', error_msg='x'))
        db.session.commit()

    client = app.test_client()
    _login(client, user_id)

    resp = client.get('/descargas/historial')
    check("historial -> 200", resp.status_code == 200, resp.status_code)
    body = resp.data.decode()

    check("no hay botón 'Reintentar descarga' sin acción",
          'title="Reintentar descarga"' not in body)
    check("hay un link de 'Descargar de nuevo' para el fallido",
          'numero=333' in body or 'numero=333%2F24' in body)
    apariciones = body.count('<div class="col-num">')
    check("aparecen los 3 expedientes", apariciones == 3, apariciones)


def test_pagina_principal_muestra_ultimas_descargas():
    app = _app()
    with app.app_context():
        user = User(email='u3@foja.com', creditos_disponibles=2)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        db.session.add(SesionUsuarioMV(user_id=user.id, cookies_json='{}', mv_usuario='mv1'))
        db.session.add(ExpedienteDescargado(user_id=user.id, numero='999/24', caratula='Test c/ Test', estado='completed', storage_key='k'))
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)

    resp = client.get('/descargas/expediente')
    check("descargar/expediente -> 200", resp.status_code == 200, resp.status_code)
    body = resp.data.decode()
    check("muestra 'Últimas descargas'", 'Últimas descargas' in body)
    check("muestra el número del expediente reciente", '999/24' in body)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE PÁGINA PRINCIPAL UNIFICADA (LOTE 4)")
    print("=" * 70)

    test_index_y_dashboard_redirigen_a_descargar()
    test_historial_no_ofrece_pdf_sin_archivo()
    test_pagina_principal_muestra_ultimas_descargas()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
