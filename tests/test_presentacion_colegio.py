#!/usr/bin/env python3
"""Tests del mail de presentación para el Colegio (scripts/generar_mail_presentacion.py)."""

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


def _html():
    spec = importlib.util.spec_from_file_location('gen', RAIZ / 'scripts' / 'generar_mail_presentacion.py')
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    app = crear_app()
    app.config['TESTING'] = True
    return gen.renderizar(app), gen


def _texto_visible(html):
    sin_tags = re.sub(r'<style.*?</style>|<[^>]+>', ' ', html, flags=re.S)
    return re.sub(r'\s+', ' ', sin_tags)


def test_presentacion():
    html, gen = _html()
    texto = _texto_visible(html)
    check("no quedan variables de Jinja sin resolver", '{{' not in html and '{%' not in html)
    check("trae el link de baja de MailerLite una sola vez y no personaliza con nombre",
          html.count('{$unsubscribe}') == 1 and '{$name' not in html)
    check("el botón lleva al registro", 'href="https://foja.com.ar/auth/signup"' in html and 'Crear una cuenta gratis' in html)
    check("no fija precios (pueden cambiar): habla de planes accesibles", '$' not in texto and 'planes sumamente accesibles' in texto)
    check("menciona las dos descargas de prueba", 'dos descargas de prueba' in texto)
    check("incluye los casos: estudiar todo y analizar una parte", 'estudiar la causa completa' in texto and 'analizar solo un tramo' in texto)
    check("crédito del desarrollo y contacto, sin nota de saludo", 'Desarrollado por Leandro Carrazza' in texto and 'Oscar' not in texto and 'asistenciafoja@gmail.com' in texto
          and 'cordial saludo' not in texto and 'Abogado' not in texto)


def test_sin_afirmaciones_falsas_ni_primera_persona():
    html, _ = _html()
    texto = _texto_visible(html).lower()
    prohibidas = ['índice', 'indice', 'foliado', 'marcadores', 'docx', 'odt', 'jurisprudencia', 'prioridad en cola',
                  'soporte dedicado', 'factura', 'cifrad', '2fa', 'cookie', 'expira sola', 'usuarios activos']
    halladas = [p for p in prohibidas if p in texto]
    check("no afirma nada que el sistema no hace ni usa tecnicismos", not halladas, halladas)
    singular = [p for p in ['les presento', 'desarrollé', ' soy ', ' mi cuenta', 'creé ', 'quiero ', 'quedo a '] if p in texto]
    check("el texto no está en primera persona del singular", not singular, singular)
    tuteo = [p for p in [' tu ', ' tus ', ' vos ', ' tenés', ' creá ', ' pruébenlo', 'entrá ', 'conectá ', 'elegí '] if p in ' ' + texto + ' ']
    check("trato formal, sin tuteo ni voseo", not tuteo, tuteo)


if __name__ == '__main__':
    test_presentacion()
    test_sin_afirmaciones_falsas_ni_primera_persona()
    print("\nTODO OK" if not _fallos else f"\n{len(_fallos)} FALLA(S): " + ", ".join(_fallos))
    sys.exit(1 if _fallos else 0)
