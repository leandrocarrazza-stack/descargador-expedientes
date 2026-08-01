"""Tests del chat STJER (interpretacion de consultas + escritos). Sin red."""

import json
from types import SimpleNamespace

import pytest

from modulos.jurisprudencia.stjer import chat as CH
from modulos.jurisprudencia.stjer import tesauro_stjer as T
from modulos.jurisprudencia.stjer.parser import NodoVoz
from tests.stjer.test_busqueda import poblado  # noqa: F401 (fixture reusada)


class _MessagesFalso:
    def __init__(self, texto_respuesta, llamadas, excepcion=None):
        self._texto = texto_respuesta
        self._llamadas = llamadas
        self._excepcion = excepcion

    def create(self, **kwargs):
        self._llamadas.append(kwargs)
        if self._excepcion:
            raise self._excepcion
        return SimpleNamespace(content=[
            SimpleNamespace(type="thinking"),  # sin `.text`, como con thinking extendido
            SimpleNamespace(type="text", text=self._texto),
        ])


class ClienteClaudeFalso:
    """Cliente en memoria que sirve una respuesta fija para messages.create()."""

    def __init__(self, texto_respuesta="", excepcion=None):
        self.llamadas = []
        self.messages = _MessagesFalso(texto_respuesta, self.llamadas, excepcion)


def _tesauro_de(con) -> T.Tesauro:
    """Arma el tesauro desde las voces del corpus de prueba, sin tocar disco."""
    nodos = [
        NodoVoz(
            materia=f["materia"] or "", voz_principal=f["voz_principal"] or "",
            voz=f["voz"], nivel=2,
        )
        for f in con.execute("SELECT materia, voz_principal, voz FROM voces")
    ]
    return T.Tesauro.desde_nodos(nodos)


def _json_claude(terminos, voces, respuesta="Buscando jurisprudencia relacionada."):
    return json.dumps({
        "terminos_busqueda": terminos,
        "voces_juridicas": voces,
        "respuesta_usuario": respuesta,
    })


# ─── consulta corta, con Claude ─────────────────────────────────────────────

def test_interpreta_con_claude_y_busca(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(_json_claude(
        ["responsabilidad objetiva"], ["RESPONSABILIDAD OBJETIVA"]
    ))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_mensaje("necesito saber que dice la juris sobre esto")

    assert cliente.llamadas, "tiene que haber llamado a Claude"
    assert r["resultados"], "tiene que encontrar el fallo de PEREZ"
    assert "PEREZ" in r["resultados"][0]["caratula"]
    assert r["voces_usadas"] == ["RESPONSABILIDAD OBJETIVA"]


def test_descarta_voces_que_claude_inventa(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(_json_claude(
        ["responsabilidad objetiva"],
        ["VOZ QUE NO EXISTE EN EL TESAURO", "RESPONSABILIDAD OBJETIVA"],
    ))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_mensaje("responsabilidad objetiva")

    assert r["voces_usadas"] == ["RESPONSABILIDAD OBJETIVA"], (
        "la voz inventada no debe pasar al resultado"
    )


def test_json_invalido_cae_a_interpretacion_local(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso("esto no es JSON, es un error de Claude")
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_mensaje("responsabilidad objetiva")

    assert cliente.llamadas, "igual se intento llamar a Claude"
    assert r["resultados"], "el fallback local tiene que seguir encontrando el fallo"


def test_respuesta_sin_bloque_de_texto_cae_a_interpretacion_local(poblado):
    # Caso real: con thinking extendido, a veces Claude no llega a emitir
    # ningun bloque de texto (se corta en el thinking, p.ej. por max_tokens).
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso()
    cliente.messages.create = lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="thinking")]
    )
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_mensaje("responsabilidad objetiva")

    assert r["resultados"]


def test_error_de_red_con_claude_cae_a_interpretacion_local(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(excepcion=ConnectionError("sin red"))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_mensaje("responsabilidad objetiva")

    assert r["resultados"]


# ─── consulta corta, sin Claude ─────────────────────────────────────────────

def test_sin_cliente_usa_interpretacion_local(poblado):
    tesauro = _tesauro_de(poblado)
    chat = CH.ChatSTJER(poblado, cliente_anthropic=None, tesauro=tesauro)

    r = chat.procesar_mensaje("responsabilidad objetiva")

    assert r["resultados"]
    assert "sin IA" in r["respuesta"]


def test_mensaje_vacio_no_llama_a_nadie(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(_json_claude([], []))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_mensaje("   ")

    assert not cliente.llamadas
    assert r["resultados"] == []


# ─── escrito completo ────────────────────────────────────────────────────────

def test_procesar_documento_usa_claude_y_busca(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(_json_claude(
        ["responsabilidad objetiva"], ["RESPONSABILIDAD OBJETIVA"]
    ))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    texto = "Vengo a demandar por la caida de un arbol de la via publica..."
    r = chat.procesar_documento(texto, nombre_archivo="demanda.docx")

    assert cliente.llamadas
    assert r["resultados"]
    # El texto del escrito viaja en el ultimo mensaje mandado a Claude.
    ultimo_mensaje = cliente.llamadas[0]["messages"][-1]["content"]
    assert "caida de un arbol" in ultimo_mensaje


def test_procesar_documento_muy_largo_se_trunca_y_avisa(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(_json_claude(
        ["responsabilidad objetiva"], ["RESPONSABILIDAD OBJETIVA"]
    ))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    texto = "responsabilidad objetiva. " * (CH.MAX_CARACTERES_DOCUMENTO // 20)
    assert len(texto) > CH.MAX_CARACTERES_DOCUMENTO

    r = chat.procesar_documento(texto, nombre_archivo="demanda.docx")

    ultimo_mensaje = cliente.llamadas[0]["messages"][-1]["content"]
    assert len(ultimo_mensaje) < len(texto) + 200
    assert "muy largo" in r["respuesta"]


def test_procesar_documento_vacio_no_llama_a_nadie(poblado):
    tesauro = _tesauro_de(poblado)
    cliente = ClienteClaudeFalso(_json_claude([], []))
    chat = CH.ChatSTJER(poblado, cliente_anthropic=cliente, tesauro=tesauro)

    r = chat.procesar_documento("   ")

    assert not cliente.llamadas
    assert r["resultados"] == []
