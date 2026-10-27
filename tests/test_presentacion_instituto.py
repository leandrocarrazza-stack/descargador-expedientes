#!/usr/bin/env python3
"""Tests del mail personal para el Instituto de Derecho Procesal del CAER (scripts/generar_mail_presentacion.py --plantilla instituto)."""

import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor

from servidor import crear_app

_fallos = []
RAIZ = Path(__file__).parent.parent


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


def _html(con_baja=True):
    spec = importlib.util.spec_from_file_location('gen', RAIZ / 'scripts' / 'generar_mail_presentacion.py')
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    app = crear_app()
    app.config['TESTING'] = True
    return gen.renderizar(app, 'instituto', con_baja=con_baja)


def _texto_visible(html):
    sin_tags = re.sub(r'<style.*?</style>|<[^>]+>', ' ', html, flags=re.S)
    return re.sub(r'\s+', ' ', sin_tags)


def test_contenido():
    html = _html()
    texto = _texto_visible(html)
    check("no quedan variables de Jinja sin resolver", '{{' not in html and '{%' not in html)
    check("se dirige a la Dra. Warlet como Directora del Instituto de Derecho Procesal del CAER",
          'Estimada Dra. Warlet' in texto and 'Directora del Instituto de Derecho Procesal' in texto
          and 'Colegio de la Abogacía de Entre Ríos' in texto)
    check("informa la dirección del sitio", 'href="https://foja.com.ar"' in html and 'foja.com.ar' in texto)
    check("informa los dos créditos sin costo", 'dos créditos sin costo' in texto)
    check("pone el acento en seguridad y privacidad y aclara que la contraseña no se guarda",
          'seguridad y la privacidad' in texto and 'la contraseña no se guarda' in texto)
    check("ofrece consultas y espera la opinión", 'a su entera disposición' in texto and 'su opinión' in texto)
    check("el botón lleva al registro", 'href="https://foja.com.ar/auth/signup"' in html)
    check("no fija precios (pueden cambiar)", '$' not in texto)
    check("trato formal, sin tuteo ni voseo", not [p for p in [' tu ', ' tus ', ' vos ', ' tenés', ' creá ', 'entrá '] if p in ' ' + texto.lower() + ' '])


def test_baja():
    con = _html(con_baja=True)
    sin = _html(con_baja=False)
    check("con baja: trae el link de MailerLite una sola vez", con.count('{$unsubscribe}') == 1)
    check("sin baja: no queda ningún link de baja ni la variable de MailerLite",
          '{$unsubscribe}' not in sin and 'Dejar de recibir' not in sin)


if __name__ == '__main__':
    test_contenido()
    test_baja()
    print("\nTODO OK" if not _fallos else f"\n{len(_fallos)} FALLA(S): " + ", ".join(_fallos))
    sys.exit(1 if _fallos else 0)
