#!/usr/bin/env python3
"""
Tests de la actualización incremental (lote 5): lectura de filas/huellas,
EstrategiaCompleta y EstrategiaIncremental, y el fix del bug preexistente
(una página sin botones ya no corta toda la descarga).

Sin Selenium: usa un driver falso, igual que tests/test_progreso.py.
`python test_actualizacion_incremental.py` o vía pytest.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.descarga import (
    DescargadorArchivos, _leer_filas_pagina, huella_fila,
    EstrategiaCompleta, EstrategiaIncremental,
)
from modulos.excepciones import ErrorDescarga

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _fila(fecha="01/01/2024", tipo="Providencia", fojas="1", descripcion="texto", n_botones=1):
    return {"fecha": fecha, "tipo": tipo, "fojas": fojas, "descripcion": descripcion, "n_botones": n_botones}


def _descargador():
    return DescargadorArchivos(cliente_selenium=None, carpeta_temp=tempfile.mkdtemp())


class DriverScriptFalso:
    """Driver mínimo: execute_script devuelve una lista de filas fija."""
    def __init__(self, filas):
        self._filas = filas

    def execute_script(self, script):
        return self._filas


class DriverScriptRoto:
    """Simula que execute_script falla (ej. contexto JS perdido)."""
    def execute_script(self, script):
        raise RuntimeError("execution context destroyed")


# ═══════════════════════════════════════════════════════════════════════════
#  1. _leer_filas_pagina y huella_fila
# ═══════════════════════════════════════════════════════════════════════════

def test_leer_filas_pagina():
    print("\n[1] _leer_filas_pagina")

    filas = [_fila(n_botones=0), _fila(n_botones=1), _fila(n_botones=2)]
    driver = DriverScriptFalso(filas)
    resultado = _leer_filas_pagina(driver)
    check("devuelve las filas tal cual las dio execute_script", resultado == filas)

    check("execute_script roto -> [] (no lanza)", _leer_filas_pagina(DriverScriptRoto()) == [])


def test_huella_fila():
    print("\n[2] huella_fila")

    a = _fila(fecha="01/01/2024", tipo="Providencia", fojas="3", descripcion="  Se   provee  ")
    b = _fila(fecha="01/01/2024", tipo="Providencia", fojas="3", descripcion="Se provee")
    check("espacios colapsados: dos filas 'iguales' dan la misma huella",
          huella_fila(a) == huella_fila(b))

    c = _fila(descripcion="otra cosa")
    check("contenido distinto -> huella distinta", huella_fila(a) != huella_fila(c))

    larga = _fila(descripcion="x" * 500)
    check("se trunca a 200 caracteres", len(huella_fila(larga)) <= 200)


# ═══════════════════════════════════════════════════════════════════════════
#  2. EstrategiaCompleta
# ═══════════════════════════════════════════════════════════════════════════

class _DescargadorFalsoParaRango:
    """Sólo implementa _leer_rango_filas, para probar la estrategia aislada."""
    def __init__(self, rango=None):
        self._rango = rango

    def _leer_rango_filas(self, driver, estricto=False):
        return self._rango


def test_estrategia_completa_captura_huellas_y_total_en_pagina_1():
    print("\n[3] EstrategiaCompleta")

    filas_pagina1 = [_fila(descripcion=f"mov {i}", n_botones=1) for i in range(6)]
    driver = DriverScriptFalso(filas_pagina1)
    descargador_falso = _DescargadorFalsoParaRango(rango=(1, 10, 47))

    estrategia = EstrategiaCompleta()
    indices, seguir = estrategia.botones_para_pagina(descargador_falso, driver, 1, 6)

    check("devuelve todos los índices de la página", indices == list(range(6)))
    check("siempre indica seguir", seguir is True)
    check("captura hasta 5 huellas (no más)", len(estrategia.huellas_iniciales) == 5)
    check("las huellas coinciden con las primeras filas",
          estrategia.huellas_iniciales == [huella_fila(f) for f in filas_pagina1[:5]])
    check("captura el total de filas del rango de MUI", estrategia.total_filas_final == 47)
    check("total_esperado se mantiene None (sigue estimando como siempre)",
          estrategia.total_esperado is None)

    # En página 2 no debe volver a tocar huellas/total (ya se fijaron en pág 1)
    estrategia.botones_para_pagina(descargador_falso, DriverScriptFalso([_fila()]), 2, 1)
    check("página 2 no pisa las huellas ya capturadas",
          estrategia.huellas_iniciales == [huella_fila(f) for f in filas_pagina1[:5]])


# ═══════════════════════════════════════════════════════════════════════════
#  3. EstrategiaIncremental
# ═══════════════════════════════════════════════════════════════════════════

def test_incremental_corta_en_delta_una_sola_pagina():
    print("\n[4] EstrategiaIncremental: corta en delta dentro de una página")

    # 3 filas nuevas (delta=3), tam_pagina=10: todas entran en la página 1.
    filas = [_fila(descripcion=f"nuevo {i}", n_botones=1) for i in range(3)] + \
            [_fila(descripcion=f"viejo {i}", n_botones=1) for i in range(7)]
    driver = DriverScriptFalso(filas)

    estrategia = EstrategiaIncremental(delta=3, huellas_previas=[], tam_pagina=10)
    indices, seguir = estrategia.botones_para_pagina(None, driver, 1, 10)

    check("sólo baja los botones de las primeras 3 filas", indices == [0, 1, 2])
    check("sin huellas previas, sigue paginando (no hay ancla que buscar)", seguir is True)


def test_incremental_ancla_exacta_en_delta():
    print("\n[5] EstrategiaIncremental: ancla justo en delta (caso limpio)")

    huellas_previas = [huella_fila(_fila(descripcion=f"viejo {i}")) for i in range(5)]
    nuevas = [_fila(descripcion=f"nuevo {i}", n_botones=1) for i in range(3)]
    viejas = [_fila(descripcion=f"viejo {i}", n_botones=1) for i in range(5)]
    filas = nuevas + viejas

    driver = DriverScriptFalso(filas)
    estrategia = EstrategiaIncremental(delta=3, huellas_previas=huellas_previas, tam_pagina=10)
    indices, seguir = estrategia.botones_para_pagina(None, driver, 1, 8)

    check("baja sólo los 3 nuevos", indices == [0, 1, 2])
    check("encuentra el ancla exactamente en offset 3 (== delta)", estrategia.posicion_ancla == 3)
    check("no sigue paginando: ya encontró el ancla", seguir is False)
    check("total_esperado queda fijado en 3", estrategia.total_esperado == 3)


def test_incremental_ancla_antes_de_delta_es_parcial():
    print("\n[6] EstrategiaIncremental: ancla ANTES de delta (movimientos intercalados)")

    # delta=3 pero el ancla aparece en offset 2: hubo un movimiento intercalado
    # (ej. una fecha de firma anterior a la de carga) que no estaba antes.
    huellas_previas = [huella_fila(_fila(descripcion=f"viejo {i}")) for i in range(5)]
    filas = [
        _fila(descripcion="nuevo 0", n_botones=1),
        _fila(descripcion="nuevo 1", n_botones=1),
    ] + [_fila(descripcion=f"viejo {i}", n_botones=1) for i in range(5)]

    driver = DriverScriptFalso(filas)
    estrategia = EstrategiaIncremental(delta=3, huellas_previas=huellas_previas, tam_pagina=10)
    indices, seguir = estrategia.botones_para_pagina(None, driver, 1, 7)

    check("sólo baja los 2 movimientos antes del ancla (no llega a los 3 del delta)",
          indices == [0, 1])
    check("posicion_ancla (2) queda por debajo de delta (3) -> caller debe marcar parcial",
          estrategia.posicion_ancla == 2 and estrategia.posicion_ancla < estrategia.delta)
    check("no sigue paginando", seguir is False)


def test_incremental_sin_ancla_dentro_del_margen_aborta():
    print("\n[7] EstrategiaIncremental: no encuentra el ancla -> ErrorDescarga")

    huellas_previas = ["algo-que-nunca-aparece"]
    # 4 páginas de 5 filas nuevas cada una (ninguna coincide con huellas_previas):
    # con tam_pagina=5 y delta=3, el límite es 3 + 5*2 = 13 filas.
    paginas = [[_fila(descripcion=f"pag{p}-{i}", n_botones=1) for i in range(5)] for p in range(4)]

    estrategia = EstrategiaIncremental(delta=3, huellas_previas=huellas_previas, tam_pagina=5)

    lanzo = False
    try:
        for pagina_actual, filas in enumerate(paginas, start=1):
            driver = DriverScriptFalso(filas)
            indices, seguir = estrategia.botones_para_pagina(None, driver, pagina_actual, 5)
            if not seguir:
                break
    except ErrorDescarga as e:
        lanzo = True
        check("el mensaje identifica la causa", "NO_SE_PUDO_ALINEAR" in str(e))
    check("levanta ErrorDescarga tras agotar delta + margen sin encontrar el ancla", lanzo)


def test_incremental_pagina_sin_botones_no_corta_el_recorrido():
    print("\n[8] EstrategiaIncremental: una página sin botones no debe terminar el recorrido")

    # delta=4: página 1 tiene 2 filas nuevas SIN botón (0 adjuntos, algo legítimo:
    # ej. una providencia sin archivo adjunto) y la página 2 tiene 2 más con botón.
    huellas_previas = []
    pagina1 = [_fila(descripcion="sin adjunto 1", n_botones=0), _fila(descripcion="sin adjunto 2", n_botones=0)]
    pagina2 = [_fila(descripcion="con adjunto 1", n_botones=1), _fila(descripcion="con adjunto 2", n_botones=1)]

    estrategia = EstrategiaIncremental(delta=4, huellas_previas=huellas_previas, tam_pagina=2)

    indices1, seguir1 = estrategia.botones_para_pagina(None, DriverScriptFalso(pagina1), 1, 0)
    check("página sin botones: indices vacíos", indices1 == [])
    check("...pero seguir=True (no corta el recorrido, ver bug preexistente)", seguir1 is True)

    indices2, seguir2 = estrategia.botones_para_pagina(None, DriverScriptFalso(pagina2), 2, 2)
    check("página 2 sí aporta sus 2 botones", indices2 == [0, 1])


def test_leer_filas_rotas_aborta_incremental():
    print("\n[9] EstrategiaIncremental: no se pudo leer la tabla -> ErrorDescarga")

    estrategia = EstrategiaIncremental(delta=3, huellas_previas=[], tam_pagina=10)
    lanzo = False
    try:
        estrategia.botones_para_pagina(None, DriverScriptRoto(), 1, 0)
    except ErrorDescarga as e:
        lanzo = True
        check("el mensaje identifica la causa", "NO_SE_PUDO_LEER_FILAS" in str(e))
    check("levanta ErrorDescarga", lanzo)


# ═══════════════════════════════════════════════════════════════════════════
#  4. _leer_rango_filas(estricto=True) no cae al fallback de page_source
# ═══════════════════════════════════════════════════════════════════════════

def test_leer_rango_filas_estricto_no_usa_fallback():
    print("\n[10] _leer_rango_filas(estricto=True) no usa el fallback de page_source")
    d = _descargador()

    class DriverSinSelectorConPageSource:
        page_source = "1–10 de 213"

        def find_elements(self, by, selector):
            return []

    driver = DriverSinSelectorConPageSource()
    check("sin estricto, encuentra el total en page_source",
          d._leer_rango_filas(driver) == (1, 10, 213))
    check("con estricto=True, no cae al page_source y devuelve None",
          d._leer_rango_filas(driver, estricto=True) is None)


# ═══════════════════════════════════════════════════════════════════════════
#  5. Regresión de punta a punta: página sin botones no corta TODA la descarga
#     (bug preexistente que este lote corrige, con la estrategia por defecto)
# ═══════════════════════════════════════════════════════════════════════════

class DriverPaginas:
    """
    Simula varias páginas de la tabla de movimientos para correr
    descargar_todo_por_paginas() de punta a punta sin Selenium.
    """
    def __init__(self, filas_por_pagina):
        self.filas_por_pagina = filas_por_pagina
        self.indice_pagina = 0
        self.current_url = "https://mesavirtual.jusentrerios.gov.ar/expedientes/1"
        self.page_source = ""

    def execute_script(self, script):
        return self.filas_por_pagina[self.indice_pagina]

    def find_elements(self, by, selector):
        if 'GetAppIcon' in str(selector):
            total = sum(f.get('n_botones', 0) for f in self.filas_por_pagina[self.indice_pagina])
            return ['boton'] * total
        return []


def test_pagina_sin_botones_no_corta_toda_la_descarga():
    print("\n[11] Regresión: una página sin botones no termina TODA la descarga (EstrategiaCompleta)")

    # Página 1: 2 archivos. Página 2: SIN archivos (0 botones). Página 3: 2 archivos.
    # Antes del fix, `if cantidad_botones == 0: break` cortaba acá para siempre,
    # perdiendo la página 3 completa.
    filas_por_pagina = [
        [_fila(descripcion="p1-a", n_botones=1), _fila(descripcion="p1-b", n_botones=1)],
        [_fila(descripcion="p2-sin-adjunto", n_botones=0)],
        [_fila(descripcion="p3-a", n_botones=1), _fila(descripcion="p3-b", n_botones=1)],
    ]
    driver = DriverPaginas(filas_por_pagina)

    d = _descargador()
    d.cliente = type('C', (), {'driver': driver})()
    d._esperar_tabla_cargada = lambda driver, timeout=15: True
    d._detectar_paginacion = lambda driver: None

    def _fake_navegar(driver, paginacion_cacheada=None):
        if driver.indice_pagina + 1 < len(driver.filas_por_pagina):
            driver.indice_pagina += 1
            return True
        return False
    d._navegar_siguiente_pagina = _fake_navegar

    def _fake_descargar(indice_boton, ruta_destino):
        ruta_destino.write_bytes(b"%PDF-1.4 fake")
        return True
    d._descargar_archivo_selenium = _fake_descargar

    import modulos.descarga as _descarga_mod
    sleep_original = _descarga_mod.time.sleep
    _descarga_mod.time.sleep = lambda *a, **k: None
    try:
        resultado = d.descargar_todo_por_paginas("1/24")
    finally:
        _descarga_mod.time.sleep = sleep_original

    check("recorre las 3 páginas pese a que la 2 no tenía adjuntos",
          driver.indice_pagina == 2, driver.indice_pagina)
    check("descarga los 4 archivos (2 + 0 + 2), no se corta en la página 2",
          len(resultado) == 4, len(resultado))


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE ACTUALIZACIÓN INCREMENTAL (LOTE 5)")
    print("=" * 70)

    test_leer_filas_pagina()
    test_huella_fila()
    test_estrategia_completa_captura_huellas_y_total_en_pagina_1()
    test_incremental_corta_en_delta_una_sola_pagina()
    test_incremental_ancla_exacta_en_delta()
    test_incremental_ancla_antes_de_delta_es_parcial()
    test_incremental_sin_ancla_dentro_del_margen_aborta()
    test_incremental_pagina_sin_botones_no_corta_el_recorrido()
    test_leer_filas_rotas_aborta_incremental()
    test_leer_rango_filas_estricto_no_usa_fallback()
    test_pagina_sin_botones_no_corta_toda_la_descarga()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
