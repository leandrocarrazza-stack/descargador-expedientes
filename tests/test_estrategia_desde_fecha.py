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


def test_con_fecha_exacta_lo_anterior_se_excluye_y_corta():
    _, (indices, seguir) = _correr([_fila("10/03/2024"), _fila("01/03/2024"), _fila("28/02/2024", 2), _fila("20/02/2024")])
    assert indices == [0, 1]
    assert seguir is False


def test_varias_filas_en_la_fecha_exacta_se_incluyen_todas():
    _, (indices, seguir) = _correr([_fila("01/03/2024"), _fila("01/03/2024", 2), _fila("29/02/2024")])
    assert indices == [0, 1, 2]
    assert seguir is False


def test_sin_fecha_exacta_empieza_desde_la_fecha_anterior_inmediata():
    # No hay filas del 01/03: se incluye también la fecha anterior más cercana (28/02, todas sus filas).
    _, (indices, seguir) = _correr(
        [_fila("10/03/2024"), _fila("28/02/2024"), _fila("28/02/2024", 2), _fila("20/02/2024")]
    )
    assert indices == [0, 1, 2, 3]
    assert seguir is False


def test_fecha_anterior_partida_entre_dos_paginas():
    est = EstrategiaDesdeFecha(DESDE)
    pag1 = [_fila("10/03/2024"), _fila("28/02/2024")]
    pag2 = [_fila("28/02/2024", 2), _fila("20/02/2024")]
    indices1, seguir1 = est.botones_para_pagina(None, DriverFalso(pag1), 1, 2)
    indices2, seguir2 = est.botones_para_pagina(None, DriverFalso(pag2), 2, 3)
    assert indices1 == [0, 1] and seguir1 is True
    assert indices2 == [0, 1] and seguir2 is False


def test_fecha_posterior_a_todo_baja_solo_la_ultima_fecha():
    _, (indices, seguir) = _correr([_fila("10/02/2024"), _fila("10/02/2024"), _fila("05/02/2024")])
    assert indices == [0, 1]
    assert seguir is False


def test_fecha_anterior_a_todo_baja_todo_y_sigue():
    _, (indices, seguir) = _correr([_fila("10/03/2024"), _fila("05/03/2024")], desde=date(2020, 1, 1))
    assert indices == [0, 1]
    assert seguir is True


def test_fecha_con_hora_se_interpreta_por_dia():
    # Mesa Virtual muestra "dd/mm/aaaa hh:mm": 01/03 a la mañana cuenta como fecha exacta.
    _, (indices, seguir) = _correr([_fila("10/03/2024 09:30"), _fila("01/03/2024 08:00"), _fila("28/02/2024 17:45")])
    assert indices == [0, 1]
    assert seguir is False


def test_fecha_ilegible_se_incluye():
    _, (indices, seguir) = _correr([_fila("sin fecha"), _fila("")])
    assert indices == [0, 1]
    assert seguir is True


def test_total_para_el_progreso_es_el_de_la_seleccion():
    est = EstrategiaDesdeFecha(DESDE)
    pag1 = [_fila("10/03/2024", 2), _fila("05/03/2024")]
    pag2 = [_fila("01/03/2024"), _fila("20/02/2024")]
    est.botones_para_pagina(None, DriverFalso(pag1), 1, 3)
    # Página 1 sin corte: total provisorio = lo seleccionado hasta ahora.
    assert est.total_estimado == 3 and est.total_esperado is None
    est.botones_para_pagina(None, DriverFalso(pag2), 2, 2)
    # Página 2 llega al corte: total exacto = 4 (el del expediente entero no se informa).
    assert est.total_esperado == 4


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
