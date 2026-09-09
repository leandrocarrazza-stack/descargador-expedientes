#!/usr/bin/env python3
"""
Tests de POST /descargas/expediente/<id>/actualizar (lote 5): validaciones
de la ruta (400/402/403/409) y el flujo completo 202 con el pipeline
mockeado, incluyendo que 'sin_novedades' no descuenta crédito.

Sigue el patrón de tests/test_emails_descarga.py: PipelineDescargador y
gestor.esperar_turno se parchean para no tocar Selenium ni la cola real de
concurrencia; storage_pdf() se redirige a un backend local en un
directorio temporal. `python test_rutas_actualizar.py`.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
config.TESTING = True
config.MAIL_DEFAULT_SENDER = 'foja@example.com'

import rutas.descargas as descargas_mod
from modulos.database import db
from modulos.models import User, ExpedienteDescargado, SesionUsuarioMV
from modulos.pipeline import ResultadoPipeline
from modulos.storage import StorageLocal
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


class _ControlFalso:
    def liberar_todo(self):
        pass


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _crear_expediente_actualizable(app, plan_max_comprado='estudio', creditos=5, storage_tmp=None):
    with app.app_context():
        user = User(email='act@foja.com', creditos_disponibles=creditos, plan_max_comprado=plan_max_comprado)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        db.session.add(SesionUsuarioMV(user_id=user.id, cookies_json='[{"name":"x","value":"y"}]', mv_usuario='mv1'))

        storage = StorageLocal(storage_tmp)
        pdf_path = storage_tmp / 'previo.pdf'
        pdf_path.write_bytes(b'%PDF-1.4 previo')
        key = 'usuarios/1/expedientes/1-viejo.pdf'
        storage.guardar(str(pdf_path), key)

        exp = ExpedienteDescargado(
            user_id=user.id, numero='1234/2024', estado='completed',
            storage_key=key, total_filas=10, total_archivos=3,
            huellas_json='["h1","h2"]',
        )
        db.session.add(exp)
        db.session.commit()
        return user.id, exp.id


def test_sin_storage_o_total_filas_da_400():
    app = _app()
    with app.app_context():
        user = User(email='sinfilas@foja.com', creditos_disponibles=5, plan_max_comprado='estudio')
        user.establecer_password('x')
        db.session.add(user)
        db.session.commit()
        exp = ExpedienteDescargado(user_id=user.id, numero='1/24', estado='completed')  # sin storage_key/total_filas
        db.session.add(exp)
        db.session.commit()
        user_id, exp_id = user.id, exp.id

    client = app.test_client()
    _login(client, user_id)
    resp = client.post(f'/descargas/expediente/{exp_id}/actualizar')
    check("400 si falta storage_key/total_filas", resp.status_code == 400, resp.status_code)
    check("tipo_error identifica la causa", resp.get_json().get('tipo_error') == 'actualizacion_no_disponible')


def test_sin_plan_da_403():
    with tempfile.TemporaryDirectory() as tmp:
        app = _app()
        user_id, exp_id = _crear_expediente_actualizable(app, plan_max_comprado=None, storage_tmp=Path(tmp))
        client = app.test_client()
        _login(client, user_id)
        resp = client.post(f'/descargas/expediente/{exp_id}/actualizar')
        check("403 sin plan estudio/matricula", resp.status_code == 403, resp.status_code)
        check("tipo_error es plan_requerido", resp.get_json().get('tipo_error') == 'plan_requerido')


def test_sin_creditos_da_402():
    with tempfile.TemporaryDirectory() as tmp:
        app = _app()
        user_id, exp_id = _crear_expediente_actualizable(app, creditos=0, storage_tmp=Path(tmp))
        client = app.test_client()
        _login(client, user_id)
        resp = client.post(f'/descargas/expediente/{exp_id}/actualizar')
        check("402 sin créditos", resp.status_code == 402, resp.status_code)


def test_job_en_curso_da_409():
    with tempfile.TemporaryDirectory() as tmp:
        app = _app()
        user_id, exp_id = _crear_expediente_actualizable(app, storage_tmp=Path(tmp))

        descargas_mod._jobs['job-existente'] = {
            'estado': 'procesando', 'user_id': user_id, 'numero': '1234/2024', 'timestamp': time.time(),
        }
        try:
            client = app.test_client()
            _login(client, user_id)
            resp = client.post(f'/descargas/expediente/{exp_id}/actualizar')
            check("409 si ya hay un job en curso para el mismo expediente", resp.status_code == 409, resp.status_code)
        finally:
            descargas_mod._jobs.pop('job-existente', None)


def _correr_actualizar_y_esperar(client, exp_id, resultado_pipeline, storage_tmp):
    original_esperar = descargas_mod.gestor.esperar_turno
    original_pipeline = descargas_mod.PipelineDescargador
    original_storage = descargas_mod.storage_pdf

    class _PipelineFalso:
        def ejecutar(self, **kwargs):
            return resultado_pipeline

    storage_falso = StorageLocal(storage_tmp)
    # Precargar la misma key que ya tiene el expediente, para que
    # descargar() la encuentre al bajar "el PDF previo" desde la ruta.
    with app_ctx_actual().app_context():
        exp = ExpedienteDescargado.query.get(exp_id)
        storage_falso.guardar(str(storage_tmp / 'previo.pdf'), exp.storage_key)

    try:
        descargas_mod.gestor.esperar_turno = lambda entrada, on_posicion=None, **kw: _ControlFalso()
        descargas_mod.PipelineDescargador = _PipelineFalso
        descargas_mod.storage_pdf = lambda: storage_falso

        resp = client.post(f'/descargas/expediente/{exp_id}/actualizar')
        if resp.status_code != 202:
            return resp, None

        job_id = resp.get_json()['job_id']
        for _ in range(50):
            job = descargas_mod._jobs.get(job_id)
            if job and job.get('estado') != 'procesando':
                break
            time.sleep(0.1)
        return resp, descargas_mod._jobs.get(job_id)
    finally:
        descargas_mod.gestor.esperar_turno = original_esperar
        descargas_mod.PipelineDescargador = original_pipeline
        descargas_mod.storage_pdf = original_storage


_app_actual = None


def app_ctx_actual():
    return _app_actual


def test_actualizacion_exitosa_202_y_no_repite_pdf_previo_perdido():
    global _app_actual
    with tempfile.TemporaryDirectory() as tmp:
        storage_tmp = Path(tmp)
        app = _app()
        _app_actual = app
        user_id, exp_id = _crear_expediente_actualizable(app, storage_tmp=storage_tmp)

        pdf_nuevo = storage_tmp / 'nuevo.pdf'
        pdf_nuevo.write_bytes(b'%PDF-1.4 nuevo')

        resultado = ResultadoPipeline(
            exito=True,
            expediente={'caratula': 'X', 'tribunal': 'Y'},
            pdf_final=pdf_nuevo,
            archivos_descargados=2,
            total_filas=12,
            huellas=['h1', 'h2'],
            parcial=False,
        )

        client = app.test_client()
        _login(client, user_id)
        resp, job = _correr_actualizar_y_esperar(client, exp_id, resultado, storage_tmp)

        check("202 al lanzar la actualización", resp.status_code == 202, resp.status_code)
        check("el job terminó en estado completo", job is not None and job.get('estado') == 'completo', job)

        with app.app_context():
            user = db.session.get(User, user_id)
            check("se descontó 1 crédito", user.creditos_disponibles == 4, user.creditos_disponibles)

            nuevo_exp = ExpedienteDescargado.query.filter_by(user_id=user_id, es_actualizacion=True).first()
            check("se creó un registro marcado como actualización", nuevo_exp is not None)
            if nuevo_exp:
                check("total_archivos es acumulativo (3 previos + 2 nuevos)", nuevo_exp.total_archivos == 5, nuevo_exp.total_archivos)
                check("actualizado_desde_id apunta al registro anterior", nuevo_exp.actualizado_desde_id == exp_id)


def test_sin_novedades_no_descuenta_credito():
    global _app_actual
    with tempfile.TemporaryDirectory() as tmp:
        storage_tmp = Path(tmp)
        app = _app()
        _app_actual = app
        user_id, exp_id = _crear_expediente_actualizable(app, storage_tmp=storage_tmp)

        resultado = ResultadoPipeline(exito=False, tipo_error='sin_novedades', error='No hay movimientos nuevos.')

        client = app.test_client()
        _login(client, user_id)
        resp, job = _correr_actualizar_y_esperar(client, exp_id, resultado, storage_tmp)

        check("202 al lanzar (el resultado final llega por /estado)", resp.status_code == 202, resp.status_code)
        check("el job termina en error/sin_novedades", job is not None and job.get('tipo_error') == 'sin_novedades', job)

        with app.app_context():
            user = db.session.get(User, user_id)
            check("NO se descontó crédito", user.creditos_disponibles == 5, user.creditos_disponibles)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE POST /descargas/expediente/<id>/actualizar (LOTE 5)")
    print("=" * 70)

    test_sin_storage_o_total_filas_da_400()
    test_sin_plan_da_403()
    test_sin_creditos_da_402()
    test_job_en_curso_da_409()
    test_actualizacion_exitosa_202_y_no_repite_pdf_previo_perdido()
    test_sin_novedades_no_descuenta_credito()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
