#!/usr/bin/env python3
"""
Tests del programa de referidos (modulos/referidos.py, signup en rutas/auth.py,
premio en rutas/pagos.py y campaña 'recomenda' de modulos/promos.py).
`python tests/test_referidos.py`
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor
# Este entorno no tiene MAIL_USERNAME, así que Flask-Mail no tendría remitente
# y rechazaría armar los mails (mismo truco que tests/test_emails_descarga.py).
config.MAIL_DEFAULT_SENDER = 'foja@example.com'
# Flask-Mail decide si suprime los envíos al inicializarse, así que TESTING tiene
# que estar puesto ANTES de crear la app; si no, el test abre SMTP real a Gmail.
config.TESTING = True

from modulos.database import db
from modulos.extensions import mail
from modulos.models import User, CompraCreditos
from modulos import promos, referidos
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _usuario(email, nombre=None, creditos=0, **extra):
    u = User(email=email, nombre=nombre, creditos_disponibles=creditos, **extra)
    u.establecer_password('x')
    db.session.add(u)
    db.session.commit()
    return u


_n_compra = [0]


def _compra(usuario):
    """Una compra pendiente de 1 crédito (como la deja crear_orden_pago)."""
    _n_compra[0] += 1
    c = CompraCreditos(user_id=usuario.id, stripe_payment_id=f'pago-{_n_compra[0]}', creditos_comprados=1,
                       monto_pagado=3000, plan='individual', estado='pending')
    db.session.add(c)
    db.session.commit()
    return c


def test_codigo():
    c = referidos.codigo_referido(7)
    check("el código vuelve al id de usuario", referidos.usuario_id_desde_codigo(c) == 7)
    check("un código alterado se rechaza", referidos.usuario_id_desde_codigo(c[:-3] + 'abc') is None)
    check("basura se rechaza", referidos.usuario_id_desde_codigo('hola') is None)
    check("un código del link de baja no sirve como código de referido",
          referidos.usuario_id_desde_codigo(promos.token_baja(7)) is None)
    check("el link usa la URL pública y el código",
          referidos.url_referido(7) == f"{config.BASE_URL.rstrip('/')}/auth/signup?ref={c}")


def test_registro():
    app = _app()
    with app.app_context():
        referidor = _usuario('referidor@gmail.com', creditos=2)
        referidor_id = referidor.id
        codigo = referidos.codigo_referido(referidor_id)

    # Entra por el link (GET con ?ref) y después crea la cuenta (POST sin ref):
    # el código viaja en la sesión.
    c = app.test_client()
    c.get(f'/auth/signup?ref={codigo}')
    r = c.post('/auth/signup', json={'email': 'nuevo@gmail.com', 'nombre': 'Ana', 'password': 'Secreta123!', 'terminos': True})
    check("el registro con link funciona", r.status_code == 200, r.get_data(as_text=True))
    with app.app_context():
        nuevo = User.query.filter_by(email='nuevo@gmail.com').first()
        check("el nuevo queda anotado como recomendado", nuevo.referido_por_id == referidor_id)
        check("registrarse solo NO da premio", db.session.get(User, referidor_id).creditos_disponibles == 2)
        check("el nuevo tiene sus 2 descargas de siempre", nuevo.creditos_disponibles == 2)

    # Código inválido: la cuenta se crea igual, sin referido.
    c2 = app.test_client()
    c2.get('/auth/signup?ref=basura')
    r = c2.post('/auth/signup', json={'email': 'otro@gmail.com', 'nombre': 'Beto', 'password': 'Secreta123!', 'terminos': True})
    check("con ref inválido la cuenta se crea igual", r.status_code == 200)
    with app.app_context():
        check("con ref inválido no queda referido", User.query.filter_by(email='otro@gmail.com').first().referido_por_id is None)


def test_premio_primera_compra():
    from rutas.pagos import _confirmar_compra
    app = _app()
    with app.app_context():
        referidor = _usuario('referidor@gmail.com', creditos=2)
        nuevo = _usuario('nuevo@gmail.com', nombre='Ana <b>Gómez</b>', creditos=2, referido_por_id=referidor.id)
        rid, nid = referidor.id, nuevo.id

        with mail.record_messages() as outbox:
            _confirmar_compra(_compra(nuevo))
            check("el comprador recibe sus créditos", db.session.get(User, nid).creditos_disponibles == 3)
            check("el referidor suma 2 créditos", db.session.get(User, rid).creditos_disponibles == 4)
            check("el recomendado queda marcado como premiado", db.session.get(User, nid).referido_premiado is True)
            check("se manda un mail de aviso al referidor", len(outbox) == 1 and outbox[0].recipients == ['referidor@gmail.com'])
            check("el mail dice quién se sumó (con HTML escapado)",
                  'Ana' in outbox[0].html and '<b>Gómez' not in outbox[0].html)

            _confirmar_compra(_compra(nuevo))
            check("la segunda compra del mismo recomendado no vuelve a premiar", db.session.get(User, rid).creditos_disponibles == 4)
            check("y no manda otro mail", len(outbox) == 1)

        # Una compra de alguien que no vino recomendado no toca a nadie.
        solo = _usuario('solo@gmail.com', creditos=0)
        _confirmar_compra(_compra(solo))
        check("compra sin referidor: nadie más suma", db.session.get(User, rid).creditos_disponibles == 4)


def test_tope():
    from rutas.pagos import _confirmar_compra
    app = _app()
    with app.app_context():
        referidor = _usuario('referidor@gmail.com', creditos=0)
        rid = referidor.id
        for i in range(referidos.TOPE_PREMIADOS):
            _usuario(f'premiado{i}@gmail.com', referido_por_id=rid, referido_premiado=True)
        referidor.creditos_disponibles = 20
        db.session.commit()
        check("cuenta los premiados", referidos.premiados_de(rid) == referidos.TOPE_PREMIADOS)

        undecimo = _usuario('undecimo@gmail.com', creditos=2, referido_por_id=rid)
        _confirmar_compra(_compra(undecimo))
        check("pasado el tope no suma créditos", db.session.get(User, rid).creditos_disponibles == 20)
        check("pero el comprador igual recibe los suyos", db.session.get(User, undecimo.id).creditos_disponibles == 3)


def test_campana_recomenda():
    app = _app()
    with app.app_context():
        u = _usuario('cli@gmail.com', nombre='María Pérez', creditos=1)
        mail_ = promos.renderizar_promo('recomenda', u, anio=2026)
        h, t = mail_['html'], mail_['texto']
        link = referidos.url_referido(u.id)
        check("asunto de la campaña", mail_['asunto'].startswith('Recomendá Foja'))
        check("trae el link personal en HTML y texto", link in h and link in t)
        check("trae el botón de WhatsApp", 'href="https://wa.me/?text=' in h)
        check("menciona el tope de 20 descargas", '20 descargas' in h and '20 descargas' in t)
        check("trae el link de baja", promos.url_baja(u.id) in h and promos.url_baja(u.id) in t)
        check("no quedan variables sin reemplazar", '{{' not in h and '{{' not in t and '{%' not in h)


def test_version_mailerlite():
    app = _app()
    with app.app_context():
        h = promos.renderizar_promo_mailerlite('recomenda', anio=2026)
        check("trae los marcadores de MailerLite para el link, WhatsApp y la baja",
              h.count('{$link_referido}') >= 1 and '{$link_whatsapp}' in h and h.count('{$unsubscribe}') == 1)
        check("el saludo usa el primer nombre con valor por defecto, sin escapar las comillas",
              "{$name|default('colega')}" in h and '&#39;' not in h)
        check("no lleva el link ni el código de ninguna persona", '/auth/signup?ref=' not in h and 'wa.me' not in h)
        check("no quedan variables de Jinja sin resolver", '{{' not in h and '{%' not in h)
        check("mantiene el tope y el premio", '20 descargas' in h and '2 descargas' in h)

        u = _usuario('cli@gmail.com', nombre='María Pérez', creditos=1)
        link = referidos.url_referido(u.id)
        check("el link de WhatsApp lleva el link personal codificado",
              promos.url_whatsapp(link).startswith('https://wa.me/?text=') and 'ref%3D' in promos.url_whatsapp(link))
        try:
            promos.renderizar_promo_mailerlite('no_existe')
            check("campaña desconocida → error", False)
        except ValueError:
            check("campaña desconocida → error", True)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE REFERIDOS")
    print("=" * 70)
    test_codigo()
    test_registro()
    test_premio_primera_compra()
    test_tope()
    test_campana_recomenda()
    test_version_mailerlite()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
