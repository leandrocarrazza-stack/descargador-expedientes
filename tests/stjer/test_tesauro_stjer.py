"""
Tests de la cosecha del tesauro. Corren SIN RED.

Incluye el caso real reportado en produccion: el pedido de "arbol_tesauro"
no llego al panel real y el sitio devolvio la pagina de busqueda comun.
"""

from modulos.jurisprudencia.stjer import tesauro_stjer as T
from tests.stjer import fixtures_sinteticas as F


class ClienteFalsoTesauro:
    """Cliente en memoria que sirve una respuesta fija para arbol_tesauro()."""

    def __init__(self, html):
        self.html_fijo = html
        from modulos.jurisprudencia.stjer.cliente import RespuestaCruda

        self._RespuestaCruda = RespuestaCruda

    def arbol_tesauro(self, ref=None):
        return self._RespuestaCruda(
            estado=200, html=self.html_fijo, crudo=self.html_fijo
        )


def test_cosechar_arbol_con_tesauro_real_funciona():
    cliente = ClienteFalsoTesauro(F.TESAURO_UL_HTML)
    t = T.cosechar_arbol(cliente)
    assert t and len(t) > 0
    assert not t.version.startswith("INVALIDO")


def test_cosechar_arbol_rechaza_el_menu_de_filtros_equivocado():
    # Este es el caso real: el sitio no entendio el pedido y devolvio la
    # pagina de busqueda comun, con los <option> de Fuero/Agregar
    # Filtro/operadores sueltos, sin ningun contenedor de tesauro.
    cliente = ClienteFalsoTesauro(F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML)
    t = T.cosechar_arbol(cliente)
    assert not t, "no tiene que guardar nada como si fuera un tesauro valido"
    assert t.version.startswith("INVALIDO")


def test_cosechar_arbol_no_pisa_un_tesauro_bueno_con_uno_invalido(tmp_path):
    destino = tmp_path / "tesauro_stjer.json"

    bueno = T.cosechar_arbol(ClienteFalsoTesauro(F.TESAURO_UL_HTML), destino=destino)
    assert bueno and destino.exists()
    contenido_antes = destino.read_text(encoding="utf-8")

    malo = T.cosechar_arbol(
        ClienteFalsoTesauro(F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML), destino=destino
    )
    assert not malo
    assert destino.read_text(encoding="utf-8") == contenido_antes, (
        "un resultado invalido no debe sobreescribir el tesauro bueno que ya "
        "estaba guardado"
    )
