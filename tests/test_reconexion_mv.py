#!/usr/bin/env python3
"""
Tests de la reconexión a Mesa Virtual:
  - El redirect a mv-login cuando no hay sesión reenvía el query string
    original (?numero=..., ?actualizar=...), para no perderlo.
  - Cuando el pipeline detecta 'auth_failed', se invalida la fila de
    SesionUsuarioMV para que la próxima visita no siga mostrando
    "Mesa Virtual conectada" con cookies ya muertas.

`python test_reconexion_mv.py`.
"""

import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
config.TESTING = True
config.MAIL_DEFAULT_SENDER = 'foja@example.com'

import rutas.descargas as descargas_mod
from modulos.database import db
from modulos.models import User, SesionUsuarioMV
from modulos.pipeline import ResultadoPipeline
from modulos.storage import StorageLocal
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


def test_redirect_reenvia_el_querystring():
    app = _app()
    with app.app_context():
        user = User(email='sinmv@foja.com', creditos_disponibles=5)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    # Sin fila de SesionUsuarioMV: el GET debe redirigir a mv-login.

    client = app.test_client()
    _login(client, user_id)
    resp = client.get('/descargas/expediente?numero=1234%2F24')
    check("redirige (302) cuando no hay sesión MV", resp.status_code == 302, resp.status_code)

    location = resp.headers.get('Location', '')
    parsed = urlparse(location)
    check("redirige a mv-login", '/auth/mv-login' in parsed.path, location)

    next_val = parse_qs(parsed.query).get('next', [''])[0]
    check("el 'next' reenvía el número original", 'numero=1234' in unquote(next_val), next_val)


def test_auth_failed_invalida_la_sesion():
    app = _app()
    with app.app_context():
        user = User(email='expirada@foja.com', creditos_disponibles=5, notificar_email=False)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        db.session.add(SesionUsuarioMV(user_id=user.id, cookies_json='[{"name":"x","value":"y"}]', mv_usuario='mv1'))
        db.session.commit()
        user_id = user.id

        check("la sesión existe antes del job", SesionUsuarioMV.query.filter_by(user_id=user_id).first() is not None)

    resultado = ResultadoPipeline(exito=False, tipo_error='auth_failed', error='Tu sesión de Mesa Virtual expiró.')

    class _PipelineFalso:
        def ejecutar(self, **kwargs):
            return resultado

    class _ControlFalso:
        def liberar_todo(self):
            pass

    original_esperar = descargas_mod.gestor.esperar_turno
    original_pipeline = descargas_mod.PipelineDescargador
    original_storage = descargas_mod.storage_pdf

    with tempfile.TemporaryDirectory() as tmp:
        try:
            descargas_mod.gestor.esperar_turno = lambda entrada, on_posicion=None, **kw: _ControlFalso()
            descargas_mod.PipelineDescargador = _PipelineFalso
            descargas_mod.storage_pdf = lambda: StorageLocal(Path(tmp))

            with app.app_context():
                descargas_mod._run_pipeline(
                    app, 'job-auth-failed', user_id, '1234/2024', None, {'cookie': 'x'},
                    entrada=object(), notificar_email=False,
                )
        finally:
            descargas_mod.gestor.esperar_turno = original_esperar
            descargas_mod.PipelineDescargador = original_pipeline
            descargas_mod.storage_pdf = original_storage

    with app.app_context():
        check("la sesión se borró tras auth_failed",
              SesionUsuarioMV.query.filter_by(user_id=user_id).first() is None)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE RECONEXIÓN A MESA VIRTUAL")
    print("=" * 70)

    test_redirect_reenvia_el_querystring()
    test_auth_failed_invalida_la_sesion()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
