"""
Tests del parser. Corren SIN RED.

Es a proposito la unica parte del sistema que se puede desarrollar y verificar
antes de tener acceso al sitio.
"""

import pytest

from modulos.jurisprudencia.stjer import parser as P
from tests.stjer import fixtures_sinteticas as F


# ─── desenvolver_toba ──────────────────────────────────────────────────────

def test_html_directo_pasa_sin_tocar():
    assert P.desenvolver_toba(F.LISTADO_HTML) == F.LISTADO_HTML


def test_extrae_html_de_un_literal_javascript():
    html = P.desenvolver_toba(F.LISTADO_JS)
    assert "<table" in html
    assert "PEREZ c/ MUNICIPALIDAD" in html
    # El escapeo de JS quedo deshecho
    assert "\\'" not in html
    assert "\\n" not in html


def test_extrae_html_de_json():
    payload = '{"ok":true,"celdas":{"ei_1":"<table><tr><td>hola</td></tr></table>"}}'
    assert "<table>" in P.desenvolver_toba(payload)


def test_cuerpo_irreconocible_se_devuelve_entero():
    # Perder la respuesta seria peor que fallar mas arriba con algo en la mano.
    assert P.desenvolver_toba("ruido sin html") == "ruido sin html"


def test_cuerpo_vacio():
    assert P.desenvolver_toba("") == ""
    assert P.desenvolver_toba(None) == ""


# ─── token ah y captcha ────────────────────────────────────────────────────

def test_extrae_token_ah():
    cuerpo = "<a href='aplicacion.php?ah=st672374e1c84563.72627224&ai=jur||11000005'>x</a>"
    assert P.extraer_token_ah(cuerpo) == "st672374e1c84563.72627224"


def test_sin_token_ah_devuelve_none():
    assert P.extraer_token_ah("<html>nada</html>") is None


def test_detecta_la_pared_de_captcha():
    assert P.hay_captcha(F.CAPTCHA_HTML) is True


def test_una_pagina_de_resultados_no_es_captcha():
    assert P.hay_captcha(F.LISTADO_HTML) is False


def test_la_palabra_verificacion_suelta_no_es_captcha():
    # Caso real observado: despues de resolver el captcha correctamente, la
    # pagina de busqueda seguia teniendo la palabra "Verificación" como
    # titulo de seccion, sin imagen ni campo de captcha. Antes del fix esto
    # disparaba un falso positivo que rompia el reintento.
    html = """
    <html><body>
      <h3>Verificación</h3>
      <form>
        <select name="fuero"><option>Civil y Comercial</option></select>
        <button>Buscar</button>
      </form>
    </body></html>
    """
    assert P.hay_captcha(html) is False


def test_captcha_real_con_imagen_si_se_detecta():
    html = """
    <html><body>
      <label>Verificación (*)</label>
      <img src="captcha.php?id=1" alt="captcha">
      <input name="codigo_captcha" type="text">
    </body></html>
    """
    assert P.hay_captcha(html) is True


def test_captcha_real_con_solo_el_campo_tambien_se_detecta():
    # Por si la imagen es un data: URI sin la palabra 'captcha' en el src.
    html = """
    <html><body>
      <label>Verificación (*)</label>
      <img src="data:image/png;base64,aGVsbG8=" alt="codigo de seguridad">
      <input name="txt_captcha" type="text">
    </body></html>
    """
    assert P.hay_captcha(html) is True


# ─── listado ───────────────────────────────────────────────────────────────

def test_parsea_las_filas_del_listado():
    r = P.parsear_listado(F.LISTADO_HTML)
    assert len(r) == 2, "la fila separadora no debe contarse"

    a = r.filas[0]
    assert a["caratula"] == "PEREZ c/ MUNICIPALIDAD s/ DAÑOS Y PERJUICIOS"
    assert a["organismo"] == "Cámara de Apelaciones Sala I"
    assert a["jurisdiccion"] == "Gualeguaychú"
    assert a["nro_expediente"] == "Nro. 12.345/2019"
    assert a["fuero"] == "Civil y Comercial"
    assert a["sumario_extracto"].startswith("La responsabilidad del Estado")


def test_normaliza_la_fecha_a_iso():
    r = P.parsear_listado(F.LISTADO_HTML)
    assert r.filas[0]["fecha_fallo"] == "2019-03-14"
    assert r.filas[1]["fecha_fallo"] == "2021-07-02"


def test_saca_el_id_del_sitio_del_onclick():
    # Si el sitio expone un id propio, es mejor clave natural que un sha1.
    r = P.parsear_listado(F.LISTADO_HTML)
    assert r.filas[0]["id_sitio"] == "88123"
    assert r.filas[1]["id_sitio"] == "88124"


def test_lee_total_y_paginacion():
    r = P.parsear_listado(F.LISTADO_HTML)
    assert r.total_registros == 47
    assert (r.pagina, r.total_paginas) == (1, 2)
    assert r.hay_siguiente is True


def test_total_con_separador_de_miles():
    html = F.LISTADO_HTML.replace("Se encontraron 47", "Se encontraron 14.822")
    assert P.parsear_listado(html).total_registros == 14822


def test_listado_envuelto_en_javascript_da_lo_mismo():
    directo = P.parsear_listado(F.LISTADO_HTML)
    envuelto = P.parsear_listado(F.LISTADO_JS)
    assert len(envuelto) == len(directo)
    assert envuelto.filas[0]["caratula"] == directo.filas[0]["caratula"]
    assert envuelto.total_registros == 47


def test_html_sin_tabla_de_resultados_no_explota():
    r = P.parsear_listado("<html><body><p>Sin resultados</p></body></html>")
    assert len(r) == 0


# ─── detalle ───────────────────────────────────────────────────────────────

def test_parsea_los_campos_del_detalle():
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert d.caratula == "PEREZ c/ MUNICIPALIDAD s/ DAÑOS Y PERJUICIOS"
    assert d.nro_expediente == "Nro. 12.345/2019"
    assert d.fecha_fallo == "2019-03-14"
    assert d.organismo == "Cámara de Apelaciones Sala I"
    assert d.fuero == "Civil y Comercial"


def test_extrae_el_enlace_al_pdf():
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert d.enlace_pdf == "dossier/bCARATULA_b__PEREZ__.PDF"


def test_separa_los_sumarios_en_bloques():
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert len(d.sumarios) == 2
    assert "objetiva" in d.sumarios[0]["texto"]
    assert "in re ipsa" in d.sumarios[1]["texto"]


def test_parsea_las_voces_con_su_jerarquia():
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert len(d.voces) == 2
    assert d.voces[0] == {
        "materia": "DERECHO CIVIL",
        "voz_principal": "RESPONSABILIDAD CIVIL",
        "voz": "RESPONSABILIDAD OBJETIVA",
    }


def test_las_voces_se_cuelgan_del_primer_sumario():
    # Colgarlas de todos inflaria el indice con repetidos.
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert len(d.sumarios[0]["voces"]) == 2
    assert d.sumarios[1]["voces"] == []


def test_parsea_los_votos():
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert {"juez": "Dra. González", "tipo_voto": "primer voto"} in d.votos
    assert {"juez": "Dr. Martínez", "tipo_voto": "adhesión"} in d.votos


def test_la_tabla_de_votos_no_se_confunde_con_la_de_voces():
    d = P.parsear_detalle(F.DETALLE_HTML)
    assert all(v["voz"] for v in d.voces)
    assert len(d.voces) == 2


def test_como_fallo_da_un_dict_para_upsert():
    d = P.parsear_detalle(F.DETALLE_HTML)
    datos = d.como_fallo()
    assert datos["caratula"] and datos["fecha_fallo"] == "2019-03-14"


# ─── tesauro ───────────────────────────────────────────────────────────────

def test_parsea_el_arbol_anidado_del_tesauro():
    nodos = P.parsear_arbol_tesauro(F.TESAURO_UL_HTML)
    rutas = {n.ruta for n in nodos}
    assert "DERECHO CIVIL" in rutas
    assert "DERECHO CIVIL > RESPONSABILIDAD CIVIL" in rutas
    assert "DERECHO CIVIL > RESPONSABILIDAD CIVIL > RESPONSABILIDAD OBJETIVA" in rutas
    assert "DERECHO PENAL > TENTATIVA" in rutas


def test_parsea_el_tesauro_como_desplegable():
    nodos = P.parsear_arbol_tesauro(F.TESAURO_SELECT_HTML)
    etiquetas = {n.materia for n in nodos}
    assert etiquetas == {"DERECHO CIVIL", "DERECHO PENAL", "DERECHOS HUMANOS"}
    # La opcion vacia "-- Seleccione --" se descarta
    assert all(n.materia for n in nodos)


def test_el_contexto_ubica_los_hijos_en_su_rama():
    # Caso "cada nivel se pide por AJAX": hay que decirle de quien cuelgan.
    nodos = P.parsear_arbol_tesauro(
        F.TESAURO_SELECT_HTML, materia="DERECHO CIVIL"
    )
    assert all(n.materia == "DERECHO CIVIL" for n in nodos)
    assert {n.voz_principal for n in nodos} == {
        "DERECHO CIVIL", "DERECHO PENAL", "DERECHOS HUMANOS"
    }


def test_no_confunde_los_dropdowns_de_filtros_con_el_tesauro():
    # Caso real observado en produccion: el pedido de "arbol_tesauro" no
    # llego al panel real y el sitio devolvio la pagina de busqueda comun.
    # Antes del fix, esto barria TODOS los <option> de la pagina (Fuero,
    # Agregar Filtro, operadores) como si fueran materias juridicas.
    nodos = P.parsear_arbol_tesauro(F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML)
    assert nodos == [], (
        "sin un contenedor de tesauro identificable, no hay que inventar "
        "nodos barriendo dropdowns ajenos"
    )


def test_parece_tesauro_valido_detecta_el_menu_equivocado():
    nodos = P.parsear_arbol_tesauro(
        F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML.replace(
            '<div id="panel_tesauro">', '<div id="panel_tesauro"><select name="x">'
        ).replace("</body>", "</select></body>")
    )
    # Se fuerza un caso con contenido mezclado para probar el umbral, aun si
    # el escaneo por contenedor ya evita la mayoria de estos casos en la
    # practica (ver test de arriba).
    es_valido, motivo = P.parece_tesauro_valido(
        [P.NodoVoz(materia=e, nivel=0) for e in
         ["contiene", "es igual a", "comienza con", "Sumario", "DERECHO CIVIL"]]
    )
    assert es_valido is False
    assert "vocabulario de filtros" in motivo


def test_parece_tesauro_valido_acepta_un_tesauro_real():
    nodos = P.parsear_arbol_tesauro(F.TESAURO_UL_HTML)
    es_valido, motivo = P.parece_tesauro_valido(nodos)
    assert es_valido is True and motivo == ""


def test_parece_tesauro_valido_con_lista_vacia():
    es_valido, motivo = P.parece_tesauro_valido([])
    assert es_valido is False and "ningun nodo" in motivo


# ─── perfil ajustable ──────────────────────────────────────────────────────

def test_el_perfil_se_puede_ajustar_sin_tocar_codigo(tmp_path):
    perfil_json = tmp_path / "perfil.json"
    perfil_json.write_text(
        '{"encabezados": {"Caratula del expediente": "caratula"}}',
        encoding="utf-8",
    )
    perfil = P.PerfilSitio.cargar(perfil_json)
    assert perfil.encabezados["caratula del expediente"] == "caratula"
    # Los que ya venian siguen estando
    assert perfil.encabezados["organismo"] == "organismo"


def test_perfil_inexistente_devuelve_los_defaults(tmp_path):
    perfil = P.PerfilSitio.cargar(tmp_path / "no_existe.json")
    assert perfil.encabezados["caratula"] == "caratula"


def test_perfil_corrupto_no_rompe(tmp_path):
    malo = tmp_path / "perfil.json"
    malo.write_text("{ esto no es json", encoding="utf-8")
    assert P.PerfilSitio.cargar(malo).encabezados["fuero"] == "fuero"
