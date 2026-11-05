#!/usr/bin/env python3
"""
Tests de la acreditación de compras con Mercado Pago (rutas/pagos.py y
validar_firma_webhook de modulos/mercado_pago.py).
`python -m pytest tests/test_pagos_mp.py`
"""

import hashlib
import hmac
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
config.MAIL_DEFAULT_SENDER = 'foja@example.com'
config.TESTING = True

import pytest

from modulos.database import db
from modulos.models import User, CompraCreditos
from modulos import mercado_pago
from servidor import crear_app
import rutas.pagos as pagos

REF = 'user_1_plan_estudio_abcd1234'


@pytest.fixture
def app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['RATELIMIT_ENABLED'] = False
    with app.app_context():
        db.drop_all()
        db.create_all()
        u = User(email='cliente@gmail.com', creditos_disponibles=0)
        u.establecer_password('x')
        db.session.add(u)
        db.session.commit()
        db.session.add(CompraCreditos(user_id=u.id, stripe_payment_id='pref-1',
                                      stripe_session_id=REF, creditos_comprados=10,
                                      monto_pagado=24000, plan='estudio', estado='pending'))
        db.session.commit()
        yield app


def _pago(status='approved', monto=24000, ref=REF):
    return {'id': 123456789, 'status': status, 'external_reference': ref,
            'transaction_amount': monto, 'currency_id': 'ARS'}


def _usuario():
    return User.query.filter_by(email='cliente@gmail.com').first()


def _firmar(secret, data_id, request_id, ts='1700000000'):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


def test_firma_con_plantilla_oficial(monkeypatch):
    monkeypatch.setenv('MERCADO_PAGO_WEBHOOK_SECRET', 's3cr3t')
    firma = _firmar('s3cr3t', '123456789', 'req-1')
    assert mercado_pago.validar_firma_webhook(firma, 'req-1', '123456789')
    assert not mercado_pago.validar_firma_webhook(firma, 'req-1', '999')
    assert not mercado_pago.validar_firma_webhook(_firmar('otro', '123456789', 'req-1'), 'req-1', '123456789')


def test_webhook_acredita_pago_aprobado(app, monkeypatch):
    monkeypatch.setenv('MERCADO_PAGO_WEBHOOK_SECRET', 's3cr3t')
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago())
    c = app.test_client()
    r = c.post('/pagos/webhook?data.id=123456789&type=payment',
               json={'action': 'payment.created', 'type': 'payment', 'data': {'id': '123456789'}},
               headers={'x-signature': _firmar('s3cr3t', '123456789', 'req-1'), 'x-request-id': 'req-1'})
    assert r.status_code == 200
    assert _usuario().creditos_disponibles == 10
    assert _usuario().plan_max_comprado == 'estudio'
    assert CompraCreditos.query.first().estado == 'completed'


def test_webhook_formato_ipn(app, monkeypatch):
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago())
    r = app.test_client().post('/pagos/webhook?topic=payment&id=123456789')
    assert r.status_code == 200
    assert _usuario().creditos_disponibles == 10


def test_webhook_firma_invalida_igual_verifica_contra_api(app, monkeypatch):
    """Un secreto mal cargado no puede hacer perder un pago real."""
    monkeypatch.setenv('MERCADO_PAGO_WEBHOOK_SECRET', 'secreto-equivocado')
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago())
    r = app.test_client().post('/pagos/webhook?data.id=123456789&type=payment',
                               json={'action': 'payment.updated', 'data': {'id': '123456789'}},
                               headers={'x-signature': _firmar('s3cr3t', '123456789', 'r'), 'x-request-id': 'r'})
    assert r.status_code == 200
    assert _usuario().creditos_disponibles == 10


def test_webhook_no_acredita_pago_no_aprobado_ni_monto_menor(app, monkeypatch):
    c = app.test_client()
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago(status='pending'))
    c.post('/pagos/webhook?data.id=1&type=payment', json={'action': 'payment.created', 'data': {'id': '1'}})
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago(monto=100))
    c.post('/pagos/webhook?data.id=1&type=payment', json={'action': 'payment.updated', 'data': {'id': '1'}})
    assert _usuario().creditos_disponibles == 0
    assert CompraCreditos.query.first().estado == 'pending'


def test_webhook_error_de_api_pide_reintento(app, monkeypatch):
    def falla(pid):
        raise mercado_pago.MercadoPagoError('timeout')
    monkeypatch.setattr(pagos, 'obtener_pago', falla)
    r = app.test_client().post('/pagos/webhook?data.id=1&type=payment', json={'action': 'payment.created', 'data': {'id': '1'}})
    assert r.status_code == 500


def test_no_se_acredita_dos_veces(app, monkeypatch):
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago())
    c = app.test_client()
    for _ in range(3):
        c.post('/pagos/webhook?data.id=123456789&type=payment', json={'action': 'payment.updated', 'data': {'id': '123456789'}})
    assert _usuario().creditos_disponibles == 10


def _login(c):
    with c.session_transaction() as s:
        s['_user_id'] = str(_usuario().id)
        s['_fresh'] = True


def test_redirect_falsificado_no_regala_creditos(app, monkeypatch):
    """Antes bastaba con abrir ?status=approved&external_reference=... para cobrar créditos."""
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago(status='rejected'))
    monkeypatch.setattr(pagos, 'buscar_pagos_por_referencia', lambda ref: [])
    c = app.test_client()
    _login(c)
    c.get(f'/pagos/pago-confirmado?status=approved&external_reference={REF}')
    c.get(f'/pagos/pago-confirmado?status=approved&external_reference={REF}&payment_id=5')
    assert _usuario().creditos_disponibles == 0


def test_redirect_acredita_si_mp_confirma(app, monkeypatch):
    monkeypatch.setattr(pagos, 'obtener_pago', lambda pid: _pago())
    c = app.test_client()
    _login(c)
    r = c.get(f'/pagos/pago-confirmado?status=approved&external_reference={REF}&payment_id=123456789')
    assert r.status_code == 200
    assert _usuario().creditos_disponibles == 10
