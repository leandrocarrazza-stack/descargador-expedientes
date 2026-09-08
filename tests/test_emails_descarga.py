#!/usr/bin/env python3
"""
Tests del aviso por email al terminar una descarga (lote 3).

Llama a rutas.descargas._run_pipeline() directamente (no por HTTP: el
pipeline real necesita Selenium) con:
  - gestor.esperar_turno() parcheado para no esperar de verdad en la cola
    (sin esto, arrastraría los chequeos reales de RAM/cupo de navegador).
  - PipelineDescargador.ejecutar() parcheado para devolver un
    ResultadoPipeline armado a mano (éxito o error), sin tocar Chrome.

Usa mail.record_messages() (Flask-Mail testing) para inspeccionar los
emails sin mandarlos de verdad. `python test_emails_descarga.py`.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
# Flask-Mail decide si suprime el envío real (Mail.init_app -> init_mail)
# en el momento en que se llama mail.init_app(app), usando app.testing en
# ESE instante — no relee app.config más tarde. app.testing a su vez sale
# de app.config['TESTING'], que Flask sólo llena si el objeto de config
# tiene el atributo TESTING al momento de app.config.from_object(config).
# Por eso esto tiene que pisarse ANTES de crear la app (acá, y no después
# con app.config['TESTING'] = True): si no, cada test intenta una conexión
# SMTP real a MAIL_SERVER y se queda colgado esperando el connect().
config.TESTING = True
# Este entorno de desarrollo no tiene MAIL_USERNAME configurado, así que
# MAIL_DEFAULT_SENDER queda vacío (ver config.py) y Flask-Mail rechaza
# armar cualquier mensaje sin remitente — se fuerza uno acá para el test.
config.MAIL_DEFAULT_SENDER = 'foja@example.com'

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


class _ControlFalso:
    """Evita tocar el gestor de concurrencia real durante el test."""
    def liberar_todo(self):
        pass


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['MAIL_SUPPRESS_SEND'] = True
    return app


def _crear_usuario(app, notificar_email=True):
    with app.app_context():
        user = User(email='avisos@foja.com', nombre='Test', notificar_email=notificar_email, creditos_disponibles=5)
        user.establecer_password('Password123!')
        db.session.add(user)
        db.session.commit()
        return user.id


def _correr_pipeline(app, user_id, resultado, notificar_email, monkeypatch_esperar=True):
    """
    Corre _run_pipeline sincrónicamente (mismo hilo) con el pipeline
    parcheado. También redirige storage_pdf() a un backend local en un
    directorio temporal: sin esto, cada corrida escribiría PDFs reales
    dentro de config.PDF_STORE_DIR (pdf_store/ del propio repo).
    """
    original_esperar = descargas_mod.gestor.esperar_turno
    original_pipeline = descargas_mod.PipelineDescargador
    original_storage = descargas_mod.storage_pdf

    class _PipelineFalso:
        def ejecutar(self, **kwargs):
            return resultado

    with tempfile.TemporaryDirectory() as tmp_storage:
        storage_falso = StorageLocal(Path(tmp_storage))
        try:
            if monkeypatch_esperar:
                descargas_mod.gestor.esperar_turno = lambda entrada, on_posicion=None, **kw: _ControlFalso()
            descargas_mod.PipelineDescargador = _PipelineFalso
            descargas_mod.storage_pdf = lambda: storage_falso

            descargas_mod._run_pipeline(
                app, 'job-test-1', user_id, '1234/2024', None, {'cookie': 'x'},
                entrada=object(), notificar_email=notificar_email,
            )
        finally:
            descargas_mod.gestor.esperar_turno = original_esperar
            descargas_mod.PipelineDescargador = original_pipeline
            descargas_mod.storage_pdf = original_storage


def test_email_exito_incluye_link_al_pdf():
    app = _app()
    user_id = _crear_usuario(app, notificar_email=True)

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(b'%PDF-1.4 contenido')
        pdf_path = Path(f.name)

    resultado = ResultadoPipeline(
        exito=True,
        expediente={'caratula': 'Perez c/ Gomez', 'tribunal': 'Juzgado Civil'},
        pdf_final=pdf_path,
        archivos_descargados=3,
    )

    with app.app_context():
        with mail.record_messages() as outbox:
            _correr_pipeline(app, user_id, resultado, notificar_email=True)

        check("se envió exactamente 1 email", len(outbox) == 1, len(outbox))
        if outbox:
            msg = outbox[0]
            check("destinatario correcto", msg.recipients == ['avisos@foja.com'], msg.recipients)
            check("el asunto menciona el expediente", '1234/2024' in msg.subject, msg.subject)
            check("el cuerpo tiene el link de descarga",
                  '/descargas/expediente/' in msg.body and '/descargar' in msg.body, msg.body)

        # El expediente debe haber quedado guardado en BD con storage_key
        from modulos.models import ExpedienteDescargado
        exp = ExpedienteDescargado.query.filter_by(user_id=user_id).first()
        check("el expediente se guardó como completed", exp is not None and exp.estado == 'completed')


def test_sin_notificar_email_no_manda_nada():
    app = _app()
    user_id = _crear_usuario(app, notificar_email=False)

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(b'%PDF-1.4 contenido')
        pdf_path = Path(f.name)

    resultado = ResultadoPipeline(exito=True, expediente={'caratula': 'X', 'tribunal': 'Y'}, pdf_final=pdf_path)

    with app.app_context():
        with mail.record_messages() as outbox:
            _correr_pipeline(app, user_id, resultado, notificar_email=False)
        check("no se envía ningún email si no se pidió", len(outbox) == 0, len(outbox))


def test_email_de_error_se_envia():
    app = _app()
    user_id = _crear_usuario(app, notificar_email=True)

    resultado = ResultadoPipeline(exito=False, tipo_error='unknown', error='Mesa Virtual no respondió')

    with app.app_context():
        with mail.record_messages() as outbox:
            _correr_pipeline(app, user_id, resultado, notificar_email=True)

        check("se envió el email de error", len(outbox) == 1, len(outbox))
        if outbox:
            msg = outbox[0]
            check("el cuerpo incluye el motivo del error",
                  'Mesa Virtual no respondió' in msg.body, msg.body)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE EMAIL DE AVISO DE DESCARGA")
    print("=" * 70)

    test_email_exito_incluye_link_al_pdf()
    test_sin_notificar_email_no_manda_nada()
    test_email_de_error_se_envia()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
