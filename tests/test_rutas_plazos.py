#!/usr/bin/env python3
"""
Tests de las rutas del contador de plazos (rutas/plazos.py) y del calendario
del panel admin (rutas/admin.py). Misma técnica que tests/test_rutas_admin.py:
app real en modo testing (SQLite en memoria) y login escribiendo `_user_id`.
`python tests/test_rutas_plazos.py`
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor

from modulos.database import db
from modulos.models import User, DiaInhabil
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


def _usuario(app, email, plan_max=None, admin=False, creditos=0):
    with app.app_context():
        u = User(email=email, is_admin=admin, plan_max_comprado=plan_max, creditos_disponibles=creditos)
        u.establecer_password('x')
        db.session.add(u)
        db.session.commit()
        return u.id


BODY = {'tipo_notificacion': 'cedula', 'fecha': '2026-10-05', 'dias': 5, 'modo': 'habiles',
        'localidad': '', 'fuero': ''}


def test_sin_login():
    client = _app().test_client()
    r = client.get('/plazos/')
    check("GET /plazos/ sin login → redirige al login", r.status_code == 302 and '/auth/login' in r.location, r.status_code)
    r = client.post('/plazos/api/calcular', json=BODY)
    check("POST calcular sin login → redirige", r.status_code == 302, r.status_code)


def test_sin_plan():
    app = _app()
    uid = _usuario(app, 'sinplan@foja.com', plan_max='estudio')   # compró antes, pero sin crédito
    client = app.test_client()
    _login(client, uid)
    r = client.get('/plazos/')
    html = r.get_data(as_text=True)
    check("sin crédito: la pantalla carga con la invitación", r.status_code == 200 and 'Ver planes' in html
          and 'id="form-plazo"' not in html, r.status_code)
    r = client.post('/plazos/api/calcular', json=BODY)
    check("sin crédito (aunque haya comprado antes): la API devuelve 403",
          r.status_code == 403 and r.get_json().get('tipo_error') == 'plan_requerido', r.status_code)


def test_con_plan():
    app = _app()
    uid = _usuario(app, 'conplan@foja.com', plan_max='individual', creditos=2)
    client = app.test_client()
    _login(client, uid)
    r = client.get('/plazos/')
    html = r.get_data(as_text=True)
    check("con plan: se ve el formulario con localidades y fueros",
          r.status_code == 200 and 'id="form-plazo"' in html and 'Gualeguaychú' in html and 'Laboral' in html, r.status_code)
    check("el menú tiene el link a Plazos", 'href="/plazos/"' in html)

    # lunes 5/10/2026, 5 hábiles; el lunes 12/10 es feriado → vence martes 13/10
    r = client.post('/plazos/api/calcular', json=BODY)
    data = r.get_json()
    check("cálculo: 5 hábiles desde 5/10/2026 → 13/10/2026",
          r.status_code == 200 and data['exito'] and data['resultado']['vencimiento'] == '2026-10-13', data)
    check("la respuesta trae detalle y la hora de gracia",
          len(data['resultado']['detalle']) == 8 and data['resultado']['gracia']['hora'] == '09:00', data['resultado']['gracia'])

    r = client.post('/plazos/api/calcular', json={**BODY, 'nombre': 'Contestación Exp. 12345', 'localidad': 'Paraná'})
    cal = r.get_json()['calendario']
    check("la respuesta trae el evento de calendario (Google + .ics)",
          cal['google_url'].startswith('https://calendar.google.com/calendar/render?')
          and 'dates=20261013%2F20261014' in cal['google_url'].replace('/', '%2F')
          and cal['ics'].startswith('BEGIN:VCALENDAR') and 'Contestación Exp. 12345' in cal['titulo'], cal['titulo'])

    r = client.post('/plazos/api/calcular', json={**BODY, 'tipo_notificacion': 'sne', 'fecha': '2026-10-07'})
    check("SNE: miércoles 7/10 → perfecciona viernes 9/10",
          r.get_json()['resultado']['fecha_perfeccion'] == '2026-10-09', r.get_json())

    r = client.post('/plazos/api/calcular', json={**BODY, 'modo': 'corridos', 'dias': 10, 'fecha': '2026-10-01'})
    check("corridos: 10 días desde 1/10 → prórroga al martes 13/10",
          r.get_json()['resultado']['vencimiento'] == '2026-10-13', r.get_json())

    r = client.post('/plazos/api/calcular', json={**BODY, 'localidad': 'Paraná'})
    check("con localidad válida → 200", r.status_code == 200)

    malos = [({'fecha': 'no-es-fecha'}, 'fecha inválida'), ({'dias': 0}, '0 días'), ({'dias': 'abc'}, 'días no numéricos'),
             ({'dias': 100000}, 'demasiados días'), ({'localidad': 'Atlantis'}, 'localidad desconocida'),
             ({'fuero': 'marciano'}, 'fuero inválido'), ({'modo': 'raro'}, 'modo inválido'),
             ({'tipo_notificacion': 'raro'}, 'tipo inválido'), ({'fecha': '1900-01-01'}, 'fecha fuera de rango')]
    for cambio, nombre in malos:
        r = client.post('/plazos/api/calcular', json={**BODY, **cambio})
        check(f"entrada inválida ({nombre}) → 400", r.status_code == 400 and not r.get_json()['exito'], r.status_code)
    r = client.post('/plazos/api/calcular', data='esto no es json', content_type='text/plain')
    check("cuerpo que no es JSON → 400", r.status_code == 400, r.status_code)
    for nombre, cuerpo in (('lista', [1, 2]), ('texto', 'hola'), ('número', 5), ('null', None)):
        r = client.post('/plazos/api/calcular', json=cuerpo)
        check(f"JSON que no es un objeto ({nombre}) → 400, no 500", r.status_code == 400, r.status_code)
    raros = [({'fuero': ['civil']}, 'fuero como lista'), ({'modo': {'a': 1}}, 'modo como objeto'),
             ({'tipo_notificacion': 5}, 'tipo numérico'), ({'localidad': ['Paraná']}, 'localidad como lista'),
             ({'nombre': {'x': 1}}, 'nombre como objeto'), ({'dias': True}, 'días booleano'),
             ({'dias': 2.5}, 'días decimal'), ({'dias': [3]}, 'días como lista')]
    for cambio, nombre in raros:
        r = client.post('/plazos/api/calcular', json={**BODY, **cambio})
        check(f"campo de tipo inesperado ({nombre}) → 400, no 500", r.status_code == 400, r.status_code)
    r = client.post('/plazos/api/calcular', data='{"dias": 1e999, "fecha": "2026-10-05"}', content_type='application/json')
    check("días = infinito → 400, no 500", r.status_code == 400, r.status_code)
    r = client.post('/plazos/api/calcular', json={**BODY, 'nombre': 'x' * 7000})
    check("un nombre larguísimo se acepta pero se recorta", r.status_code == 200
          and len(r.get_json()['calendario']['titulo']) <= 140, r.status_code)
    r = client.post('/plazos/api/calcular', json={**BODY, 'nombre': 'x' * 20000})
    check("cuerpo de más de 8 KB → 413", r.status_code == 413, r.status_code)


def test_calendario_admin():
    app = _app()
    admin_id = _usuario(app, 'admin@foja.com', admin=True)
    user_id = _usuario(app, 'comun@foja.com', plan_max='estudio', creditos=1)

    comun = app.test_client()
    _login(comun, user_id)
    check("usuario común no entra al calendario admin", comun.get('/admin/calendario').status_code == 403)
    check("usuario común no puede cargar días", comun.post('/admin/calendario/nuevo', data={}).status_code == 403)

    admin = app.test_client()
    _login(admin, admin_id)
    r = admin.get('/admin/calendario?anio=2026')
    check("admin ve el calendario", r.status_code == 200 and 'Calendario judicial' in r.get_data(as_text=True), r.status_code)

    r = admin.post('/admin/calendario/nuevo', data={
        'desde': '2026-10-08', 'hasta': '', 'tipo': 'inhabil_judicial', 'motivo': 'Acuerdo de prueba',
        'alcance': 'provincia', 'fuero': ''})
    check("carga manual → redirige", r.status_code == 302, r.status_code)
    with app.app_context():
        fila = DiaInhabil.query.filter_by(fuente='manual').first()
        check("quedó guardado como manual", fila is not None and fila.fecha_desde == date(2026, 10, 8))
        fila_id = fila.id

    # El día cargado a mano ya cuenta: cédula 5/10, 5 hábiles: 6, 7, (8 inhábil), 9, 13 (el 12 es feriado), 14
    r = comun.post('/plazos/api/calcular', json={**BODY})
    check("el día manual se descuenta en el cómputo (vence 14/10)", r.get_json()['resultado']['vencimiento'] == '2026-10-14',
          r.get_json()['resultado']['vencimiento'])

    admin.post(f'/admin/calendario/{fila_id}/activo')
    r = comun.post('/plazos/api/calcular', json={**BODY})
    check("desactivado, deja de descontarse (vuelve al 13/10)", r.get_json()['resultado']['vencimiento'] == '2026-10-13')

    r = admin.post('/admin/calendario/nuevo', data={'desde': '2026-10-14', 'tipo': 'inhabil_judicial', 'motivo': '',
                                                    'alcance': 'provincia'})
    check("carga sin motivo → redirige sin guardar", r.status_code == 302)
    with app.app_context():
        check("sigue habiendo una sola fila", DiaInhabil.query.count() == 1)

    admin.post(f'/admin/calendario/{fila_id}/eliminar')
    with app.app_context():
        check("se puede borrar un día manual", DiaInhabil.query.count() == 0)

    # El botón "Sincronizar ahora" llama a sincronizar_todo (acá simulado: los tests no salen a internet)
    from modulos.plazos import sync_stjer
    original = sync_stjer.sincronizar_todo
    sync_stjer.sincronizar_todo = lambda _db: {'pagina': {'resultado': 'ok'}, 'rss': {'resultado': 'sin_cambios'}}
    try:
        r = admin.post('/admin/calendario/sincronizar')
        check("sincronizar ahora → redirige enseguida (corre en segundo plano)", r.status_code == 302, r.status_code)
        if sync_stjer.ultimo_hilo_manual:
            sync_stjer.ultimo_hilo_manual.join(10)
    finally:
        sync_stjer.sincronizar_todo = original


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE RUTAS DE PLAZOS")
    print("=" * 70)
    test_sin_login()
    test_sin_plan()
    test_con_plan()
    test_calendario_admin()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
