"""
Tests contra las fixtures REALES capturadas en la Fase 0.

Se saltean solos mientras no existan los archivos, asi que la suite pasa desde
hoy. En cuanto guardes los .html de la Fase 0 en
`data/jurisprudencia/descubrimiento/`, estos tests se activan y pasan a ser
los que mandan: los sinteticos prueban la logica, estos prueban que el parser
entiende el sitio de verdad.
"""

import pytest

from modulos.jurisprudencia.stjer import ajustes
from modulos.jurisprudencia.stjer import parser as P


def _fixture(nombre: str) -> str:
    ruta = ajustes.DESCUBRIMIENTO_DIR / nombre
    if not ruta.exists() or ruta.stat().st_size == 0:
        pytest.skip(
            f"Falta la fixture real {nombre}. Ver docs/STJER_FASE0.md"
        )
    return ruta.read_text(encoding="utf-8", errors="replace")


# ─── listado ───────────────────────────────────────────────────────────────

def test_reconoce_la_tabla_de_resultados_real():
    listado = P.parsear_listado(_fixture("01_listado.html"))
    assert len(listado) > 0, (
        "No se reconocio ninguna fila. Ajusta 'encabezados' en "
        "data/jurisprudencia/descubrimiento/perfil.json"
    )


def test_las_filas_reales_traen_los_campos_para_citar():
    filas = P.parsear_listado(_fixture("01_listado.html")).filas
    fila = filas[0]
    assert fila.get("caratula"), "sin caratula"
    assert fila.get("fecha_fallo"), "la fecha no se pudo normalizar a ISO"
    assert len(fila["fecha_fallo"]) == 10 and fila["fecha_fallo"][4] == "-"


def test_lee_el_total_declarado_por_el_sitio():
    # Sin este numero no hay reconciliacion posible, y la reconciliacion es
    # la unica defensa contra perder filas en silencio.
    listado = P.parsear_listado(_fixture("01_listado.html"))
    assert listado.total_registros is not None, (
        "No se encontro 'Se encontraron N registros'. Ajusta 'patron_total'."
    )


def test_se_puede_volver_a_abrir_cada_fila():
    filas = P.parsear_listado(_fixture("01_listado.html")).filas
    assert any(f.get("ref_detalle") for f in filas), (
        "Ninguna fila expone como reabrirla: sin esto la pasada de detalles "
        "no puede funcionar"
    )


def test_la_segunda_pagina_se_parsea_igual():
    p1 = P.parsear_listado(_fixture("01_listado.html"))
    p2 = P.parsear_listado(_fixture("02_listado_p2.html"))
    assert len(p2) > 0
    claves1 = {f.get("caratula") for f in p1.filas}
    claves2 = {f.get("caratula") for f in p2.filas}
    assert claves1 != claves2, "la pagina 2 trajo lo mismo que la 1"


# ─── detalle ───────────────────────────────────────────────────────────────

def test_el_detalle_real_trae_caratula_y_sumario():
    d = P.parsear_detalle(_fixture("03_detalle.html"))
    assert d.caratula, "sin caratula: ajusta los pares etiqueta/valor"
    assert d.sumarios, "sin sumarios: es lo que se cita, no puede faltar"


def test_el_detalle_real_trae_las_voces():
    d = P.parsear_detalle(_fixture("03_detalle.html"))
    assert d.voces, "sin voces: revisa la tabla Materia/Voz Principal/Voz"
    assert all(v.get("voz") for v in d.voces)


def test_el_detalle_real_trae_el_enlace_al_pdf():
    d = P.parsear_detalle(_fixture("03_detalle.html"))
    assert d.enlace_pdf, "sin enlace al PDF: ajusta 'patron_pdf'"
    assert ".pdf" in d.enlace_pdf.lower()


def test_los_votos_no_se_confunden_con_las_voces():
    d = P.parsear_detalle(_fixture("03_detalle.html"))
    if not d.votos:
        pytest.skip("este fallo no publica votos")
    nombres = {v["juez"] for v in d.votos}
    tipos = {(v["tipo_voto"] or "").lower() for v in d.votos}
    assert not (nombres & tipos), "un tipo de voto se guardo como nombre de juez"


# ─── tesauro ───────────────────────────────────────────────────────────────

def test_pagina_busqueda_no_expone_tesauro():
    # La fixture 04_tesauro.html es la pagina de busqueda del STJER, que NO
    # contiene el arbol del tesauro (solo selectores Toba de filtros). Con los
    # fixes de parser el resultado correcto es 0 nodos, no garbage.
    nodos = P.parsear_arbol_tesauro(_fixture("04_tesauro.html"))
    assert len(nodos) == 0, (
        f"Se esperaban 0 nodos (la pagina de busqueda no es el arbol del tesauro) "
        f"pero se obtuvieron {len(nodos)}. Si el sitio cambio y ahora SI expone "
        f"el tesauro en esa URL, actualizar este test."
    )


# ─── captcha ───────────────────────────────────────────────────────────────

def test_detecta_el_captcha_en_la_pagina_inicial_real():
    html = _fixture("00_inicial.html")
    if not P.hay_captcha(html):
        pytest.skip(
            "La captura inicial no tiene captcha (se guardo despues de "
            "resolverlo, o el sitio ya no lo pide)"
        )
    assert P.hay_captcha(html)


def test_una_pagina_de_resultados_real_no_se_confunde_con_captcha():
    # Un falso positivo aca haria que la cosecha aborte en cada request.
    assert P.hay_captcha(_fixture("01_listado.html")) is False
