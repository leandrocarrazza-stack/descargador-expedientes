#!/usr/bin/env python3
"""
Tests del popup que promociona el contador de plazos
(templates/_popup_contador_plazos.html), incluido en la presentación (/) y en
Descargar. Cubre: qué botón ve cada tipo de usuario y que deja de generarse
pasada la fecha de corte (config.POPUP_PLAZOS_HASTA).
Misma técnica que tests/test_rutas_plazos.py: app real en modo testing
(SQLite en memoria) y login escribiendo `_user_id`.
`python tests/test_popup_contador_plazos.py`
"""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor

from modulos.database import db
from modulos.models import User, SesionUsuarioMV
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:   # bajo pytest una falla tiene que hacer fallar el test
            raise AssertionError(nombre)


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _usuario(app, email, admin=False, creditos=0):
    with app.app_context():
        u = User(email=email, is_admin=admin, creditos_disponibles=creditos)
        u.establecer_password('x')
        db.session.add(u)
        db.session.commit()
        # Descargar redirige a conectar Mesa Virtual si el usuario no tiene sesión guardada
        db.session.add(SesionUsuarioMV(user_id=u.id, cookies_json='[{"name":"x","value":"y"}]', mv_usuario='mv1'))
        db.session.commit()
        return u.id


def _popup(html):
    """Solo el HTML del popup (desde su contenedor hasta su script), o '' si no está."""
    if 'id="popupPlazos"' not in html:
        return ''
    return html.split('id="popupPlazos"', 1)[1].split('<script>', 1)[0]


def _descargar(app, email, admin=False, creditos=0):
    """HTML de Descargar visto por un usuario logueado."""
    uid = _usuario(app, email, admin=admin, creditos=creditos)
    client = app.test_client()
    _login(client, uid)
    r = client.get('/descargas/expediente')
    return r.status_code, r.get_data(as_text=True)


def test_visitante_en_la_presentacion():
    r = _app().test_client().get('/')
    pop = _popup(r.get_data(as_text=True))
    check("visitante: la presentación carga", r.status_code == 200, r.status_code)
    check("visitante: aparece el popup", pop != '')
    check("visitante: el botón lleva a crear cuenta", 'href="/auth/signup" class="btn-foja">Crear cuenta gratis' in pop)
    check("visitante: marca de sesión propia", 'foja_popup_plazos_visitante' in pop)


def test_cliente_con_credito():
    status, html = _descargar(_app(), 'conc@foja.com', creditos=2)
    pop = _popup(html)
    check("con crédito: Descargar carga", status == 200, status)
    check("con crédito: el botón lleva al contador", 'href="/plazos/" class="btn-foja">Probar el contador' in pop)
    check("con crédito: marca de sesión de cliente", 'foja_popup_plazos_cliente' in pop)


def test_cliente_sin_credito_y_admin():
    status, html = _descargar(_app(), 'sinc@foja.com', creditos=0)
    check("sin crédito: el botón lleva a los planes", 'href="/pagos/planes" class="btn-foja">Ver planes' in _popup(html), status)
    status, html = _descargar(_app(), 'admin@foja.com', admin=True, creditos=0)
    check("admin sin crédito: el botón lleva al contador", 'href="/plazos/" class="btn-foja">Probar el contador' in _popup(html), status)


def test_vigencia():
    original = config.POPUP_PLAZOS_HASTA
    try:
        app = _app()
        uid = _usuario(app, 'vig@foja.com', creditos=1)
        logueado = app.test_client()
        _login(logueado, uid)
        visitante = app.test_client()

        def hay_popup():
            return (_popup(visitante.get('/').get_data(as_text=True)) != ''
                    , _popup(logueado.get('/descargas/expediente').get_data(as_text=True)) != '')

        config.POPUP_PLAZOS_HASTA = date.today()
        check("el último día de vigencia todavía aparece (inclusive)", hay_popup() == (True, True), hay_popup())
        config.POPUP_PLAZOS_HASTA = date.today() - timedelta(days=1)
        check("pasada la fecha de corte no se genera ni en la presentación ni en Descargar",
              hay_popup() == (False, False), hay_popup())
    finally:
        config.POPUP_PLAZOS_HASTA = original


def test_fecha_por_defecto_y_valor_invalido():
    check("por defecto vence el 2027-01-09 (3 meses desde octubre 2026)",
          config._POPUP_PLAZOS_HASTA_DEFAULT == date(2027, 1, 9), config._POPUP_PLAZOS_HASTA_DEFAULT)

    def leer(valor):
        """Fecha que carga config.py con POPUP_PLAZOS_HASTA=valor en el entorno (proceso aparte)."""
        import os
        entorno = {**os.environ, 'POPUP_PLAZOS_HASTA': valor}
        out = subprocess.run([sys.executable, '-c', 'import config; print(config.POPUP_PLAZOS_HASTA)'],
                             cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=60)
        return out.stdout.strip()

    check("variable de entorno válida: se respeta (para extender el popup sin tocar código)",
          leer('2027-03-31') == '2027-03-31', leer('2027-03-31'))
    check("variable mal escrita: no rompe el arranque y vuelve a la fecha por defecto",
          leer('9/1/2027') == '2027-01-09', leer('9/1/2027'))


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DEL POPUP DEL CONTADOR DE PLAZOS")
    print("=" * 70)
    test_visitante_en_la_presentacion()
    test_cliente_con_credito()
    test_cliente_sin_credito_y_admin()
    test_vigencia()
    test_fecha_por_defecto_y_valor_invalido()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
