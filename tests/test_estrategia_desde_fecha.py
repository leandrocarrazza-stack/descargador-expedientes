#!/usr/bin/env python3
"""
Tests de EstrategiaDesdeFecha (descarga parcial "desde una fecha").

Sin Selenium: usa un driver falso, igual que tests/test_actualizacion_incremental.py.
`python tests/test_estrategia_desde_fecha.py` o vía pytest.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.descarga import EstrategiaDesdeFecha
from modulos.excepciones import ErrorDescarga

DESDE = date(2024, 3, 1)


def _fila(fecha, n_botones=1):
    return {"fecha": fecha, "tipo": "Providencia", "fojas": "1", "descripcion": "x", "n_botones": n_botones}


class DriverFalso:
    def __init__(self, filas):
        self._filas = filas

    def execute_script(self, script):
        return self._filas


def _correr(filas, desde=DESDE):
    est = EstrategiaDesdeFecha(desde)
    return est, est.botones_para_pagina(None, DriverFalso(filas), 1, sum(f["n_botones"] for f in filas))


def test_pagina_toda_nueva_sigue_paginando():
    _, (indices, seguir) = _correr([_fila("10/03/2024"), _fila("05/03/2024", 2)])
    assert indices == [0, 1, 2]
    assert seguir is True


def test_pagina_que_cruza_la_fecha_corta():
    # La fila vieja (28/02) y todo lo que sigue no se baja; se frena la paginación.
    _, (indices, seguir) = _correr([_fila("10/03/2024"), _fila("28/02/2024", 2), _fila("20/02/2024")])
    assert indices == [0]
    assert seguir is False


def test_varias_filas_en_la_fecha_exacta_se_incluyen_todas():
    _, (indices, seguir) = _correr([_fila("01/03/2024"), _fila("01/03/2024", 2), _fila("29/02/2024")])
    assert indices == [0, 1, 2]
    assert seguir is False


def test_los_indices_de_boton_respetan_n_botones_de_filas_excluidas():
    # La fila excluida del medio ocupa 2 botones: el índice de la siguiente salta.
    _, (indices, _) = _correr([_fila("10/03/2024"), _fila("01/01/2024", 2), _fila("05/03/2024")])
    assert indices == [0, 3]


def test_fecha_ilegible_se_incluye():
    _, (indices, seguir) = _correr([_fila("sin fecha"), _fila("")])
    assert indices == [0, 1]
    assert seguir is True


def test_no_deja_datos_para_actualizacion_incremental():
    est, _ = _correr([_fila("10/03/2024")])
    assert est.huellas_iniciales == []
    assert est.total_filas_final is None
    assert est.total_esperado is None


def test_sin_filas_levanta_error():
    try:
        _correr([])
    except ErrorDescarga:
        return
    raise AssertionError("debía levantar ErrorDescarga")


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_"):
            try:
                fn()
                print(f"  OK  {nombre}")
            except Exception as e:
                fallos += 1
                print(f" FALLA {nombre} -> {e!r}")
    sys.exit(1 if fallos else 0)
