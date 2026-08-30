"""
Tests del servidor MCP de jurisprudencia. Sin red, sin subproceso: las
funciones que decora @mcp.tool siguen siendo corrutinas normales, asi que
se llaman directo (via asyncio.run, no hay plugin pytest-asyncio instalado)
con las mismas fixtures que usa el resto de tests/stjer/.

La conexion real por stdio (lo que usaria Claude Desktop) se probo a mano
con un cliente MCP real contra el corpus de produccion; esto cubre la
logica de cada herramienta en CI.
"""

import asyncio
import json
from datetime import date

import pytest

import mcp_server_jurisprudencia as S
from modulos.jurisprudencia.stjer import ajustes
from modulos.jurisprudencia.stjer import corpus as C
from modulos.jurisprudencia.stjer import cosecha as H
from modulos.jurisprudencia.stjer.parser import NodoVoz
from modulos.jurisprudencia.stjer.tesauro_stjer import Tesauro
from tests.stjer.test_cosecha import ClienteFalso


def _llamar(coro):
    return asyncio.run(coro)


@pytest.fixture
def corpus_de_prueba(tmp_path, monkeypatch):
    """Corpus y tesauro chicos pero reales, aislados del archivo de produccion."""
    ruta_corpus = tmp_path / "corpus.sqlite"
    con = C.abrir(ruta_corpus)
    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    cos.planificar_detalles()
    cos.ejecutar(H.TIPO_DETALLE)
    C.reconstruir_documentos(con)

    nodos = [
        NodoVoz(
            materia=f["materia"] or "", voz_principal=f["voz_principal"] or "",
            voz=f["voz"], nivel=2,
        )
        for f in con.execute("SELECT materia, voz_principal, voz FROM voces")
    ]
    con.close()

    ruta_tesauro = tmp_path / "tesauro_stjer.json"
    Tesauro.desde_nodos(nodos).guardar(ruta_tesauro)

    monkeypatch.setattr(ajustes, "CORPUS_PATH", ruta_corpus)
    monkeypatch.setattr(ajustes, "TESAURO_STJER_PATH", ruta_tesauro)
    yield ruta_corpus


# ─── buscar ──────────────────────────────────────────────────────────────

def test_buscar_encuentra_por_texto(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_buscar(S.BuscarInput(consulta="responsabilidad objetiva")))
    datos = json.loads(r)
    assert datos, "tendria que encontrar el sumario de PEREZ"
    assert "PEREZ" in datos[0]["caratula"]
    assert "«" in datos[0]["fragmento"]


def test_buscar_con_voz_inexistente_no_encuentra_nada(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_buscar(
        S.BuscarInput(consulta="responsabilidad", voces=["VOZ QUE NO EXISTE"])
    ))
    assert json.loads(r) == []


def test_buscar_consulta_solo_de_stopwords_da_error_legible(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_buscar(S.BuscarInput(consulta="de la y el")))
    datos = json.loads(r)
    assert "error" in datos


def test_buscar_sin_corpus_avisa_en_vez_de_explotar(tmp_path, monkeypatch):
    monkeypatch.setattr(ajustes, "CORPUS_PATH", tmp_path / "no_existe.sqlite")
    r = _llamar(S.jurisprudencia_buscar(S.BuscarInput(consulta="responsabilidad")))
    datos = json.loads(r)
    assert "error" in datos


# ─── sugerir voces ───────────────────────────────────────────────────────

def test_sugerir_voces_devuelve_voces_reales(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_sugerir_voces(
        S.SugerirVocesInput(consulta="responsabilidad objetiva")
    ))
    datos = json.loads(r)
    assert datos
    assert any(d["voz"] == "RESPONSABILIDAD OBJETIVA" for d in datos)


# ─── listar voces ────────────────────────────────────────────────────────

def test_listar_voces_devuelve_el_tesauro(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_listar_voces(S.ListarVocesInput()))
    datos = json.loads(r)
    assert datos["materias"]
    nombres = {m["nombre"] for m in datos["materias"]}
    assert "DERECHO CIVIL" in nombres


def test_listar_voces_filtra_por_materia(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_listar_voces(S.ListarVocesInput(materia="civil")))
    datos = json.loads(r)
    assert all("civil" in m["nombre"].lower() for m in datos["materias"])


def test_listar_voces_sin_tesauro_avisa(tmp_path, monkeypatch, corpus_de_prueba):
    monkeypatch.setattr(ajustes, "TESAURO_STJER_PATH", tmp_path / "no_existe.json")
    r = _llamar(S.jurisprudencia_listar_voces(S.ListarVocesInput()))
    datos = json.loads(r)
    assert "error" in datos


# ─── obtener fallo ───────────────────────────────────────────────────────

def test_obtener_fallo_por_id(corpus_de_prueba):
    encontrados = json.loads(
        _llamar(S.jurisprudencia_buscar(S.BuscarInput(consulta="responsabilidad objetiva")))
    )
    fallo_id = encontrados[0]["fallo_id"]

    r = _llamar(S.jurisprudencia_obtener_fallo(S.ObtenerFalloInput(identificador=str(fallo_id))))
    datos = json.loads(r)
    assert "PEREZ" in datos["caratula"]
    assert datos["sumarios"]
    assert datos["voces"]


def test_obtener_fallo_inexistente_avisa(corpus_de_prueba):
    r = _llamar(S.jurisprudencia_obtener_fallo(S.ObtenerFalloInput(identificador="999999999")))
    datos = json.loads(r)
    assert "error" in datos
