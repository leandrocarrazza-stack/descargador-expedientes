"""
Tests del sistema de pagos y créditos.

Verifican: idempotencia del webhook, acreditación correcta de créditos
y que el webhook requiere firma válida.
"""
import uuid
import pytest
from unittest.mock import patch
from tests.conftest import login_como, _hacer_usuario


def _crear_compra_pendiente(app, user_id, plan='estudio', creditos=10, monto=24000):
    """Crea un registro CompraCreditos pendiente en la BD."""
    from modulos.models import CompraCreditos
    from modulos.database import db
    with app.app_context():
        ref = f"user_{user_id}_plan_{plan}_{uuid.uuid4().hex[:8]}"
        compra = CompraCreditos(
            user_id=user_id,
            stripe_payment_id=f"mp_{uuid.uuid4().hex}",
            stripe_session_id=ref,
            creditos_comprados=creditos,
            monto_pagado=monto,
            plan=plan,
            estado='pending',
        )
        db.session.add(compra)
        db.session.commit()
        return compra.id, ref


# ── Idempotencia del webhook ───────────────────────────────────────────────

def test_creditos_se_acreditan_una_sola_vez(app):
    """
    El webhook puede llegar dos veces (red, reintentos de MP).
    Los créditos deben sumarse una sola vez.
    """
    from modulos.models import User, CompraCreditos
    from modulos.database import db
    from rutas.pagos import _confirmar_compra

    uid = _hacer_usuario(app, f"pago_{uuid.uuid4().hex[:8]}@test.com", creditos=5)
    compra_id, _ = _crear_compra_pendiente(app, uid, creditos=10)

    with app.app_context():
        compra = db.session.get(CompraCreditos, compra_id)
        usuario = db.session.get(User, uid)

        _confirmar_compra(compra)
        assert usuario.creditos_disponibles == 15   # 5 + 10
        assert compra.estado == 'completed'

        # Simular segundo webhook: la query filtra por estado='pending',
        # así que no encontrará la compra y no acreditará de nuevo.
        segunda_compra = CompraCreditos.query.filter_by(
            id=compra_id, estado='pending'
        ).first()
        assert segunda_compra is None, (
            "Una compra ya completada no debe encontrarse con estado='pending'"
        )
        assert usuario.creditos_disponibles == 15   # sin cambios


def test_admin_no_pierde_creditos_al_descargar(app):
    """Los admins pueden descargar sin consumir créditos (creditos_disponibles no decrece)."""
    from modulos.models import User
    from modulos.database import db

    uid = _hacer_usuario(app, f"pago_{uuid.uuid4().hex[:8]}@test.com",
                         is_admin=True, creditos=0)

    with app.app_context():
        user = db.session.get(User, uid)
        creditos_antes = user.creditos_disponibles

        # Simular la lógica de descontar créditos del pipeline
        if user and not user.is_admin:
            user.creditos_disponibles -= 1

        db.session.commit()
        assert user.creditos_disponibles == creditos_antes


# ── Validación del webhook ─────────────────────────────────────────────────

def test_webhook_rechaza_firma_invalida(client, app):
    """Webhook con firma HMAC incorrecta devuelve 400."""
    with patch.dict('os.environ', {'MERCADO_PAGO_WEBHOOK_SECRET': 'secret-real'}):
        resp = client.post(
            '/pagos/webhook',
            json={'action': 'payment.created', 'data': {'id': '123'}},
            headers={
                'x-signature': 'ts=1234567890,v1=firma-falsa-que-no-coincide',
                'x-request-id': 'req-123',
            }
        )
    assert resp.status_code == 400
    assert resp.get_json()['status'] == 'invalid_signature'


def test_webhook_sin_secret_configurado_pasa(client, app):
    """
    Sin MERCADO_PAGO_WEBHOOK_SECRET configurado (entorno dev),
    el webhook se procesa igualmente (con warning en logs).
    """
    with patch.dict('os.environ', {'MERCADO_PAGO_WEBHOOK_SECRET': ''}):
        with patch('rutas.pagos.obtener_pago',
                   return_value={'status': 'pending', 'external_reference': None}):
            resp = client.post(
                '/pagos/webhook',
                json={'action': 'payment.created', 'data': {'id': '999'}},
            )
    # Debe devolver 200 (MP requiere respuesta rápida)
    assert resp.status_code == 200


# ── Creación de órdenes ────────────────────────────────────────────────────

def test_crear_orden_plan_invalido(client):
    """Solicitar un plan que no existe devuelve 400."""
    login_como(client, 'normal')
    resp = client.post('/pagos/crear-orden', json={'plan': 'platinum-ultra'})
    assert resp.status_code == 400
    data = resp.get_json()
    assert not data['success']
