#!/usr/bin/env python3
"""
Tests de los mails promocionales (modulos/promos.py, rutas/promos.py y
scripts/exportar_destinatarios_promo.py). `python tests/test_promos.py`
"""

import importlib.util
import sys
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


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _usuario(email, nombre=None, creditos=0, promos_ok=None):
    u = User(email=email, nombre=nombre, creditos_disponibles=creditos, recibir_promociones=promos_ok)
    u.establecer_password('x')
    db.session.add(u)
    db.session.commit()
    return u


def test_token():
    t = promos.token_baja(7)
    check("el token vuelve al id de usuario", promos.usuario_id_desde_token(t) == 7)
    check("un token alterado se rechaza", promos.usuario_id_desde_token(t[:-3] + 'abc') is None)
    check("basura se rechaza", promos.usuario_id_desde_token('hola') is None)
    check("un token de otra clave se rechaza", promos.usuario_id_desde_token(
        __import__('itsdangerous').URLSafeSerializer('otra-clave', salt=promos.SALT_BAJA).dumps(7)) is None)
    check("el link de baja usa la URL pública", promos.url_baja(7).startswith(config.BASE_URL.rstrip('/') + '/promociones/baja/'))


def test_render():
    app = _app()
    with app.app_context():
        u = _usuario('cli@gmail.com', nombre='María <b>Pérez</b>', creditos=3)
        mail = promos.renderizar_promo('contador_plazos', u, anio=2026)
        h, t = mail['html'], mail['texto']
        check("asunto de la campaña", mail['asunto'].startswith('Nuevo en Foja'))
        check("remitente asistenciafoja@gmail.com", mail['remitente'] == 'asistenciafoja@gmail.com' and 'asistenciafoja@gmail.com' in h)
        check("el HTML trae el botón al contador", 'href="' + config.BASE_URL.rstrip('/') + '/plazos/"' in h and 'Probar el contador' in h)
        hero = config.BASE_URL.rstrip('/') + '/static/img/promo/hero_contador_plazos.png'
        check("la imagen del encabezado apunta a la URL pública y existe en static/", f'src="{hero}"' in h
              and (Path(__file__).parent.parent / 'static' / 'img' / 'promo' / 'hero_contador_plazos.png').exists())
        check("el ejemplo muestra el vencimiento calculado", 'Jueves 26 de noviembre de 2026' in h and '26 de noviembre de 2026' in t)
        check("el HTML y el texto traen el link de baja del usuario", promos.url_baja(u.id) in h and promos.url_baja(u.id) in t)
        check("menciona Google Calendar, .ics, 3 ejemplos y 4 pasos", 'Google Calendar' in h and '.ics' in h
              and 'Te notificaron' in h and 'feria' in h and 'Caducidad' in h and all(f'>{n}</span>' in h for n in '1234'))
        check("no quedan variables sin reemplazar", '{{' not in h and '{{' not in t and '{%' not in h)
        check("el nombre con HTML se escapa en el correo HTML", '<b>Pérez' not in h and 'María' in h)
        check("el saludo usa el primer nombre", 'María' in h and 'Pérez' not in t.split('\n')[0])
        sin_nombre = _usuario('sn@gmail.com', creditos=1)
        check("sin nombre, saludo neutro", promos.renderizar_promo('contador_plazos', sin_nombre)['texto'].startswith('Hola,'))
        try:
            promos.renderizar_promo('no_existe', u)
            check("campaña desconocida → error", False)
        except ValueError:
            check("campaña desconocida → error", True)


def test_ejemplo_del_mail_coincide_con_el_motor():
    """El mail muestra un cálculo de ejemplo; si cambian las reglas, este test avisa que hay que actualizarlo."""
    from datetime import date
    from modulos.plazos.calendario import Calendario
    from modulos.plazos.calculo import calcular
    r = calcular(Calendario([]), tipo_notificacion='sne', fecha=date(2026, 11, 6), dias=10)
    check("ejemplo del mail: perfecciona martes 10/11", r.fecha_perfeccion == date(2026, 11, 10))
    check("ejemplo del mail: vence jueves 26/11/2026", r.vencimiento == date(2026, 11, 26))
    check("ejemplo del mail: hora de gracia viernes 27/11 09:00", (r.gracia_fecha, r.gracia_hora) == (date(2026, 11, 27), '09:00'))


def test_baja():
    app = _app()
    with app.app_context():
        a = _usuario('a@gmail.com', creditos=1)
        b = _usuario('b@gmail.com', creditos=1)
        ta, a_id, b_id = promos.token_baja(a.id), a.id, b.id
    c = app.test_client()
    r = c.get(f'/promociones/baja/{ta}')
    check("GET muestra la confirmación", r.status_code == 200 and 'Confirmar baja' in r.get_data(as_text=True), r.status_code)
    with app.app_context():
        check("GET no da de baja (los antivirus abren los links)", db.session.get(User, a_id).recibir_promociones is None)
    r = c.post(f'/promociones/baja/{ta}')
    check("POST da de baja", r.status_code == 200 and 'ya no vas a recibir' in r.get_data(as_text=True))
    with app.app_context():
        check("el usuario queda con recibir_promociones = False", db.session.get(User, a_id).recibir_promociones is False)
        check("otro usuario no se toca", db.session.get(User, b_id).recibir_promociones is None)
    r = c.get(f'/promociones/baja/{ta}')
    check("si ya está de baja, lo dice", 'Ya estás dado de baja' in r.get_data(as_text=True))
    r = c.post('/promociones/baja/token-roto')
    check("token inválido → 400 y no cambia nada", r.status_code == 400)


def test_exportador():
    spec = importlib.util.spec_from_file_location('exportador', Path(__file__).parent.parent / 'scripts' / 'exportar_destinatarios_promo.py')
    exportador = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exportador)
    app = _app()
    with app.app_context():
        db.session.query(User).delete()
        _usuario('con@gmail.com', creditos=2)
        _usuario('false@gmail.com', creditos=2, promos_ok=False)
        _usuario('true@gmail.com', creditos=1, promos_ok=True)
        _usuario('sin@gmail.com', creditos=0)
        _usuario('roto', creditos=5)
        emails = sorted(u.email for u in exportador.destinatarios(User))
        check("solo con crédito, sin baja y con email válido", emails == ['con@gmail.com', 'true@gmail.com'], emails)


def test_acceso_por_credito():
    from rutas.plazos import tiene_acceso
    check("con crédito entra", tiene_acceso(User(creditos_disponibles=1, is_admin=False)))
    check("sin crédito no entra", not tiene_acceso(User(creditos_disponibles=0, is_admin=False)))
    check("haber comprado antes ya no alcanza", not tiene_acceso(User(creditos_disponibles=0, plan_max_comprado='matricula', is_admin=False)))
    check("admin siempre entra", tiene_acceso(User(creditos_disponibles=0, is_admin=True)))


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE MAILS PROMOCIONALES")
    print("=" * 70)
    test_token()
    test_render()
    test_ejemplo_del_mail_coincide_con_el_motor()
    test_baja()
    test_exportador()
    test_acceso_por_credito()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
