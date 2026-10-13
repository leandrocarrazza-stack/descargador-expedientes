#!/usr/bin/env python3
"""
Tests de las validaciones de la descarga parcial "desde una fecha":
POST /descargas/expediente rechaza fechas inválidas/futuras, y
POST /descargas/expediente/<id>/actualizar rechaza registros parciales.

Mismo esquema que tests/test_rutas_actualizar.py (BD en memoria, sin Selenium).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
config.TESTING = True
config.MAIL_DEFAULT_SENDER = 'foja@example.com'

from modulos.database import db
from modulos.models import User, ExpedienteDescargado
from servidor import crear_app


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _usuario(app, email):
    with app.app_context():
        user = User(email=email, creditos_disponibles=5, plan_max_comprado='estudio')
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        return user.id


def test_fecha_con_formato_invalido_da_400():
    app = _app()
    client = app.test_client()
    _login(client, _usuario(app, 'f1@foja.com'))
    resp = client.post('/descargas/expediente', json={'numero_expediente': '1/24', 'desde_fecha': '01/03/2024'})
    assert resp.status_code == 400
    assert 'inválida' in resp.get_json()['mensaje']


def test_fecha_futura_da_400():
    app = _app()
    client = app.test_client()
    _login(client, _usuario(app, 'f2@foja.com'))
    manana = (date.today() + timedelta(days=1)).isoformat()
    resp = client.post('/descargas/expediente', json={'numero_expediente': '1/24', 'desde_fecha': manana})
    assert resp.status_code == 400
    assert 'futura' in resp.get_json()['mensaje']


def test_actualizar_un_registro_parcial_da_400():
    app = _app()
    user_id = _usuario(app, 'f3@foja.com')
    with app.app_context():
        exp = ExpedienteDescargado(
            user_id=user_id, numero='1/24', estado='completed',
            storage_key='usuarios/1/expedientes/1-x.pdf', total_filas=10,
            desde_fecha=date(2024, 3, 1),
        )
        db.session.add(exp)
        db.session.commit()
        exp_id = exp.id

    client = app.test_client()
    _login(client, user_id)
    resp = client.post(f'/descargas/expediente/{exp_id}/actualizar')
    assert resp.status_code == 400
    assert 'parciales' in resp.get_json()['mensaje']


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            try:
                fn()
                print(f"  OK  {nombre}")
            except Exception as e:
                fallos += 1
                print(f" FALLA {nombre} -> {e!r}")
    sys.exit(1 if fallos else 0)
