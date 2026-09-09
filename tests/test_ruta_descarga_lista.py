#!/usr/bin/env python3
"""
Tests de GET /descargas/expediente/<id>/listo: la página de confirmación a
la que apunta el link del email de aviso (en vez de la descarga directa).
`python test_ruta_descarga_lista.py`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
config.TESTING = True

from modulos.database import db
from modulos.models import User, ExpedienteDescargado
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
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def test_muestra_el_expediente_al_dueno():
    app = _app()
    with app.app_context():
        user = User(email='listo@foja.com', creditos_disponibles=5)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        exp = ExpedienteDescargado(
            user_id=user.id, numero='1234/2024', tribunal='Juzgado Civil',
            estado='completed', total_archivos=3, completado_en=datetime.utcnow(),
        )
        db.session.add(exp)
        db.session.commit()
        user_id, exp_id = user.id, exp.id

    client = app.test_client()
    _login(client, user_id)
    resp = client.get(f'/descargas/expediente/{exp_id}/listo')
    check("200 al dueño del expediente", resp.status_code == 200, resp.status_code)
    body = resp.get_data(as_text=True)
    check("muestra el número de expediente", '1234/2024' in body)
    check("trae el link real de descarga del PDF", f'/descargas/expediente/{exp_id}/descargar' in body)


def test_otro_usuario_da_403():
    app = _app()
    with app.app_context():
        dueno = User(email='dueno@foja.com', creditos_disponibles=5)
        dueno.establecer_password('Password123!')
        otro = User(email='otro@foja.com', creditos_disponibles=5)
        otro.establecer_password('Password123!')
        db.session.add_all([dueno, otro])
        db.session.commit()
        exp = ExpedienteDescargado(user_id=dueno.id, numero='1/24', estado='completed')
        db.session.add(exp)
        db.session.commit()
        otro_id, exp_id = otro.id, exp.id

    client = app.test_client()
    _login(client, otro_id)
    resp = client.get(f'/descargas/expediente/{exp_id}/listo')
    check("403 si no es el dueño", resp.status_code == 403, resp.status_code)


def test_inexistente_da_404():
    app = _app()
    with app.app_context():
        user = User(email='u404@foja.com', creditos_disponibles=5)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)
    resp = client.get('/descargas/expediente/99999/listo')
    check("404 si el expediente no existe", resp.status_code == 404, resp.status_code)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE GET /descargas/expediente/<id>/listo")
    print("=" * 70)

    test_muestra_el_expediente_al_dueno()
    test_otro_usuario_da_403()
    test_inexistente_da_404()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
