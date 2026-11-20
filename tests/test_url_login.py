"""Tests de modulos.url_login.url_es_login (falso positivo de 'sesión vencida')."""

from modulos.url_login import url_es_login

EXPEDIENTE = "https://mesavirtual.jusentrerios.gov.ar/expedientes/689c67090c92450008e113e2"


def test_pantalla_de_login_de_keycloak_es_login():
    assert url_es_login("https://ol-sso.jusentrerios.gov.ar/realms/mesavirtual/protocol/openid-connect/auth?client_id=x")


def test_ruta_login_es_login():
    assert url_es_login("https://mesavirtual.jusentrerios.gov.ar/login")


def test_expediente_normal_no_es_login():
    assert not url_es_login(EXPEDIENTE)


def test_regreso_del_sso_con_iss_en_el_fragmento_no_es_login():
    # Caso real de producción: Mesa Virtual tras volver del SSO.
    url = (EXPEDIENTE + "#state=abc&session_state=def"
           "&iss=https%3A%2F%2Fol-sso.jusentrerios.gov.ar%2Frealms%2Fmesavirtual&code=xyz")
    assert not url_es_login(url)


def test_ol_sso_en_la_query_no_es_login():
    assert not url_es_login(EXPEDIENTE + "?iss=https://ol-sso.jusentrerios.gov.ar/realms/mesavirtual")


def test_vacio_o_none_no_es_login():
    assert not url_es_login("")
    assert not url_es_login(None)
