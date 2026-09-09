#!/usr/bin/env python3
"""
Test de la condición de carrera en el registro de jobs (POST /descargas/expediente):
antes del fix, el chequeo de _hay_job_en_curso y la reserva en _jobs no eran
atómicos, así que dos POST casi simultáneos del mismo (usuario, expediente)
podían lanzar dos pipelines independientes en vez de que el segundo reciba 409.

Dispara dos requests reales desde dos threads, sincronizados con una
threading.Barrier para maximizar la superposición, contra la ruta real (no
_run_pipeline directo). gestor.esperar_turno se parchea para bloquear un
rato corto (mantiene el job "procesando" durante la ventana del test) y
PipelineDescargador se parchea para no tocar Selenium. `python
test_concurrencia_jobs.py`.
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

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


class _ControlFalso:
    def liberar_navegador(self):
        pass

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


def test_dos_post_simultaneos_solo_uno_arranca():
    app = _app()
    with app.app_context():
        user = User(email='race@foja.com', creditos_disponibles=5)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        db.session.add(SesionUsuarioMV(user_id=user.id, cookies_json='[{"name":"x","value":"y"}]', mv_usuario='mv1'))
        db.session.commit()
        user_id = user.id

    with tempfile.TemporaryDirectory() as tmp:
        storage_tmp = Path(tmp)
        pdf_final = storage_tmp / 'final.pdf'
        pdf_final.write_bytes(b'%PDF-1.4 x')

        resultado = ResultadoPipeline(
            exito=True,
            expediente={'caratula': 'X', 'tribunal': 'Y'},
            pdf_final=pdf_final,
            archivos_descargados=1,
        )

        class _PipelineFalso:
            def ejecutar(self, **kwargs):
                return resultado

        original_esperar = descargas_mod.gestor.esperar_turno
        original_pipeline = descargas_mod.PipelineDescargador
        original_storage = descargas_mod.storage_pdf

        # Mantiene el job "procesando" el tiempo suficiente para que el
        # segundo POST llegue a chequear _jobs mientras el primero sigue
        # registrado (así el test no depende de que gane una carrera real).
        def _esperar_turno_lento(entrada, on_posicion=None, **kw):
            time.sleep(0.3)
            return _ControlFalso()

        resultados = {}
        barrera = threading.Barrier(2)

        def _post(nombre):
            client = app.test_client()
            _login(client, user_id)
            barrera.wait(timeout=5)
            resp = client.post('/descargas/expediente', json={'numero_expediente': '9999/2024'})
            resultados[nombre] = (resp.status_code, resp.get_json())

        try:
            descargas_mod.gestor.esperar_turno = _esperar_turno_lento
            descargas_mod.PipelineDescargador = _PipelineFalso
            descargas_mod.storage_pdf = lambda: StorageLocal(storage_tmp)

            t1 = threading.Thread(target=_post, args=('a',))
            t2 = threading.Thread(target=_post, args=('b',))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            codigos = sorted(v[0] for v in resultados.values())
            check("ambos threads respondieron", len(resultados) == 2, resultados)
            check("exactamente uno de los dos arrancó (202) y el otro fue rechazado (409)",
                  codigos == [202, 409], codigos)

            rechazado = next((v for v in resultados.values() if v[0] == 409), None)
            check("el rechazado trae tipo_error=job_en_curso",
                  rechazado is not None and rechazado[1].get('tipo_error') == 'job_en_curso',
                  rechazado)

            # Esperar a que el job ganador termine, para no dejar el thread
            # de fondo corriendo tras salir del directorio temporal.
            aceptado = next((v for v in resultados.values() if v[0] == 202), None)
            if aceptado:
                job_id = aceptado[1]['job_id']
                for _ in range(50):
                    job = descargas_mod._jobs.get(job_id)
                    if job and job.get('estado') != 'procesando':
                        break
                    time.sleep(0.1)
        finally:
            descargas_mod.gestor.esperar_turno = original_esperar
            descargas_mod.PipelineDescargador = original_pipeline
            descargas_mod.storage_pdf = original_storage
            descargas_mod._jobs.clear()


if __name__ == '__main__':
    print("=" * 70)
    print(" TEST DE CONDICIÓN DE CARRERA EN REGISTRO DE JOBS")
    print("=" * 70)

    test_dos_post_simultaneos_solo_uno_arranca()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
