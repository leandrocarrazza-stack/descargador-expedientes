#!/usr/bin/env python3
"""
Tests de /pagos/pago-confirmado: los créditos solo se acreditan si Mercado Pago
confirma el pago y la compra es del usuario logueado (la URL la controla el
usuario, no se le puede creer). `python test_rutas_pagos.py`.
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
# Ver tests/test_rutas_admin.py: pisar esto ANTES de importar servidor.
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.models import User, CompraCreditos
from modulos.mercado_pago import MercadoPagoError
from servidor import crear_app

_fallos = []

REF = 'user_1_plan_estudio_ab12cd34'


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


def _preparar(app):
    """Crea 2 usuarios y una compra pendiente (plan estudio, 10 créditos) del primero."""
    with app.app_context():
        ids = []
        for email in ('dueno@foja.com', 'otro@foja.com'):
            u = User(email=email, nombre='X')
            u.establecer_password('Password123!')
            db.session.add(u)
            db.session.commit()
            ids.append(u.id)
        db.session.add(CompraCreditos(
            user_id=ids[0], stripe_payment_id='pref-1', stripe_session_id=REF,
            creditos_comprados=10, monto_pagado=24000, plan='estudio', estado='pending',
        ))
        db.session.commit()
    return ids


def _creditos(app, user_id):
    with app.app_context():
        return db.session.get(User, user_id).creditos_disponibles


URL = f'/pagos/pago-confirmado?status=approved&external_reference={REF}&payment_id=999'


def test_url_falsa_sin_pago_real_no_acredita():
    """MP dice que el pago no está aprobado: la URL no alcanza."""
    app = _app()
    dueno, _ = _preparar(app)
    client = app.test_client()
    _login(client, dueno)
    with mock.patch('rutas.pagos.obtener_pago', return_value={'status': 'rejected', 'external_reference': REF}):
        client.get(URL)
    check("pago no aprobado en MP -> 0 créditos", _creditos(app, dueno) == 0, _creditos(app, dueno))


def test_sin_payment_id_no_acredita():
    app = _app()
    dueno, _ = _preparar(app)
    client = app.test_client()
    _login(client, dueno)
    with mock.patch('rutas.pagos.obtener_pago') as m:
        client.get(f'/pagos/pago-confirmado?status=approved&external_reference={REF}')
        check("sin payment_id no consulta a MP", not m.called)
    check("sin payment_id -> 0 créditos", _creditos(app, dueno) == 0, _creditos(app, dueno))


def test_error_de_mp_no_acredita():
    app = _app()
    dueno, _ = _preparar(app)
    client = app.test_client()
    _login(client, dueno)
    with mock.patch('rutas.pagos.obtener_pago', side_effect=MercadoPagoError('caído')):
        resp = client.get(URL)
    check("error de MP -> la página igual responde 200", resp.status_code == 200, resp.status_code)
    check("error de MP -> 0 créditos", _creditos(app, dueno) == 0, _creditos(app, dueno))


def test_referencia_no_coincide_no_acredita():
    """Un pago aprobado real pero de OTRA compra no sirve para esta referencia."""
    app = _app()
    dueno, _ = _preparar(app)
    client = app.test_client()
    _login(client, dueno)
    with mock.patch('rutas.pagos.obtener_pago', return_value={'status': 'approved', 'external_reference': 'otra_ref'}):
        client.get(URL)
    check("referencia distinta -> 0 créditos", _creditos(app, dueno) == 0, _creditos(app, dueno))


def test_otro_usuario_no_puede_acreditar_compra_ajena():
    app = _app()
    dueno, otro = _preparar(app)
    client = app.test_client()
    _login(client, otro)
    with mock.patch('rutas.pagos.obtener_pago', return_value={'status': 'approved', 'external_reference': REF}):
        client.get(URL)
    check("otro usuario -> el dueño sigue con 0", _creditos(app, dueno) == 0, _creditos(app, dueno))
    check("otro usuario -> el intruso no cobra", _creditos(app, otro) == 0, _creditos(app, otro))


def test_pago_real_aprobado_acredita():
    app = _app()
    dueno, _ = _preparar(app)
    client = app.test_client()
    _login(client, dueno)
    with mock.patch('rutas.pagos.obtener_pago', return_value={'status': 'approved', 'external_reference': REF}):
        client.get(URL)
    check("pago aprobado por MP -> 10 créditos", _creditos(app, dueno) == 10, _creditos(app, dueno))


def test_doble_confirmacion_acredita_una_sola_vez():
    """Webhook + redirect sobre la misma compra: los créditos se suman una vez."""
    from rutas.pagos import _confirmar_compra
    app = _app()
    dueno, _ = _preparar(app)
    with app.app_context():
        compra = CompraCreditos.query.filter_by(stripe_session_id=REF).first()
        primera = _confirmar_compra(compra)
        segunda = _confirmar_compra(compra)
    check("primera confirmación acredita", primera is True)
    check("segunda confirmación no acredita", segunda is False)
    check("créditos sumados una sola vez (10)", _creditos(app, dueno) == 10, _creditos(app, dueno))


def test_carrera_dos_copias_desactualizadas():
    """
    Simula la carrera: dos requests leyeron la compra como 'pending' ANTES de
    que cualquiera la confirmara (objetos separados, ambos con estado viejo).
    """
    from rutas.pagos import _confirmar_compra
    app = _app()
    dueno, _ = _preparar(app)
    with app.app_context():
        copia_a = CompraCreditos.query.filter_by(stripe_session_id=REF).first()
        db.session.expunge(copia_a)
        copia_b = CompraCreditos.query.filter_by(stripe_session_id=REF).first()
        db.session.expunge(copia_b)
        check("ambas copias ven 'pending'", copia_a.estado == 'pending' and copia_b.estado == 'pending')
        db.session.add(copia_a)
        resultados = [_confirmar_compra(copia_a)]
        db.session.expunge(copia_a)
        db.session.add(copia_b)
        resultados.append(_confirmar_compra(copia_b))
    check("solo una de las dos acredita", sorted(resultados) == [False, True], resultados)
    check("créditos = 10, no 20", _creditos(app, dueno) == 10, _creditos(app, dueno))


def test_webhook_y_redirect_juntos_no_duplican():
    """Flujo real: primero pago-confirmado acredita, después llega el webhook."""
    app = _app()
    dueno, _ = _preparar(app)
    client = app.test_client()
    _login(client, dueno)
    pago = {'status': 'approved', 'external_reference': REF}
    with mock.patch('rutas.pagos.obtener_pago', return_value=pago):
        client.get(URL)
        client.post('/pagos/webhook', json={'action': 'payment.updated', 'data': {'id': '999'}})
    check("redirect + webhook -> 10 créditos", _creditos(app, dueno) == 10, _creditos(app, dueno))


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE PAGO-CONFIRMADO")
    print("=" * 70)

    test_url_falsa_sin_pago_real_no_acredita()
    test_sin_payment_id_no_acredita()
    test_error_de_mp_no_acredita()
    test_referencia_no_coincide_no_acredita()
    test_otro_usuario_no_puede_acreditar_compra_ajena()
    test_pago_real_aprobado_acredita()
    test_doble_confirmacion_acredita_una_sola_vez()
    test_carrera_dos_copias_desactualizadas()
    test_webhook_y_redirect_juntos_no_duplican()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
