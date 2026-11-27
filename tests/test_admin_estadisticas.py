#!/usr/bin/env python3
"""
Tests de las estadísticas de uso:
  - modulos/estadisticas.py: totales y agrupación por mes con datos sembrados.
  - /admin/estadisticas: 403 para no admin, 200 para admin.
  - Registro de eventos: visita a inicio (sin sesión) y cálculo de plazos.
  - La baja de cuenta borra los eventos del usuario.
`python tests/test_admin_estadisticas.py` o `pytest tests/test_admin_estadisticas.py`
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

from modulos.database import db
from modulos.estadisticas import calcular_estadisticas
from modulos.models import CompraCreditos, EventoUso, ExpedienteDescargado, User
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


def _usuario(email, admin=False, creditos=0, creado=None):
    u = User(email=email, is_admin=admin, creditos_disponibles=creditos,
             terminos_version=config.TERMINOS_VERSION)
    u.establecer_password('x')
    if creado:
        u.creado_en = creado
    db.session.add(u)
    db.session.commit()
    return u.id


def _cliente(app, uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    return c


def test_calculo_con_datos_sembrados():
    app = _app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        ahora = datetime(2026, 11, 15, 15, 0)
        mes_pasado = datetime(2026, 10, 10, 15, 0)
        a = _usuario('a@example.com', creado=mes_pasado)
        b = _usuario('b@example.com', creado=ahora)
        db.session.add_all([
            ExpedienteDescargado(user_id=a, numero='1', tribunal='Juzgado X', estado='completed', creado_en=ahora),
            ExpedienteDescargado(user_id=a, numero='2', tribunal='Juzgado X', estado='completed',
                                 es_actualizacion=True, creado_en=mes_pasado),
            ExpedienteDescargado(user_id=a, numero='3', estado='failed', creado_en=ahora),
            CompraCreditos(user_id=a, stripe_payment_id='p1', creditos_comprados=5, monto_pagado=1000,
                           plan='x', estado='completed', creado_en=ahora, completado_en=ahora),
            CompraCreditos(user_id=a, stripe_payment_id='p2', creditos_comprados=5, monto_pagado=500,
                           plan='x', estado='pending', creado_en=ahora),
            EventoUso(tipo='visita', ruta='inicio', creado_en=ahora),
            EventoUso(tipo='plazo_calculado', ruta='plazos', user_id=a, creado_en=ahora),
            EventoUso(tipo='plazo_calculado', ruta='plazos', user_id=a, creado_en=mes_pasado),
        ])
        db.session.commit()

        est = calcular_estadisticas(ahora=ahora)
        t = est['totales']
        check("usuarios", t['usuarios'] == 2)
        check("usuarios con y sin descarga", (t['usuarios_con_descarga'], t['usuarios_sin_descarga']) == (1, 1))
        check("descargas completas y actualizaciones", (t['descargas'], t['actualizaciones']) == (2, 1))
        check("fallidas y tasa de éxito", (t['fallidas'], t['tasa_exito']) == (1, 67))
        check("solo cuentan compras completadas", (t['compras'], t['ingresos']) == (1, 1000))
        check("visitas y plazos", (t['visitas'], t['plazos']) == (1, 2))
        meses = {m['mes']: m for m in est['por_mes']}
        check("12 meses, el actual al final", len(est['por_mes']) == 12 and est['por_mes'][-1]['mes'] == '2026-11')
        check("noviembre", meses['2026-11']['descargas'] == 1 and meses['2026-11']['plazos'] == 1
              and meses['2026-11']['usuarios_nuevos'] == 1 and meses['2026-11']['ingresos'] == 1000)
        check("octubre", meses['2026-10']['descargas'] == 1 and meses['2026-10']['plazos'] == 1)
        check("ranking de usuarios", est['top_usuarios'][0] == {'email': 'a@example.com', 'descargas': 2})
        check("ranking de tribunales", est['tribunales'][0] == {'tribunal': 'Juzgado X', 'descargas': 2})
        db.drop_all()


def test_ruta_solo_admin():
    app = _app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = _usuario('admin@example.com', admin=True)
        comun = _usuario('comun@example.com')
    check("sin sesión: no entra", app.test_client().get('/admin/estadisticas').status_code in (302, 401))
    check("usuario común: 403", _cliente(app, comun).get('/admin/estadisticas').status_code == 403)
    r = _cliente(app, admin).get('/admin/estadisticas')
    check("admin: 200", r.status_code == 200)
    check("admin: muestra la página", 'Estadísticas de uso' in r.get_data(as_text=True))


def test_registro_de_eventos():
    app = _app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        uid = _usuario('p@example.com', creditos=3)
    app.test_client().get('/')
    with app.app_context():
        e = EventoUso.query.all()
        check("visita a inicio sin sesión", len(e) == 1 and e[0].tipo == 'visita' and e[0].ruta == 'inicio'
              and e[0].user_id is None)

    cli = _cliente(app, uid)
    r = cli.post('/plazos/api/calcular', json={
        'tipo_notificacion': 'cedula', 'fecha': '2026-10-05', 'dias': 5, 'modo': 'habiles'})
    check("el cálculo responde bien", r.status_code == 200 and r.get_json().get('exito'), r.get_data(as_text=True)[:200])
    with app.app_context():
        pl = EventoUso.query.filter_by(tipo='plazo_calculado').all()
        check("cálculo de plazos registrado con usuario", len(pl) == 1 and pl[0].user_id == uid)

    # Un cálculo inválido no cuenta
    cli.post('/plazos/api/calcular', json={'fecha': 'mal', 'dias': 5})
    with app.app_context():
        check("cálculo inválido no se registra", EventoUso.query.filter_by(tipo='plazo_calculado').count() == 1)


def test_registro_no_afecta_la_sesion_ni_las_descargas():
    """Registrar un evento no debe tocar db.session ni romper la página si la BD falla."""
    import modulos.models as models_mod
    from sqlalchemy import event
    app = _app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        uid = _usuario('q@example.com', creditos=3)
        # Una descarga "en curso" pendiente en la sesión, con un cambio sin guardar
        d = ExpedienteDescargado(user_id=uid, numero='999', estado='pending')
        db.session.add(d)
        db.session.commit()
        d.porcentaje = 40   # cambio pendiente de la sesión (no confirmado)
        antes_sucio = db.session.is_modified(d)

        models_mod.registrar_evento('visita', 'descargar')

        # Medir la sesión ANTES de consultar: una query hace autoflush y borraría la marca
        conserva = antes_sucio and db.session.is_modified(d) and d.porcentaje == 40
        check("la sesión conserva su cambio pendiente (no hizo commit/rollback)", conserva)
        check("el evento se guardó", EventoUso.query.filter_by(ruta='descargar').count() == 1)
        db.session.rollback()

        # Si la inserción falla, no levanta excepción y la sesión sigue usable
        def _falla(*a, **k):
            raise RuntimeError('BD caída (simulada)')
        evt = event.listen(db.engine, 'before_cursor_execute', _falla)
        try:
            models_mod.registrar_evento('visita', 'descargar')
            resultado = 'sin excepción'
        except Exception as e:
            resultado = f'levantó {e}'
        finally:
            event.remove(db.engine, 'before_cursor_execute', _falla)
        check("si falla el registro no levanta excepción", resultado == 'sin excepción', resultado)
        check("la sesión sigue usable tras el fallo", ExpedienteDescargado.query.count() == 1)
        db.drop_all()


if __name__ == '__main__':
    test_calculo_con_datos_sembrados()
    test_ruta_solo_admin()
    test_registro_de_eventos()
    test_registro_no_afecta_la_sesion_ni_las_descargas()
    print("\nFALLAS:" if _fallos else "\nTodo OK", _fallos or "")
    sys.exit(1 if _fallos else 0)
