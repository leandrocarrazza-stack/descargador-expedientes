#!/usr/bin/env python3
"""
Tests de las rutas nuevas del admin (lote 1): borrar mensajes leídos,
individualmente o en bloque.

Usa la app real (servidor.crear_app) en modo testing (SQLite en memoria) y
el test_client de Flask. La sesión de login se arma escribiendo `_user_id`
directo (mismo mecanismo que usa Flask-Login internamente), sin pasar por
el formulario de /auth/login. `python test_rutas_admin.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
# servidor.py instancia una app a nivel de módulo (`app = crear_app()`, para
# gunicorn) usando config.SQLALCHEMY_DATABASE_URI tal como esté en ESTE
# momento: hay que pisarla ANTES de `from servidor import crear_app`, que es
# lo que dispara esa instanciación la primera vez que se importa servidor
# en todo el proceso de pytest (sin esto, este test podía terminar
# escribiendo en la BD real de desarrollo si otro archivo de test importaba
# `config` primero con el FLASK_ENV real).
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.models import User, MensajeContacto
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _app_y_cliente():
    app = crear_app()
    app.config['TESTING'] = True
    # Las rutas de borrado ya no llevan @csrf.exempt (se sacó en este mismo
    # lote): el test_client no arma un token CSRF real, así que se
    # deshabilita la protección para estos tests de ruta, como es estándar
    # al testear una app Flask-WTF.
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def test_eliminar_mensaje_no_leido_rechazado():
    app = _app_y_cliente()
    with app.app_context():
        admin = User(email='admin@foja.com', is_admin=True)
        admin.establecer_password('x')
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

        msg = MensajeContacto(nombre='Juan', email='juan@x.com', asunto='consulta', mensaje='hola', leido=False)
        db.session.add(msg)
        db.session.commit()
        msg_id = msg.id

    client = app.test_client()
    _login(client, admin_id)

    resp = client.delete(f'/admin/mensajes/{msg_id}')
    check("DELETE sin leer -> 400", resp.status_code == 400, resp.status_code)

    with app.app_context():
        check("el mensaje sigue existiendo", MensajeContacto.query.get(msg_id) is not None)


def test_eliminar_mensaje_leido_ok():
    app = _app_y_cliente()
    with app.app_context():
        admin = User(email='admin2@foja.com', is_admin=True)
        admin.establecer_password('x')
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

        msg = MensajeContacto(nombre='Ana', email='ana@x.com', asunto='consulta', mensaje='hola', leido=True)
        db.session.add(msg)
        db.session.commit()
        msg_id = msg.id

    client = app.test_client()
    _login(client, admin_id)

    resp = client.delete(f'/admin/mensajes/{msg_id}')
    check("DELETE leído -> 200", resp.status_code == 200, resp.status_code)

    with app.app_context():
        check("el mensaje ya no existe", MensajeContacto.query.get(msg_id) is None)


def test_eliminar_leidos_en_bloque():
    app = _app_y_cliente()
    with app.app_context():
        admin = User(email='admin3@foja.com', is_admin=True)
        admin.establecer_password('x')
        db.session.add(admin)
        db.session.add(MensajeContacto(nombre='A', email='a@x.com', asunto='s', mensaje='m', leido=True))
        db.session.add(MensajeContacto(nombre='B', email='b@x.com', asunto='s', mensaje='m', leido=True))
        db.session.add(MensajeContacto(nombre='C', email='c@x.com', asunto='s', mensaje='m', leido=False))
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    _login(client, admin_id)

    resp = client.post('/admin/mensajes/eliminar-leidos')
    check("POST eliminar-leidos -> 200", resp.status_code == 200, resp.status_code)
    data = resp.get_json()
    check("borra los 2 leídos, no el no-leído", data.get('cantidad') == 2, data)

    with app.app_context():
        check("quedó exactamente 1 mensaje (el no leído)",
              MensajeContacto.query.count() == 1)


def test_no_admin_no_puede():
    app = _app_y_cliente()
    with app.app_context():
        user = User(email='user@foja.com', is_admin=False)
        user.establecer_password('x')
        db.session.add(user)
        db.session.add(MensajeContacto(nombre='A', email='a@x.com', asunto='s', mensaje='m', leido=True))
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)

    resp = client.post('/admin/mensajes/eliminar-leidos')
    check("usuario no-admin -> 403", resp.status_code == 403, resp.status_code)


def test_csv_recomenda():
    import csv, io
    from modulos import referidos
    app = _app_y_cliente()
    with app.app_context():
        admin = User(email='admin@foja.com', is_admin=True)
        admin.establecer_password('x')
        sin_creditos = User(email='cero@foja.com', nombre='=HYPERLINK("http://x")', creditos_disponibles=0)
        sin_creditos.establecer_password('x')
        de_baja = User(email='baja@foja.com', nombre='Baja', recibir_promociones=False)
        de_baja.establecer_password('x')
        malo = User(email='no-es-un-email', nombre='Malo')
        malo.establecer_password('x')
        db.session.add_all([admin, sin_creditos, de_baja, malo])
        db.session.commit()
        admin_id, cero_id = admin.id, sin_creditos.id
        link_cero = referidos.url_referido(cero_id)
        no_admin = User(email='comun@foja.com', is_admin=False)
        no_admin.establecer_password('x')
        db.session.add(no_admin)
        db.session.commit()
        comun_id = no_admin.id

    sin_sesion = app.test_client().get('/admin/promo/recomenda.csv')
    check("CSV recomenda: sin sesión no se descarga", sin_sesion.status_code in (301, 302, 401), sin_sesion.status_code)

    c = app.test_client()
    _login(c, comun_id)
    check("CSV recomenda: un usuario común recibe 403", c.get('/admin/promo/recomenda.csv').status_code == 403)

    c = app.test_client()
    _login(c, admin_id)
    r = c.get('/admin/promo/recomenda.csv')
    check("CSV recomenda: el admin lo descarga", r.status_code == 200, r.status_code)
    check("CSV recomenda: es un adjunto CSV sin caché",
          r.mimetype == 'text/csv' and 'attachment' in r.headers.get('Content-Disposition', '')
          and r.headers.get('Cache-Control') == 'no-store')
    texto = r.get_data(as_text=True)
    filas = list(csv.DictReader(io.StringIO(texto.lstrip('\ufeff'))))
    emails = sorted(f['email'] for f in filas)
    check("CSV recomenda: incluye a todos salvo bajas y emails inválidos (también sin créditos)",
          emails == ['admin@foja.com', 'comun@foja.com', 'cero@foja.com'], emails)
    cero = next(f for f in filas if f['email'] == 'cero@foja.com')
    check("CSV recomenda: lleva el link personal y el de WhatsApp",
          cero['url_referido'] == link_cero and cero['url_whatsapp'].startswith('https://wa.me/?text='))
    check("CSV recomenda: un nombre que empieza con = no se ejecuta como fórmula en Excel",
          cero['nombre'].startswith("'="), cero['nombre'])
    check("CSV recomenda: el panel muestra el botón",
          'CSV campaña Recomendá Foja' in c.get('/admin/').get_data(as_text=True))


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE RUTAS ADMIN (mensajes)")
    print("=" * 70)

    test_eliminar_mensaje_no_leido_rechazado()
    test_eliminar_mensaje_leido_ok()
    test_eliminar_leidos_en_bloque()
    test_no_admin_no_puede()
    test_csv_recomenda()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
