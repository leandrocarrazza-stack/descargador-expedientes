#!/usr/bin/env python3
"""
Tests de las páginas legales: /terminos y /privacidad son públicas, el pie de
página muestra a la persona responsable solo si están sus datos completos, y
el registro exige aceptar los términos y deja constancia de la versión.
`python tests/test_rutas_legal.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

import modulos.auth as auth_mod
from modulos.database import db
from modulos.models import User
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _afirmar_sin_fallos():
    """Hace que pytest falle si algún check() del test falló (el script ya lo informa)."""
    fallos = list(_fallos)
    assert not fallos, fallos


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _setear_responsable(nombre='EJEMPLO Nombre Apellido', cuit='20-00000000-0',
                        domicilio='EJEMPLO 123, Paraná'):
    config.RESPONSABLE_NOMBRE = nombre
    config.RESPONSABLE_CUIT = cuit
    config.RESPONSABLE_DOMICILIO = domicilio


def test_paginas_publicas():
    client = _app().test_client()
    r = client.get('/terminos')
    check("/terminos responde 200 sin login", r.status_code == 200, r.status_code)
    check("/terminos muestra la versión vigente", config.TERMINOS_VERSION in r.get_data(as_text=True))
    r = client.get('/privacidad')
    check("/privacidad redirige al art. 25 de los términos",
          r.status_code == 302 and r.headers['Location'].endswith('/terminos#art-25'),
          f"{r.status_code} {r.headers.get('Location')}")
    _afirmar_sin_fallos()


def test_pie_con_y_sin_responsable():
    client = _app().test_client()

    _setear_responsable()
    html = client.get('/').get_data(as_text=True)
    check("pie muestra al responsable cuando los datos están completos",
          'EJEMPLO Nombre Apellido' in html and '20-00000000-0' in html)
    check("pie enlaza a los términos y no tiene un enlace aparte a la política de privacidad",
          'href="/terminos"' in html and 'Política de privacidad' not in html and '/privacidad' not in html)

    _setear_responsable(cuit='')
    html = client.get('/').get_data(as_text=True)
    check("pie no muestra bloque a medias si falta un dato", 'Responsable:' not in html)
    _setear_responsable()
    _afirmar_sin_fallos()


def _set_legal(terminos_borrador, email='ejemplo@foja.com.ar'):
    config.TERMINOS_BORRADOR = terminos_borrador
    config.RESPONSABLE_EMAIL = email


def test_terminos_definitivos():
    client = _app().test_client()
    _setear_responsable()
    _set_legal(terminos_borrador=False)
    try:
        html = client.get('/terminos').get_data(as_text=True)
        check("términos definitivos: sin cartel de borrador", 'Documento en preparación' not in html)
        check("términos definitivos: datos del responsable en el art. 1",
              'titularidad de EJEMPLO Nombre Apellido, CUIT 20-00000000-0' in html)
        check("términos definitivos: sin placeholders ni marcas pendientes",
              not any(m in html for m in ('[RESPONSABLE', '[TERMINOS', '[EMAIL', 'A CONFIRMAR', 'A DEFINIR', 'Nota de uso')))
        check("términos definitivos: la retención sale de la configuración",
              f'{config.RETENCION_PDF_DIAS} días desde el último acceso' in html)
        check("términos: están los 25 artículos", all(f'id="art-{n}"' in html for n in range(1, 26)))
        check("términos: incluye la decisión sobre créditos con piezas omitidas",
              'aun cuando alguna pieza haya sido omitida' in html)
        check("términos: incluye el pago aprobado y no acreditado", 'no se acreditaran en la cuenta' in html)

        check("art. 25: finalidad exclusiva de los datos",
              'no utiliza los datos de los Usuarios para ninguna finalidad distinta' in html)
        check("art. 25: el Titular no accede a los expedientes descargados",
              'El Titular no accede a su contenido, no los consulta ni los utiliza' in html)
        check("art. 25: ni otros usuarios ni administradores pueden verlos",
              'ni a administradores del Servicio ver o descargar esos archivos' in html)
        check("art. 25: leyenda de la AAIP", 'ÓRGANO DE CONTROL DE LA LEY N° 25.326' in html)
        check("art. 19 remite al art. 25", 'href="#art-25"' in html)
        check("art. 9: programa de recomendación con premio, tope y primera compra",
              'Programa de recomendación' in html and '2 (dos) créditos por cada Recomendado' in html
              and 'hasta 10 (diez) Recomendados' in html and 'primera compra de créditos del Recomendado' in html)
        check("art. 9 y 11: los créditos de recomendación no son canjeables ni reembolsables",
              'no son canjeables por dinero' in html and 'ni tampoco los Créditos de Recomendación' in html)
        check("art. 25: datos del programa de recomendación", 'el vínculo entre el Usuario que recomendó Foja' in html)
        check("art. 9: el título figura en el índice", 'programa de recomendación</a></li>' in html)

        _set_legal(terminos_borrador=False, email='')
        html = client.get('/terminos').get_data(as_text=True)
        check("sin email del responsable el texto no se publica como definitivo",
              'Documento en preparación' in html and 'titularidad de' not in html)

        _set_legal(terminos_borrador=False)
        config.RESPONSABLE_CUIT = ''
        html = client.get('/terminos').get_data(as_text=True)
        check("sin CUIT el texto no se publica como definitivo",
              'Documento en preparación' in html and 'titularidad de' not in html)
    finally:
        _set_legal(terminos_borrador=True, email='')
        _setear_responsable()
    _afirmar_sin_fallos()


def test_signup_exige_terminos_y_deja_constancia():
    # validar_email consulta el DNS del dominio; el test no depende de la red
    auth_mod.validar_email = lambda e: (True, e.strip().lower(), None)
    app = _app()
    client = app.test_client()
    base = {'email': 'legal1@foja.com', 'nombre': 'Ana', 'password': 'Password123!'}

    r = client.post('/auth/signup', json=base)
    check("signup sin terminos devuelve 400", r.status_code == 400, r.status_code)

    r = client.post('/auth/signup', json={**base, 'terminos': False})
    check("signup con terminos=false devuelve 400", r.status_code == 400, r.status_code)

    r = client.post('/auth/signup', json={**base, 'terminos': True},
                    headers={'X-Forwarded-For': '203.0.113.7, 10.0.0.1'})
    check("signup aceptando términos devuelve 200", r.status_code == 200, r.status_code)
    with app.app_context():
        u = User.query.filter_by(email='legal1@foja.com').first()
        check("queda guardada la versión aceptada", u and u.terminos_version == config.TERMINOS_VERSION)
        check("queda guardada la fecha de aceptación", u and u.terminos_aceptados_en is not None)
        check("queda guardada la IP del cliente", u and u.terminos_aceptados_ip == '203.0.113.7',
              u.terminos_aceptados_ip if u else None)
    _afirmar_sin_fallos()


if __name__ == '__main__':
    test_paginas_publicas()
    test_pie_con_y_sin_responsable()
    test_terminos_definitivos()
    test_signup_exige_terminos_y_deja_constancia()
    if _fallos:
        print(f"\n{len(_fallos)} fallo(s): {_fallos}")
        sys.exit(1)
    print("\nTodo OK")
