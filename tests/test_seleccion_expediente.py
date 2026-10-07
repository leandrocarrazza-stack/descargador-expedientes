#!/usr/bin/env python3
"""
Tests de la selección del expediente: verificación de identidad (no sólo
posición en la lista) y que un click fallido NO siga con la página de
resultados como si fuera el expediente. Sin Selenium: se usan dobles de
prueba. `python test_seleccion_expediente.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import modulos.navegacion as nav_mod
import modulos.pipeline as pipeline_mod
from modulos.navegacion import BuscadorExpedientes
from modulos.pipeline import PipelineDescargador

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def test_coincide_identidad():
    print("\n[1] _coincide_identidad")
    c = BuscadorExpedientes._coincide_identidad

    exp = {'url': 'https://mv/expedientes/abc123', 'caratula': 'PEREZ c/ GOMEZ s/ Daños', 'tribunal': 'Juzgado Civil 2'}

    check("misma URL coincide",
          c(exp, {'url': 'https://mv/expedientes/abc123', 'caratula': 'otra cosa'}))
    check("URL distinta NO coincide aunque la carátula sea igual",
          not c(exp, {'url': 'https://mv/expedientes/zzz999', 'caratula': 'PEREZ c/ GOMEZ s/ Daños'}))

    sin_url = {'url': '', 'caratula': 'PEREZ  c/ GOMEZ s/ Daños', 'tribunal': 'Juzgado Civil 2'}
    check("sin URL: misma carátula (espacios/mayúsculas distintos) coincide",
          c(sin_url, {'url': '', 'caratula': 'perez c/ gomez s/ daños', 'tribunal': 'juzgado civil 2'}))
    check("sin URL: carátula distinta NO coincide",
          not c(sin_url, {'url': '', 'caratula': 'LOPEZ c/ DIAZ s/ Daños', 'tribunal': 'Juzgado Civil 2'}))
    check("sin URL: misma carátula pero otro tribunal NO coincide",
          not c(sin_url, {'url': '', 'caratula': 'PEREZ c/ GOMEZ s/ Daños', 'tribunal': 'Cámara Civil'}))
    check("sin URL ni carátula esperada NO coincide con nada",
          not c(sin_url, {'url': '', 'caratula': '', 'tribunal': ''}))
    check("tribunal 'no especificado' no bloquea la coincidencia",
          c({'url': '', 'caratula': 'A c/ B', 'tribunal': 'Tribunal no especificado'},
            {'url': '', 'caratula': 'A c/ B', 'tribunal': 'Juzgado X'}))


def test_click_fallido_lanza_error():
    print("\n[2] _clickear_expediente lanza error si no puede entrar")

    class FakeDriver:
        def find_elements(self, *a, **k):
            return []

    class WaitQueFalla:
        def __init__(self, *a, **k):
            pass

        def until(self, *a, **k):
            raise Exception("timeout")

    original = nav_mod.WebDriverWait
    nav_mod.WebDriverWait = WaitQueFalla
    try:
        b = BuscadorExpedientes(None)
        error = None
        try:
            b._clickear_expediente(FakeDriver(), 0, {'url': ''})
        except Exception as e:
            error = str(e)
    finally:
        nav_mod.WebDriverWait = original

    check("lanzó excepción", error is not None)
    check("con el marcador NO_SE_PUDO_ENTRAR_AL_EXPEDIENTE", error is not None and "NO_SE_PUDO_ENTRAR_AL_EXPEDIENTE" in error, error)


class FakeCliente:
    driver = None

    def cerrar(self):
        pass


def _ejecutar_con_buscador(fake_buscador_cls, **kwargs):
    p = PipelineDescargador()
    p._autenticar = lambda cookies_mv: FakeCliente()
    original = pipeline_mod.BuscadorExpedientes
    pipeline_mod.BuscadorExpedientes = fake_buscador_cls
    try:
        return p.ejecutar('1/24', cookies_mv=[{'name': 'x', 'value': 'y'}], **kwargs)
    finally:
        pipeline_mod.BuscadorExpedientes = original


def test_pipeline_traduce_errores():
    print("\n[3] pipeline: errores de selección -> mensajes claros, sin éxito")

    class NoCoincide:
        def __init__(self, cliente):
            self._opciones_multiples = []

        def buscar(self, numero, indice_expediente=None, identidad=None):
            raise Exception("Error en búsqueda: EXPEDIENTE_NO_COINCIDE")

    r = _ejecutar_con_buscador(NoCoincide, identidad_expediente={'caratula': 'A c/ B'})
    check("no es éxito", r.exito is False)
    check("tipo_error expediente_no_coincide", r.tipo_error == 'expediente_no_coincide', r.tipo_error)

    class NoAbre:
        def __init__(self, cliente):
            self._opciones_multiples = []

        def buscar(self, numero, indice_expediente=None, identidad=None):
            raise Exception("Error en búsqueda: NO_SE_PUDO_ENTRAR_AL_EXPEDIENTE: x")

    r = _ejecutar_con_buscador(NoAbre)
    check("no es éxito (click fallido)", r.exito is False)
    check("tipo_error no_se_pudo_abrir", r.tipo_error == 'no_se_pudo_abrir', r.tipo_error)


def test_pipeline_pasa_identidad_solo_si_existe():
    print("\n[4] pipeline: pasa `identidad` al buscador sólo cuando hay una")

    llamadas = []

    class Registrador:
        def __init__(self, cliente):
            self._opciones_multiples = []

        def buscar(self, numero, indice_expediente=None, **kwargs):
            llamadas.append(kwargs)
            return None  # corta el pipeline en 'not_found'

    ident = {'url': '', 'caratula': 'A c/ B', 'tribunal': 'J1'}
    _ejecutar_con_buscador(Registrador, identidad_expediente=ident)
    _ejecutar_con_buscador(Registrador)

    check("con identidad: se la pasa al buscador", llamadas[0] == {'identidad': ident}, llamadas[0])
    check("sin identidad: no agrega kwargs (compatible con buscadores viejos)", llamadas[1] == {}, llamadas[1])


if __name__ == "__main__":
    test_coincide_identidad()
    test_click_fallido_lanza_error()
    test_pipeline_traduce_errores()
    test_pipeline_pasa_identidad_solo_si_existe()

    print()
    if _fallos:
        print(f"FALLARON {len(_fallos)}: {_fallos}")
        sys.exit(1)
    print("TODOS OK")
