#!/usr/bin/env python3
"""
Tests de la lógica de decisión de PipelineDescargador.ejecutar() en modo
incremental (lote 5): sin_novedades, expediente_cambio (delta negativo o
sin ancla), y que una descarga completa (modo_actualizacion=None) no se ve
afectada. No usa Selenium: se parchean _autenticar, BuscadorExpedientes y
DescargadorArchivos con dobles de prueba. `python test_pipeline_incremental.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import modulos.pipeline as pipeline_mod
from modulos.pipeline import PipelineDescargador
from modulos.descarga import huella_fila

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _fila(descripcion, n_botones=1):
    return {"fecha": "01/01/2024", "tipo": "Providencia", "fojas": "1", "descripcion": descripcion, "n_botones": n_botones}


class FakeDriver:
    def __init__(self, filas):
        self._filas = filas
        self.current_url = "https://mesavirtual.jusentrerios.gov.ar/expedientes/1"

    def execute_script(self, script):
        return self._filas


class FakeCliente:
    def __init__(self, driver):
        self.driver = driver

    def cerrar(self):
        pass


class FakeBuscador:
    def __init__(self, cliente):
        pass

    def buscar(self, numero, indice_expediente=None):
        return {'numero': numero, 'caratula': 'Test c/ Test', 'tribunal': 'Juzgado X', 'url': ''}


def _fake_descargador_factory(rango):
    """Fábrica de una clase DescargadorArchivos falsa que sólo implementa
    leer_total_filas_confiable (lo único que hace falta antes de que las
    ramas sin_novedades/expediente_cambio retornen)."""
    class FakeDescargador:
        def __init__(self, cliente, carpeta_temp, fn_reconectar=None):
            self.cliente = cliente

        def leer_total_filas_confiable(self, driver):
            return rango

    return FakeDescargador


def _ejecutar_incremental(rango, filas_pagina1, modo_actualizacion):
    p = PipelineDescargador()
    driver = FakeDriver(filas_pagina1)
    p._autenticar = lambda cookies_mv: FakeCliente(driver)

    original_buscador = pipeline_mod.BuscadorExpedientes
    original_descargador = pipeline_mod.DescargadorArchivos
    pipeline_mod.BuscadorExpedientes = FakeBuscador
    pipeline_mod.DescargadorArchivos = _fake_descargador_factory(rango)
    try:
        return p.ejecutar('1/24', cookies_mv=[{'name': 'x', 'value': 'y'}], modo_actualizacion=modo_actualizacion)
    finally:
        pipeline_mod.BuscadorExpedientes = original_buscador
        pipeline_mod.DescargadorArchivos = original_descargador


def test_sin_novedades_no_cobra():
    print("\n[1] delta=0 y huellas iguales -> sin_novedades")

    huellas_previas = [huella_fila(_fila(f"mov {i}")) for i in range(5)]
    filas_pagina1 = [_fila(f"mov {i}") for i in range(5)]

    resultado = _ejecutar_incremental(
        rango=(1, 5, 10),
        filas_pagina1=filas_pagina1,
        modo_actualizacion={'total_filas_previo': 10, 'huellas_previas': huellas_previas, 'pdf_previo_local': '/no/existe.pdf'},
    )

    check("no es éxito", resultado.exito is False)
    check("tipo_error es sin_novedades", resultado.tipo_error == 'sin_novedades', resultado.tipo_error)


def test_delta_negativo_es_expediente_cambio():
    print("\n[2] delta<0 (el expediente tiene MENOS filas que antes) -> expediente_cambio")

    resultado = _ejecutar_incremental(
        rango=(1, 5, 5),  # ahora tiene 5 filas
        filas_pagina1=[_fila(f"mov {i}") for i in range(5)],
        modo_actualizacion={'total_filas_previo': 10, 'huellas_previas': [], 'pdf_previo_local': '/no/existe.pdf'},  # antes tenía 10
    )

    check("no es éxito", resultado.exito is False)
    check("tipo_error es expediente_cambio", resultado.tipo_error == 'expediente_cambio', resultado.tipo_error)


def test_delta_cero_pero_huellas_distintas_es_expediente_cambio():
    print("\n[3] delta=0 pero las huellas de página 1 cambiaron -> expediente_cambio")

    huellas_previas = [huella_fila(_fila(f"mov-viejo {i}")) for i in range(5)]
    filas_pagina1_distintas = [_fila(f"mov-totalmente-otro {i}") for i in range(5)]

    resultado = _ejecutar_incremental(
        rango=(1, 5, 10),
        filas_pagina1=filas_pagina1_distintas,
        modo_actualizacion={'total_filas_previo': 10, 'huellas_previas': huellas_previas, 'pdf_previo_local': '/no/existe.pdf'},
    )

    check("no es éxito", resultado.exito is False)
    check("tipo_error es expediente_cambio", resultado.tipo_error == 'expediente_cambio', resultado.tipo_error)


def test_no_se_pudo_leer_total_de_forma_confiable():
    print("\n[4] leer_total_filas_confiable devuelve None -> expediente_cambio (no explota)")

    resultado = _ejecutar_incremental(
        rango=None,
        filas_pagina1=[],
        modo_actualizacion={'total_filas_previo': 10, 'huellas_previas': [], 'pdf_previo_local': '/no/existe.pdf'},
    )

    check("no es éxito", resultado.exito is False)
    check("tipo_error es expediente_cambio", resultado.tipo_error == 'expediente_cambio', resultado.tipo_error)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE PipelineDescargador.ejecutar() EN MODO INCREMENTAL")
    print("=" * 70)

    test_sin_novedades_no_cobra()
    test_delta_negativo_es_expediente_cambio()
    test_delta_cero_pero_huellas_distintas_es_expediente_cambio()
    test_no_se_pudo_leer_total_de_forma_confiable()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
