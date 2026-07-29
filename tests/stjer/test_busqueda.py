"""Tests del buscador local. Corren SIN RED."""

import pytest

from modulos.jurisprudencia.stjer import busqueda as B
from modulos.jurisprudencia.stjer import corpus as C
from modulos.jurisprudencia.stjer import cosecha as H
from tests.stjer.test_cosecha import ClienteFalso


@pytest.fixture
def poblado(tmp_path):
    """Corpus chico pero real: cosechado con el cliente falso."""
    con = C.abrir(tmp_path / "corpus.sqlite")
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    cos.planificar_detalles()
    cos.ejecutar(H.TIPO_DETALLE)
    C.reconstruir_documentos(con)
    yield con
    con.close()


# ─── construccion del MATCH ────────────────────────────────────────────────

def test_arma_el_match_con_prefijos():
    assert B.construir_match("prescripcion liberatoria") == '"prescripcion"* AND "liberatoria"*'


def test_los_terminos_cortos_no_llevan_prefijo():
    # 'robo' tiene 4 letras: no se le agrega '*'. 'hurto' tiene 5: si.
    assert B.construir_match("robo y hurto") == '"robo" AND "hurto"*'
    assert '"y"' not in B.construir_match("robo y hurto"), "la stop-word se descarta"


def test_respeta_las_frases_entre_comillas():
    assert B.construir_match('"daño moral"') == '"dano moral"'


def test_la_puntuacion_no_rompe_la_sintaxis_de_fts():
    # Sin entrecomillar, un '*' o un '-' sueltos hacen fallar la consulta.
    for consulta in ["art. 1113, inc. 2)", "daño* -moral", 'NOT AND OR', "c/ s/"]:
        try:
            m = B.construir_match(consulta)
        except B.ErrorBusqueda:
            continue  # quedarse sin terminos utiles es una salida valida
        assert m.count('"') % 2 == 0, f"comillas desbalanceadas en {m!r}"


def test_una_consulta_solo_de_stop_words_avisa():
    with pytest.raises(B.ErrorBusqueda, match="palabras vacias"):
        B.construir_match("de la y el")


def test_consulta_vacia_avisa():
    with pytest.raises(B.ErrorBusqueda):
        B.construir_match("   ")


# ─── busqueda ──────────────────────────────────────────────────────────────

def test_encuentra_por_texto_del_sumario(poblado):
    r = B.BuscadorCorpus(poblado).buscar("responsabilidad objetiva")
    assert r, "tendria que encontrar el sumario"
    assert "PEREZ" in r[0]["caratula"]
    assert "«" in r[0]["fragmento"], "el fragmento debe venir resaltado"


def test_encuentra_sin_tildes(poblado):
    # Nadie escribe las tildes al buscar apurado.
    con_tilde = B.BuscadorCorpus(poblado).buscar('"daño moral"')
    sin_tilde = B.BuscadorCorpus(poblado).buscar('"dano moral"')
    assert con_tilde and sin_tilde
    assert con_tilde[0]["clave"] == sin_tilde[0]["clave"]


def test_el_prefijo_encuentra_variantes(poblado):
    # 'responsabilidad' esta en el texto; 'responsabilidades' no.
    assert B.BuscadorCorpus(poblado).buscar("responsabilidad")


def test_filtra_por_fuero_de_forma_laxa(poblado):
    b = B.BuscadorCorpus(poblado)
    assert b.buscar("responsabilidad", fuero="civil"), "'civil' debe matchear 'Civil y Comercial'"
    assert not b.buscar("responsabilidad", fuero="Laboral")


def test_filtra_por_rango_de_fechas(poblado):
    b = B.BuscadorCorpus(poblado)
    assert b.buscar("responsabilidad", desde="2019", hasta="2019")
    assert not b.buscar("responsabilidad", desde="2022")


def test_acepta_fechas_parciales(poblado):
    b = B.BuscadorCorpus(poblado)
    assert b.buscar("responsabilidad", desde="2019-03", hasta="2019-03")
    assert not b.buscar("responsabilidad", desde="2019-04")


def test_filtra_por_juez(poblado):
    b = B.BuscadorCorpus(poblado)
    assert b.buscar("responsabilidad", juez="González")
    assert not b.buscar("responsabilidad", juez="Inexistente")


def test_filtra_por_voz(poblado):
    b = B.BuscadorCorpus(poblado)
    assert b.buscar("responsabilidad", voces=["RESPONSABILIDAD OBJETIVA"])
    assert not b.buscar("responsabilidad", voces=["TENTATIVA"])


def test_relaja_a_or_cuando_el_and_no_trae_nada(poblado):
    b = B.BuscadorCorpus(poblado)
    # 'zzz' no existe: con AND estricto no habria nada.
    assert b.buscar("responsabilidad zzzinexistente", relajar=True)
    assert not b.buscar("responsabilidad zzzinexistente", relajar=False)


def test_respeta_el_limite(poblado):
    b = B.BuscadorCorpus(poblado)
    assert len(b.buscar("responsabilidad daño", limite=1, relajar=True)) == 1
    assert len(b.buscar("responsabilidad daño", limite=10, relajar=True)) > 1


def test_el_resultado_trae_lo_necesario_para_citar(poblado):
    r = B.BuscadorCorpus(poblado).buscar("responsabilidad")[0]
    for campo in ("caratula", "expediente", "fecha", "organismo", "voces", "clave"):
        assert campo in r, f"falta {campo}"
    assert r["url_pdf"].startswith("https://jur.jusentrerios.gov.ar/jur/dossier/")
    # bm25 de SQLite es <= 0 (mas bajo = mejor); se invierte para que en la
    # salida mas alto sea mejor. Puede dar 0 exacto cuando el termino esta en
    # la mitad del corpus y el IDF se anula: con 4 documentos pasa siempre.
    assert r["puntaje"] >= 0


# ─── voces ─────────────────────────────────────────────────────────────────

def test_sugiere_voces_por_cooocurrencia(poblado):
    b = B.BuscadorCorpus(poblado)
    sugerencias = b.sugerir_voces("caída de un árbol responsabilidad del estado")
    assert sugerencias
    voces = [s["voz"] for s in sugerencias]
    assert "RESPONSABILIDAD OBJETIVA" in voces
    assert any(s["origen"].startswith("corpus") for s in sugerencias)


def test_las_sugerencias_traen_la_ruta_completa(poblado):
    from modulos.jurisprudencia.stjer.tesauro_stjer import Tesauro
    from modulos.jurisprudencia.stjer.parser import NodoVoz

    t = Tesauro.desde_nodos([
        NodoVoz(materia="DERECHO CIVIL", voz_principal="RESPONSABILIDAD CIVIL",
                voz="RESPONSABILIDAD OBJETIVA", nivel=2),
    ])
    s = B.BuscadorCorpus(poblado, tesauro=t).sugerir_voces("responsabilidad objetiva")
    assert any("DERECHO CIVIL >" in x["ruta"] for x in s)


def test_sugerir_voces_no_explota_con_corpus_vacio(tmp_path):
    con = C.abrir(tmp_path / "vacio.sqlite")
    assert B.BuscadorCorpus(con).sugerir_voces("cualquier cosa") == []
    con.close()


# ─── obtener_fallo ─────────────────────────────────────────────────────────

def test_obtener_fallo_por_clave(poblado):
    b = B.BuscadorCorpus(poblado)
    clave = b.buscar("responsabilidad")[0]["clave"]
    f = b.obtener_fallo(clave)
    assert f["caratula"] and f["sumarios"] and f["votos"]
    assert f["votos"][0]["tipo_voto"] == "primer voto"


def test_obtener_fallo_inexistente_devuelve_vacio(poblado):
    assert B.BuscadorCorpus(poblado).obtener_fallo("sha1:noexiste") == {}


# ─── salida ────────────────────────────────────────────────────────────────

def test_markdown_es_compacto(poblado):
    r = B.BuscadorCorpus(poblado).buscar("responsabilidad")
    md = B.a_markdown(r)
    assert "###" in md and "clave:" in md
    # ~4 caracteres por token: 10 resultados tienen que entrar comodos.
    assert len(md) / max(len(r), 1) < 1200, "cada resultado deberia ser chico"


def test_markdown_sin_resultados():
    assert "Sin resultados" in B.a_markdown([])


def test_json_compacto_omite_el_sumario_entero(poblado):
    import json

    r = B.BuscadorCorpus(poblado).buscar("responsabilidad")
    datos = json.loads(B.a_json(r, compacto=True))
    assert "sumario" not in datos[0] and "fragmento" in datos[0]
    assert "sumario" in json.loads(B.a_json(r, compacto=False))[0]


def test_avisa_cuando_el_sumario_es_solo_un_extracto(tmp_path):
    from datetime import date

    con = C.abrir(tmp_path / "solo_listas.sqlite")
    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)  # sin pasada de detalles
    C.reconstruir_documentos(con)

    r = B.BuscadorCorpus(con).buscar("responsabilidad")
    assert r[0]["sumario_truncado"] is True
    assert "extracto del listado" in B.a_markdown(r)
    con.close()
