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


def test_cosechar_arbol_con_tesauro_real_funciona(tmp_path):
    cliente = ClienteFalsoTesauro(F.TESAURO_UL_HTML)
    t = T.cosechar_arbol(cliente, destino=tmp_path / "tesauro_stjer.json")
    assert t and len(t) > 0
    assert not t.version.startswith("INVALIDO")


def test_cosechar_arbol_rechaza_el_menu_de_filtros_equivocado(tmp_path):
    # Este es el caso real: el sitio no entendio el pedido y devolvio la
    # pagina de busqueda comun, con los <option> de Fuero/Agregar
    # Filtro/operadores sueltos, sin ningun contenedor de tesauro.
    cliente = ClienteFalsoTesauro(F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML)
    t = T.cosechar_arbol(cliente, destino=tmp_path / "tesauro_stjer.json")
    assert not t, "no tiene que guardar nada como si fuera un tesauro valido"
    assert t.version.startswith("INVALIDO")


class ClienteFalsoTesauroEco:
    """
    Simula el bug real reportado en produccion: "expandir" CUALQUIER nodo
    devuelve exactamente la misma pagina de siempre, porque el 'ref' que se
    usa para pedirlo no hace nada de verdad (era el value de un <option>, no
    una llamada JS real).
    """

    def __init__(self, html):
        self.html_fijo = html
        self.llamadas = 0
        from modulos.jurisprudencia.stjer.cliente import RespuestaCruda

        self._RespuestaCruda = RespuestaCruda

    def arbol_tesauro(self, ref=None):
        self.llamadas += 1
        return self._RespuestaCruda(
            estado=200, html=self.html_fijo, crudo=self.html_fijo
        )


def test_cosechar_arbol_detecta_el_eco_y_no_explota(tmp_path):
    # Caso real: 161 categorias iniciales, cada intento de "abrir" una
    # devolvia la misma lista de 161 de siempre. Sin la deteccion de eco,
    # esto tardaba mas de dos horas y generaba una cola de cientos de miles.
    cliente = ClienteFalsoTesauroEco(F.TESAURO_SELECT_HTML)
    t = T.cosechar_arbol(
        cliente, destino=tmp_path / "tesauro_stjer.json", max_nodos=500
    )

    # Se intenta expandir cada uno de los 3 nodos originales UNA vez, se
    # detecta el eco, y se corta — no una explosion combinatoria.
    assert cliente.llamadas <= 4, f"se hicieron {cliente.llamadas} pedidos, algo no corto a tiempo"


def test_una_lista_plana_sin_sub_niveles_queda_aprovechada(tmp_path):
    # Si el tesauro real no tiene mas profundidad (es una lista plana de
    # terminos, no un arbol de 3 niveles), los terminos igual tienen que
    # quedar buscables — no descartados como si "no hubiera voces".
    cliente = ClienteFalsoTesauroEco(F.TESAURO_SELECT_HTML)
    t = T.cosechar_arbol(
        cliente, destino=tmp_path / "tesauro_stjer.json", max_nodos=500
    )

    assert len(t) == 3, "las 3 categorias planas deberian quedar como voces buscables"
    assert set(t.voces()) == {"DERECHO CIVIL", "DERECHO PENAL", "DERECHOS HUMANOS"}
    assert t.ruta_de("DERECHO CIVIL") == "DERECHO CIVIL"


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
