#!/usr/bin/env python3
"""
Tests de la baja de cuenta (modulos/baja_cuenta.py y scripts/eliminar_cuenta.py):
borra PDF, historial, sesión de Mesa Virtual, tokens y mensajes; conserva las
compras anonimizando la cuenta; no toca a otros usuarios; rechaza a los
administradores; y sin --confirmar no modifica nada.
`python tests/test_baja_cuenta.py` o `pytest tests/test_baja_cuenta.py`.
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from flask import Flask

import config
from modulos.baja_cuenta import BajaNoPermitida, dar_de_baja, resumen_baja
from modulos.database import db
from modulos.models import (
    CompraCreditos, ExpedienteDescargado, MensajeContacto,
    SesionUsuarioMV, TokenResetPassword, User,
)
from modulos.storage import StorageLocal


@pytest.fixture
def entorno():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with tempfile.TemporaryDirectory() as tmp, app.app_context():
        db.create_all()
        tmp = Path(tmp)
        storage = StorageLocal(tmp / 'store')
        output = tmp / 'output'
        output.mkdir()
        yield app, storage, output
        db.session.remove()
        db.drop_all()


def _usuario(email, con_datos=True, compras=0, admin=False, storage=None, output=None):
    """Crea un usuario con historial, PDF, sesión de MV, token y mensaje."""
    u = User(email=email, nombre='Ana', plan='estudio', is_admin=admin, creditos_disponibles=5,
             notificar_email=True, recibir_promociones=True,
             terminos_version='2026-10', terminos_aceptados_en=datetime.utcnow(),
             terminos_aceptados_ip='203.0.113.7')
    u.establecer_password('Password123!')
    db.session.add(u)
    db.session.commit()
    if con_datos:
        key = f"usuarios/{u.id}/expedientes/123_2024-abc.pdf"
        origen = storage.base_dir.parent / f"origen_{u.id}.pdf"
        origen.write_bytes(b'%PDF-1.4 prueba')
        storage.guardar(str(origen), key)
        cache = output / f"cache_{u.id}.pdf"
        cache.write_bytes(b'%PDF-1.4 cache')
        exp = ExpedienteDescargado(user_id=u.id, numero='123/2024', estado='completed',
                                   storage_key=key, pdf_ruta_temporal=str(cache))
        db.session.add(exp)
        db.session.commit()
        (output / f"recuperado_{exp.id}.pdf").write_bytes(b'%PDF-1.4 recuperado')
        # Un PDF huérfano: sin fila en la base, pero bajo el prefijo del usuario
        storage.guardar(str(origen), f"usuarios/{u.id}/expedientes/huerfano.pdf")
        sesion = SesionUsuarioMV(user_id=u.id, cookies_json='{}', mv_usuario='ana')
        db.session.add(sesion)
        db.session.add(TokenResetPassword(user_id=u.id, token=f"tok-{u.id}",
                                          expira_en=datetime.utcnow() + timedelta(hours=1)))
        db.session.add(MensajeContacto(user_id=u.id, nombre='Ana', email=email, asunto='Otro',
                                       mensaje='Hola, necesito ayuda con mi cuenta.'))
        # Un mensaje enviado sin sesión iniciada, con el mismo correo
        db.session.add(MensajeContacto(user_id=None, nombre='Ana', email=email.upper(), asunto='Otro',
                                       mensaje='Mensaje enviado sin iniciar sesión.'))
    for i in range(compras):
        db.session.add(CompraCreditos(user_id=u.id, stripe_payment_id=f"pago-{u.id}-{i}",
                                      creditos_comprados=10, monto_pagado=24000.0, plan='estudio',
                                      estado='completed'))
    db.session.commit()
    return u


def _filas(user_id):
    return {
        'expedientes': ExpedienteDescargado.query.filter_by(user_id=user_id).count(),
        'sesion': SesionUsuarioMV.query.filter_by(user_id=user_id).count(),
        'tokens': TokenResetPassword.query.filter_by(user_id=user_id).count(),
        'compras': CompraCreditos.query.filter_by(user_id=user_id).count(),
    }


def test_baja_sin_compras_elimina_todo(entorno):
    _, storage, output = entorno
    u = _usuario('ana@ejemplo.com', storage=storage, output=output)
    otro = _usuario('otro@ejemplo.com', storage=storage, output=output)
    uid, oid = u.id, otro.id

    r = dar_de_baja(u, storage=storage, output_dir=output)

    assert r['resultado'] == 'eliminada'
    assert r['keys_restantes'] == []
    assert db.session.get(User, uid) is None
    assert _filas(uid) == {'expedientes': 0, 'sesion': 0, 'tokens': 0, 'compras': 0}
    assert MensajeContacto.query.filter(MensajeContacto.email.ilike('ana@ejemplo.com')).count() == 0
    assert not (storage.base_dir / f"usuarios/{uid}").exists()
    assert not (output / f"cache_{uid}.pdf").exists()
    assert (output / f"cache_{oid}.pdf").exists()  # el caché del otro usuario sigue
    # El otro usuario queda intacto (incluido el prefijo usuarios/1 vs usuarios/10)
    assert db.session.get(User, oid) is not None
    assert storage.existe(f"usuarios/{oid}/expedientes/123_2024-abc.pdf")
    assert storage.existe(f"usuarios/{oid}/expedientes/huerfano.pdf")
    assert _filas(oid)['expedientes'] == 1 and _filas(oid)['sesion'] == 1
    assert MensajeContacto.query.filter(MensajeContacto.email.ilike('otro@ejemplo.com')).count() == 2


def test_baja_con_compras_anonimiza_y_conserva_las_compras(entorno):
    _, storage, output = entorno
    u = _usuario('ana@ejemplo.com', compras=2, storage=storage, output=output)
    uid = u.id

    r = dar_de_baja(u, storage=storage, output_dir=output)

    assert r['resultado'] == 'anonimizada'
    u = db.session.get(User, uid)
    assert u.email == f"baja-{uid}@foja.invalid"
    assert u.nombre is None and u.terminos_aceptados_ip is None
    assert u.creditos_disponibles == 0 and u.plan == 'free'
    assert u.notificar_email is False and u.recibir_promociones is False
    assert not u.verificar_password('Password123!')
    assert _filas(uid) == {'expedientes': 0, 'sesion': 0, 'tokens': 0, 'compras': 2}
    assert not (storage.base_dir / f"usuarios/{uid}").exists()


def test_borrar_compras_elimina_la_cuenta_entera(entorno):
    _, storage, output = entorno
    u = _usuario('ana@ejemplo.com', compras=2, storage=storage, output=output)
    uid = u.id

    r = dar_de_baja(u, borrar_compras=True, storage=storage, output_dir=output)

    assert r['resultado'] == 'eliminada'
    assert db.session.get(User, uid) is None
    assert CompraCreditos.query.filter_by(user_id=uid).count() == 0


def test_cuenta_sin_datos_asociados(entorno):
    _, storage, output = entorno
    u = _usuario('vacia@ejemplo.com', con_datos=False, storage=storage, output=output)
    uid = u.id
    assert dar_de_baja(u, storage=storage, output_dir=output)['resultado'] == 'eliminada'
    assert db.session.get(User, uid) is None


def test_rechaza_administradores(entorno):
    _, storage, output = entorno
    u = _usuario('admin@ejemplo.com', admin=True, storage=storage, output=output)
    with pytest.raises(BajaNoPermitida):
        dar_de_baja(u, storage=storage, output_dir=output)
    assert db.session.get(User, u.id) is not None
    assert storage.existe(f"usuarios/{u.id}/expedientes/123_2024-abc.pdf")


def test_resumen_no_modifica_nada(entorno):
    _, storage, output = entorno
    u = _usuario('ana@ejemplo.com', compras=1, storage=storage, output=output)
    resumen = resumen_baja(u, storage=storage, output_dir=output)
    assert resumen['expedientes'] == 1 and resumen['compras'] == 1
    assert len(resumen['keys_storage']) == 2 and len(resumen['archivos_locales']) == 2
    assert resumen['sesion_mv'] == 1 and resumen['tokens_reset'] == 1 and resumen['mensajes_contacto'] == 2
    assert db.session.get(User, u.id).email == 'ana@ejemplo.com'
    assert storage.existe(f"usuarios/{u.id}/expedientes/huerfano.pdf")


def test_no_borra_archivos_fuera_de_output(entorno, tmp_path):
    """Una ruta de pdf_ruta_temporal fuera de output/ no se toca."""
    _, storage, output = entorno
    u = _usuario('ana@ejemplo.com', storage=storage, output=output)
    ajeno = tmp_path / 'importante.pdf'
    ajeno.write_bytes(b'no tocar')
    exp = ExpedienteDescargado.query.filter_by(user_id=u.id).first()
    exp.pdf_ruta_temporal = str(ajeno)
    db.session.commit()
    dar_de_baja(u, storage=storage, output_dir=output)
    assert ajeno.exists()


def test_cli_sin_confirmar_no_modifica_nada(entorno, monkeypatch, capsys):
    app, storage, output = entorno
    import scripts.eliminar_cuenta as cli
    import modulos.baja_cuenta as baja
    monkeypatch.setattr(cli, '_crear_app_minima', lambda: app)
    monkeypatch.setattr(baja, 'storage_pdf', lambda: storage)
    monkeypatch.setattr(config, 'OUTPUT_DIR', output)
    u = _usuario('ana@ejemplo.com', storage=storage, output=output)

    assert cli.main(['ana@ejemplo.com']) == 0
    salida = capsys.readouterr().out
    assert 'No se modificó nada' in salida
    assert db.session.get(User, u.id) is not None

    uid = u.id
    assert cli.main(['ana@ejemplo.com', '--confirmar']) == 0
    db.session.expire_all()  # el CLI usó otra sesión: no mirar el caché de esta
    assert db.session.get(User, uid) is None
    assert cli.main(['no-existe@ejemplo.com', '--confirmar']) == 1
