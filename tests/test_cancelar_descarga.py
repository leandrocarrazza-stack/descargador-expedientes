#!/usr/bin/env python3
"""
Tests del botón de cancelar una descarga en curso:
  1. debe_cancelar corta el loop de páginas de descargar_todo_por_paginas
     (modulos/descarga.py) apenas se nota, sin terminar el recorrido.
  2. esperar_turno (modulos/concurrencia.py) levanta ErrorCancelado si
     debe_cancelar() da True mientras el job espera en cola.
  3. POST /descargas/expediente/<job_id>/cancelar: 404 ajeno/inexistente,
     400 si ya terminó, 200 si está en curso.
  4. Un job cancelado no cobra crédito ni manda el email de error aunque
     esté activado.

`python test_cancelar_descarga.py`.
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

from modulos.descarga import DescargadorArchivos
from modulos.excepciones import ErrorDescarga
from modulos.concurrencia import GestorConcurrencia, EntradaCola, ErrorCancelado

import rutas.descargas as descargas_mod
from modulos.database import db
from modulos.models import User
from modulos.pipeline import ResultadoPipeline
from modulos.extensions import mail
from modulos.storage import StorageLocal
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


# ═══════════════════════════════════════════════════════════════════════════
#  1. debe_cancelar corta el loop de páginas
# ═══════════════════════════════════════════════════════════════════════════

def _fila(descripcion="texto", n_botones=1):
    return {"fecha": "01/01/2024", "tipo": "Providencia", "fojas": "1", "descripcion": descripcion, "n_botones": n_botones}


class _DriverPaginas:
    def __init__(self, filas_por_pagina):
        self.filas_por_pagina = filas_por_pagina
        self.indice_pagina = 0
        self.current_url = "https://mesavirtual.jusentrerios.gov.ar/expedientes/1"
        self.page_source = ""

    def execute_script(self, script):
        return self.filas_por_pagina[self.indice_pagina]

    def find_elements(self, by, selector):
        if 'GetAppIcon' in str(selector):
            total = sum(f.get('n_botones', 0) for f in self.filas_por_pagina[self.indice_pagina])
            return ['boton'] * total
        return []


def _descargador():
    return DescargadorArchivos(cliente_selenium=None, carpeta_temp=Path(tempfile.mkdtemp()))


def test_debe_cancelar_corta_el_loop_de_paginas():
    print("\n[1] debe_cancelar corta descargar_todo_por_paginas antes de terminar")

    # 3 páginas con 2 archivos cada una: sin cancelar bajaría 6.
    filas_por_pagina = [
        [_fila("p1-a"), _fila("p1-b")],
        [_fila("p2-a"), _fila("p2-b")],
        [_fila("p3-a"), _fila("p3-b")],
    ]
    driver = _DriverPaginas(filas_por_pagina)

    d = _descargador()
    d.cliente = type('C', (), {'driver': driver})()
    d._esperar_tabla_cargada = lambda driver, timeout=15: True
    d._detectar_paginacion = lambda driver: None

    def _fake_navegar(driver, paginacion_cacheada=None):
        if driver.indice_pagina + 1 < len(driver.filas_por_pagina):
            driver.indice_pagina += 1
            return True
        return False
    d._navegar_siguiente_pagina = _fake_navegar

    llamados = {'n': 0}

    def _fake_descargar(indice_boton, ruta_destino):
        llamados['n'] += 1
        ruta_destino.write_bytes(b"%PDF-1.4 fake")
        return True
    d._descargar_archivo_selenium = _fake_descargar

    # Cancela apenas se descargó el primer archivo.
    cancelar = {'valor': False}

    def _debe_cancelar():
        if llamados['n'] >= 1:
            cancelar['valor'] = True
        return cancelar['valor']

    import modulos.descarga as _descarga_mod
    sleep_original = _descarga_mod.time.sleep
    _descarga_mod.time.sleep = lambda *a, **k: None
    try:
        try:
            d.descargar_todo_por_paginas("1/24", debe_cancelar=_debe_cancelar)
            check("levantó ErrorDescarga(CANCELADO_POR_USUARIO)", False, "no lanzó nada")
        except ErrorDescarga as e:
            check("levantó ErrorDescarga(CANCELADO_POR_USUARIO)", "CANCELADO_POR_USUARIO" in str(e), str(e))
    finally:
        _descarga_mod.time.sleep = sleep_original

    check("no llegó a descargar los 6 archivos (se cortó temprano)", llamados['n'] < 6, llamados['n'])


def test_sin_debe_cancelar_no_cambia_nada():
    print("\n[1b] Sin debe_cancelar (llamador viejo), el comportamiento no cambia")
    filas_por_pagina = [[_fila("p1-a")]]
    driver = _DriverPaginas(filas_por_pagina)

    d = _descargador()
    d.cliente = type('C', (), {'driver': driver})()
    d._esperar_tabla_cargada = lambda driver, timeout=15: True
    d._detectar_paginacion = lambda driver: None
    d._navegar_siguiente_pagina = lambda driver, paginacion_cacheada=None: False
    d._descargar_archivo_selenium = lambda indice_boton, ruta_destino: (ruta_destino.write_bytes(b"%PDF-1.4 x"), True)[1]

    import modulos.descarga as _descarga_mod
    sleep_original = _descarga_mod.time.sleep
    _descarga_mod.time.sleep = lambda *a, **k: None
    try:
        resultado = d.descargar_todo_por_paginas("1/24")
    finally:
        _descarga_mod.time.sleep = sleep_original

    check("descarga normal sin debe_cancelar", len(resultado) == 1, len(resultado))


# ═══════════════════════════════════════════════════════════════════════════
#  2. ErrorCancelado en esperar_turno (cola)
# ═══════════════════════════════════════════════════════════════════════════

def test_cancelar_mientras_espera_en_cola():
    print("\n[2] debe_cancelar levanta ErrorCancelado mientras espera turno en cola")
    # 0 navegadores disponibles: nunca se admite, así que se queda esperando
    # hasta que debe_cancelar() dé True.
    gestor = GestorConcurrencia(max_navegadores=0, fn_memoria=lambda: None)
    entrada, _ = gestor.encolar('job-cancel')

    llamadas = {'n': 0}

    def _debe_cancelar():
        llamadas['n'] += 1
        return llamadas['n'] >= 2

    try:
        gestor.esperar_turno(entrada, timeout=5, debe_cancelar=_debe_cancelar)
        check("levantó ErrorCancelado", False, "no lanzó nada")
    except ErrorCancelado:
        check("levantó ErrorCancelado", True)

    check("la entrada se sacó de la cola pese a la excepción", gestor.estado()['cola'] == 0, gestor.estado())


# ═══════════════════════════════════════════════════════════════════════════
#  3. Ruta POST /descargas/expediente/<job_id>/cancelar
# ═══════════════════════════════════════════════════════════════════════════

def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['MAIL_SUPPRESS_SEND'] = True
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def test_ruta_cancelar_404_400_200():
    app = _app()
    with app.app_context():
        user = User(email='cancel@foja.com', creditos_disponibles=5)
        user.establecer_password('Password123!')
        otro = User(email='otro-cancel@foja.com', creditos_disponibles=5)
        otro.establecer_password('Password123!')
        db.session.add_all([user, otro])
        db.session.commit()
        user_id, otro_id = user.id, otro.id

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/descargas/expediente/inexistente/cancelar')
    check("404 si el job no existe", resp.status_code == 404, resp.status_code)

    descargas_mod._jobs['job-de-otro'] = {'estado': 'procesando', 'user_id': otro_id, 'numero': '1/24', 'timestamp': time.time()}
    try:
        resp = client.post('/descargas/expediente/job-de-otro/cancelar')
        check("404 si el job es de otro usuario", resp.status_code == 404, resp.status_code)
    finally:
        descargas_mod._jobs.pop('job-de-otro', None)

    descargas_mod._jobs['job-terminado'] = {'estado': 'completo', 'user_id': user_id, 'numero': '1/24', 'timestamp': time.time()}
    try:
        resp = client.post('/descargas/expediente/job-terminado/cancelar')
        check("400 si el job ya terminó", resp.status_code == 400, resp.status_code)
    finally:
        descargas_mod._jobs.pop('job-terminado', None)

    descargas_mod._jobs['job-en-curso'] = {'estado': 'procesando', 'user_id': user_id, 'numero': '1/24', 'timestamp': time.time()}
    try:
        resp = client.post('/descargas/expediente/job-en-curso/cancelar')
        check("200 si el job está en curso y es propio", resp.status_code == 200, resp.status_code)
        check("marca el job como cancelado", descargas_mod._jobs['job-en-curso'].get('cancelado') is True)
    finally:
        descargas_mod._jobs.pop('job-en-curso', None)


# ═══════════════════════════════════════════════════════════════════════════
#  4. Un job cancelado no cobra crédito ni manda email de error
# ═══════════════════════════════════════════════════════════════════════════

def test_cancelado_no_cobra_ni_avisa_error():
    print("\n[4] tipo_error='cancelado' no descuenta crédito ni manda email")
    app = _app()
    with app.app_context():
        user = User(email='cancelnotif@foja.com', creditos_disponibles=5, notificar_email=True)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    resultado = ResultadoPipeline(exito=False, tipo_error='cancelado', error='Descarga cancelada.')

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
                with mail.record_messages() as outbox:
                    descargas_mod._jobs['job-cancel-final'] = {'estado': 'procesando', 'user_id': user_id, 'numero': '1/24', 'timestamp': time.time()}
                    descargas_mod._run_pipeline(
                        app, 'job-cancel-final', user_id, '1234/2024', None, {'cookie': 'x'},
                        entrada=object(), notificar_email=True,
                    )
                check("no se mandó ningún email pese a notificar_email=True", len(outbox) == 0, len(outbox))
        finally:
            descargas_mod.gestor.esperar_turno = original_esperar
            descargas_mod.PipelineDescargador = original_pipeline
            descargas_mod.storage_pdf = original_storage
            descargas_mod._jobs.pop('job-cancel-final', None)

    with app.app_context():
        user = db.session.get(User, user_id)
        check("no se descontó crédito", user.creditos_disponibles == 5, user.creditos_disponibles)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DEL BOTÓN DE CANCELAR")
    print("=" * 70)

    test_debe_cancelar_corta_el_loop_de_paginas()
    test_sin_debe_cancelar_no_cambia_nada()
    test_cancelar_mientras_espera_en_cola()
    test_ruta_cancelar_404_400_200()
    test_cancelado_no_cobra_ni_avisa_error()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
