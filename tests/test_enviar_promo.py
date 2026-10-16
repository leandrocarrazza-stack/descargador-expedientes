#!/usr/bin/env python3
"""
Tests del script de envío de promos (scripts/enviar_promo.py), con un servidor SMTP
falso: no se manda ningún mail de verdad. `python tests/test_enviar_promo.py`
"""

import importlib.util
import smtplib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor

from modulos.database import db
from modulos.models import User
from modulos import promos
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


def _cargar_script():
    spec = importlib.util.spec_from_file_location(
        'enviar_promo', Path(__file__).parent.parent / 'scripts' / 'enviar_promo.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    return app


def _usuarios(n):
    out = []
    for i in range(n):
        u = User(email=f'cli{i}@gmail.com', nombre=f'Cliente {i}', creditos_disponibles=2)
        u.establecer_password('x')
        db.session.add(u)
        out.append(u)
    db.session.commit()
    return out


class SmtpFalso:
    """Registra lo que se 'envía'; `fallas` mapea email → excepción a lanzar."""
    def __init__(self, fallas=None):
        self.enviados, self.fallas = [], fallas or {}

    def send_message(self, msg):
        destino = msg['To']
        if destino in self.fallas:
            raise self.fallas[destino]
        self.enviados.append(msg)


def test_mensaje():
    ep = _cargar_script()
    app = _app()
    with app.app_context():
        u = _usuarios(1)[0]
        mail = promos.renderizar_promo('contador_plazos', u)
        msg = ep.construir_mensaje(mail, u.email, promos.url_baja(u.id))
        texto = msg.get_body(('plain',)).get_content()
        html = msg.get_body(('html',)).get_content()
        check("el mail trae versión texto y HTML", 'Hola Cliente' in texto and '<table' in html)
        check("remitente y respuesta son asistenciafoja", 'asistenciafoja@gmail.com' in msg['From'] and msg['Reply-To'] == 'asistenciafoja@gmail.com')
        check("destinatario y asunto correctos", msg['To'] == u.email and msg['Subject'].startswith('Nuevo en Foja'))
        check("trae el encabezado de cancelar suscripción", msg['List-Unsubscribe'] == f'<{promos.url_baja(u.id)}>')
        check("el link de baja personal está en el HTML y en el texto", promos.url_baja(u.id) in html and promos.url_baja(u.id) in texto)
        check("el asunto con tildes sobrevive al armado del correo", 'calculá' in msg.as_string() or '=?utf-8?' in msg.as_string().lower())


def test_lote_y_limite():
    ep = _cargar_script()
    app = _app()
    with app.app_context():
        db.session.query(User).delete()
        us = _usuarios(5)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'envios.csv'
            pausas = []
            smtp = SmtpFalso()
            r = ep.enviar_lote(smtp, us, 'contador_plazos', log, limite=3, pausa=10, dormir=pausas.append)
            check("respeta el límite por corrida", r['enviados'] == 3 and len(smtp.enviados) == 3, r)
            check("pausa entre mails, no antes del primero", len(pausas) == 2 and all(10 <= p <= 15 for p in pausas), pausas)
            check("cada cliente recibe SU link de baja y no el de otro",
                  all(promos.url_baja(u.id) in m.get_body(('html',)).get_content()
                      for u, m in zip(us[:3], smtp.enviados)) and
                  promos.url_baja(us[4].id) not in smtp.enviados[0].get_body(('html',)).get_content())
            check("el registro anota a los 3 enviados", ep.leer_registrados(log) == {'cli0@gmail.com', 'cli1@gmail.com', 'cli2@gmail.com'})

            # segunda corrida: continúa con los que faltan, sin repetir
            ya = ep.leer_registrados(log)
            pendientes = [u for u in us if u.email.lower() not in ya]
            smtp2 = SmtpFalso()
            r2 = ep.enviar_lote(smtp2, pendientes, 'contador_plazos', log, limite=25, pausa=0, dormir=lambda s: None)
            check("la segunda corrida solo manda a los que faltaban", [m['To'] for m in smtp2.enviados] == ['cli3@gmail.com', 'cli4@gmail.com'], r2)
            check("después de las dos corridas nadie queda pendiente", len(ep.leer_registrados(log)) == 5)


def test_fallas():
    ep = _cargar_script()
    app = _app()
    with app.app_context():
        db.session.query(User).delete()
        us = _usuarios(5)
        with tempfile.TemporaryDirectory() as tmp:
            # un destinatario rechazado no frena a los demás y no se reintenta
            log = Path(tmp) / 'a.csv'
            smtp = SmtpFalso({'cli1@gmail.com': smtplib.SMTPRecipientsRefused({'cli1@gmail.com': (550, b'no existe')})})
            r = ep.enviar_lote(smtp, us, 'contador_plazos', log, 25, 0, dormir=lambda s: None)
            check("un rechazo se anota y el resto sigue", r['rechazados'] == 1 and r['enviados'] == 4 and r['detenido'] is None, r)
            check("lo rechazado no se vuelve a intentar", 'cli1@gmail.com' in ep.leer_registrados(log))

            # límite diario de Gmail: se frena y NO se anota (para reintentar mañana)
            log = Path(tmp) / 'b.csv'
            smtp = SmtpFalso({'cli2@gmail.com': smtplib.SMTPDataError(550, b'5.4.5 Daily user sending limit exceeded')})
            r = ep.enviar_lote(smtp, us, 'contador_plazos', log, 25, 0, dormir=lambda s: None)
            check("si Gmail frena, el script se detiene", r['enviados'] == 2 and r['detenido'] and 'Gmail frenó' in r['detenido'], r)
            check("el que no se pudo enviar queda pendiente", 'cli2@gmail.com' not in ep.leer_registrados(log))

            # tres errores seguidos: algo anda mal, se frena
            log = Path(tmp) / 'c.csv'
            smtp = SmtpFalso({f'cli{i}@gmail.com': RuntimeError('boom') for i in range(5)})
            r = ep.enviar_lote(smtp, us, 'contador_plazos', log, 25, 0, dormir=lambda s: None)
            check("3 errores seguidos frenan el envío", r['errores'] == 3 and r['detenido'] and len(smtp.enviados) == 0, r)
            check("los errores se reintentan (no cuentan como terminados)", ep.leer_registrados(log) == set())

            # la prueba (log=None) no deja registro
            smtp = SmtpFalso()
            r = ep.enviar_lote(smtp, us[:1], 'contador_plazos', None, 1, 0, dormir=lambda s: None)
            check("la prueba sin registro envía igual", r['enviados'] == 1 and len(smtp.enviados) == 1)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DEL ENVÍO DE PROMOS")
    print("=" * 70)
    test_mensaje()
    test_lote_y_limite()
    test_fallas()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
