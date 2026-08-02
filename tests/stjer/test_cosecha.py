"""
Tests de la cosecha, con un cliente falso. Corren SIN RED.

Verifican lo que de verdad importa del diseño: que se pueda cortar y retomar,
que no duplique, que el disyuntor corte, y que el muro de captcha no consuma
intentos.
"""

import pytest

from modulos.jurisprudencia.stjer import corpus as C
from modulos.jurisprudencia.stjer import cosecha as H
from modulos.jurisprudencia.stjer import parser as P
from modulos.jurisprudencia.stjer.cliente import ErrorCaptcha, ErrorCliente, RespuestaCruda
from tests.stjer import fixtures_sinteticas as F


class ClienteFalso:
    """Cliente en memoria que sirve fixtures y puede fallar a pedido."""

    def __init__(self, listado=None, detalle=None, fallar_en=(), captcha_en=()):
        self.listado = listado if listado is not None else F.LISTADO_HTML
        self.detalle = detalle if detalle is not None else F.DETALLE_HTML
        self.fallar_en = set(fallar_en)      # meses que tiran ErrorCliente
        self.captcha_en = set(captcha_en)    # meses que tiran ErrorCaptcha
        self.llamadas = []

    def buscar_listado(self, desde, hasta, fuero=None, pagina=1):
        mes = f"{desde:%Y-%m}"
        self.llamadas.append(("listado", mes, pagina))
        if mes in self.captcha_en:
            raise ErrorCaptcha("verificacion requerida")
        if mes in self.fallar_en:
            raise ErrorCliente("HTTP 500")
        # La fixture dice "Página 1 de 2"; se devuelve vacio en la 2 para
        # cortar sin inventar una segunda pagina.
        if pagina > 1:
            return RespuestaCruda(estado=200, html="<html></html>", crudo="<html></html>")
        return RespuestaCruda(estado=200, html=self.listado, crudo=self.listado)

    def abrir_detalle(self, ref, mes=None, pagina=None):
        self.llamadas.append(("detalle", ref, 1))
        return RespuestaCruda(estado=200, html=self.detalle, crudo=self.detalle)

    def arbol_tesauro(self, ref=None):
        self.llamadas.append(("tesauro", ref, 1))
        return RespuestaCruda(
            estado=200, html=F.TESAURO_UL_HTML, crudo=F.TESAURO_UL_HTML
        )


@pytest.fixture
def con(tmp_path):
    c = C.abrir(tmp_path / "corpus.sqlite")
    yield c
    c.close()


# ─── helpers ───────────────────────────────────────────────────────────────

def test_meses_entre_es_inclusivo():
    from datetime import date

    assert H.meses_entre(date(2019, 11, 1), date(2020, 2, 28)) == [
        "2019-11", "2019-12", "2020-01", "2020-02"
    ]
    assert H.meses_entre(date(2019, 3, 1), date(2019, 3, 31)) == ["2019-03"]


def test_rango_del_mes_toma_el_ultimo_dia():
    from datetime import date

    assert H.rango_del_mes("2020-02") == (date(2020, 2, 1), date(2020, 2, 29))
    assert H.rango_del_mes("2019-02") == (date(2019, 2, 1), date(2019, 2, 28))


# ─── pasada A ──────────────────────────────────────────────────────────────

def test_planificar_encola_un_mes_por_tarea(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    assert cos.planificar_listados(date(2019, 1, 1), date(2019, 12, 31)) == 12
    # Re-planificar no duplica ni pisa el progreso
    assert cos.planificar_listados(date(2019, 1, 1), date(2019, 12, 31)) == 0


def test_cosecha_listados_guarda_los_fallos(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    r = cos.ejecutar(H.TIPO_LISTA)

    assert r.tareas_ok == 1 and r.tareas_error == 0
    assert r.fallos_nuevos == 2
    stats = C.estadisticas(con)
    assert stats["fallos"] == 2
    assert stats["sumarios"] == 2
    assert stats["sumarios_truncados"] == 2, "el extracto del listado va como truncado"


def test_recosechar_el_mismo_mes_no_duplica(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)

    con.execute("UPDATE cosecha_tareas SET estado='pendiente'")
    r2 = cos.ejecutar(H.TIPO_LISTA)

    assert r2.fallos_nuevos == 0
    assert r2.fallos_actualizados == 2
    assert C.estadisticas(con)["fallos"] == 2


def test_el_limite_permite_trocear_la_corrida(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 1, 1), date(2019, 12, 31))

    assert cos.ejecutar(H.TIPO_LISTA, limite=3).tareas_ok == 3
    pend = con.execute(
        "SELECT COUNT(*) FROM cosecha_tareas WHERE estado='pendiente'"
    ).fetchone()[0]
    assert pend == 9, "el resto tiene que quedar pendiente para la proxima"


def test_los_recientes_se_cosechan_primero(con):
    from datetime import date

    # Si se corta la corrida, lo que quedo cosechado tiene que ser lo que mas
    # se consulta.
    cliente = ClienteFalso()
    cos = H.Cosechadora(cliente, con)
    cos.planificar_listados(date(2018, 1, 1), date(2020, 12, 31))
    cos.ejecutar(H.TIPO_LISTA, limite=2, orden="reciente")

    meses = [m for (t, m, _p) in cliente.llamadas if t == "listado"]
    assert meses[0] == "2020-12"


# ─── pasada B ──────────────────────────────────────────────────────────────

def test_el_detalle_reemplaza_al_extracto_truncado(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    assert C.estadisticas(con)["sumarios_truncados"] == 2

    cos.planificar_detalles()
    r = cos.ejecutar(H.TIPO_DETALLE)

    assert r.tareas_ok == 2
    stats = C.estadisticas(con)
    assert stats["fallos_con_detalle"] == 2
    assert stats["sumarios_truncados"] == 0, "el detalle debe pisar el extracto"
    assert stats["voces"] >= 2, "el detalle trae las voces reales"
    assert stats["votos"] == 4


def test_el_extracto_no_pisa_un_sumario_completo_ya_cosechado(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    cos.planificar_detalles()
    cos.ejecutar(H.TIPO_DETALLE)

    # Un re-listado posterior no puede degradar lo cosechado.
    con.execute("UPDATE cosecha_tareas SET estado='pendiente' WHERE tipo=?",
                (H.TIPO_LISTA,))
    cos.ejecutar(H.TIPO_LISTA)
    assert C.estadisticas(con)["sumarios_truncados"] == 0


def test_detalle_invalido_no_se_guarda_y_queda_en_error(con):
    from datetime import date

    # Caso real: abrir_detalle no re-navega a la fila exacta y el sitio
    # devuelve la pagina de busqueda comun en vez del detalle.
    cliente = ClienteFalso(detalle=F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML)
    cos = H.Cosechadora(cliente, con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    caratulas_antes = {
        r["caratula"] for r in con.execute("SELECT caratula FROM fallos")
    }

    cos.planificar_detalles()
    r = cos.ejecutar(H.TIPO_DETALLE)

    assert r.tareas_ok == 0, "ningun detalle invalido deberia contarse como ok"
    assert r.tareas_error == 2
    assert con.execute(
        "SELECT COUNT(*) FROM fallos WHERE detalle_ok=1"
    ).fetchone()[0] == 0
    # La caratula real del listado no se piso con basura del detalle invalido.
    caratulas_despues = {
        r["caratula"] for r in con.execute("SELECT caratula FROM fallos")
    }
    assert caratulas_despues == caratulas_antes
    assert not any(
        P.caratula_parece_invalida(c) for c in caratulas_despues
    )


# ─── resiliencia ───────────────────────────────────────────────────────────

def test_el_disyuntor_corta_tras_fallos_seguidos(con):
    from datetime import date

    # 12 meses que fallan todos: tiene que frenar en el quinto, no seguir
    # martillando al servidor.
    meses = [f"2019-{m:02d}" for m in range(1, 13)]
    cos = H.Cosechadora(
        ClienteFalso(fallar_en=meses), con, max_fallos_consecutivos=5
    )
    cos.planificar_listados(date(2019, 1, 1), date(2019, 12, 31))
    r = cos.ejecutar(H.TIPO_LISTA)

    assert r.abortada_por, "tendria que haber abortado"
    assert "fallos seguidos" in r.abortada_por
    assert r.tareas_error == 5


def test_el_captcha_devuelve_la_tarea_sin_gastar_intentos(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(captcha_en={"2019-03"}), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    r = cos.ejecutar(H.TIPO_LISTA)

    assert "verificacion" in r.abortada_por
    tarea = con.execute("SELECT * FROM cosecha_tareas").fetchone()
    assert tarea["estado"] == "pendiente", "tiene que volver a la cola"
    assert tarea["intentos"] == 0, "no fallo la tarea, se corto la sesion"


def test_un_fallo_aislado_no_traba_ni_aborta_la_corrida(con):
    from datetime import date

    # Un mes roto no debe reintentarse en el acto (martillaria al servidor y
    # dispararia el disyuntor por si solo): queda en 'error' y la corrida
    # sigue con los demas.
    cos = H.Cosechadora(ClienteFalso(fallar_en={"2019-02"}), con,
                        max_fallos_consecutivos=5)
    cos.planificar_listados(date(2019, 1, 1), date(2019, 3, 31))
    r = cos.ejecutar(H.TIPO_LISTA)

    assert not r.abortada_por
    assert r.tareas_ok == 2 and r.tareas_error == 1
    estado = con.execute(
        "SELECT estado, intentos FROM cosecha_tareas WHERE clave='2019-02'"
    ).fetchone()
    assert estado["estado"] == "error" and estado["intentos"] == 1


def test_reparar_reencola_los_errores(con):
    from datetime import date

    cliente = ClienteFalso(fallar_en={"2019-02"})
    cos = H.Cosechadora(cliente, con)
    cos.planificar_listados(date(2019, 1, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)

    assert cos.reparar()["tareas_reencoladas"] >= 1

    # Ya sin la falla, el mes se cosecha en la segunda vuelta.
    cliente.fallar_en.clear()
    r = cos.ejecutar(H.TIPO_LISTA)
    assert r.tareas_ok >= 1
    assert con.execute(
        "SELECT COUNT(*) FROM cosecha_tareas WHERE estado='error'"
    ).fetchone()[0] == 0


def test_reanudar_tras_una_corrida_muerta(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 1, 1), date(2019, 3, 31))

    # Simula el proceso muerto: una tarea quedo 'en_curso'
    t = C.tomar_tarea(con, H.TIPO_LISTA)
    con.execute(
        "UPDATE cosecha_tareas SET tomada_en='2020-01-01T00:00:00+00:00' WHERE id=?",
        (t["id"],),
    )
    r = cos.ejecutar(H.TIPO_LISTA)
    assert r.tareas_ok == 3, "tiene que recuperar la huerfana y terminar las 3"


# ─── reconciliacion y re-parseo ────────────────────────────────────────────

def test_la_reconciliacion_detecta_que_faltan_filas(con):
    from datetime import date

    # La fixture declara 47 registros pero solo trae 2 filas: es exactamente
    # el sintoma de una paginacion que perdio resultados.
    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)

    # La fixture declara 47 pero solo una de sus dos filas cae en 2019-03
    # (la otra es de 2021-07), asi que el mes queda descuadrado.
    dif = C.diferencias_reconciliacion(con)
    assert dif == [{"mes": "2019-03", "esperados": 47, "guardados": 1}]

    rep = cos.reparar()
    assert rep["meses_descuadrados"] == 1
    estado = con.execute(
        "SELECT estado FROM cosecha_tareas WHERE clave='2019-03'"
    ).fetchone()[0]
    assert estado == "pendiente", "el mes descuadrado tiene que re-encolarse"


def test_reparar_reencola_detalles_marcados_ok_con_datos_invalidos(con):
    from datetime import date

    # Simula el estado corrupto real: un fallo con detalle_ok=1 pero
    # caratula/organismo de encabezado de columna en vez de datos reales
    # (paso antes del chequeo de cordura, con corpus viejos).
    cos = H.Cosechadora(ClienteFalso(), con)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    cos.planificar_detalles()
    cos.ejecutar(H.TIPO_DETALLE)

    fila = con.execute("SELECT id, clave_natural FROM fallos LIMIT 1").fetchone()
    con.execute(
        "UPDATE fallos SET caratula='Sumario', detalle_ok=1 WHERE id=?",
        (fila["id"],),
    )
    con.commit()

    resultado = cos.reparar()

    assert resultado["detalles_corruptos_reencolados"] == 1
    estado = con.execute(
        "SELECT detalle_ok FROM fallos WHERE id=?", (fila["id"],)
    ).fetchone()["detalle_ok"]
    assert estado == 0
    tarea = con.execute(
        "SELECT estado FROM cosecha_tareas WHERE tipo=? AND clave=?",
        (H.TIPO_DETALLE, fila["clave_natural"]),
    ).fetchone()
    assert tarea["estado"] == "pendiente"


def test_reparsear_no_toca_la_red(con):
    from datetime import date

    cliente = ClienteFalso()
    cos = H.Cosechadora(cliente, con, guardar_crudo=True)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    cos.planificar_detalles()
    cos.ejecutar(H.TIPO_DETALLE)

    antes = len(cliente.llamadas)
    r = H.reparsear_crudos(con, H.TIPO_DETALLE)

    assert r["procesados"] == 2 and r["errores"] == 0
    assert len(cliente.llamadas) == antes, "re-parsear no debe pedir nada"


def test_reparsear_no_confirma_un_crudo_invalido(con):
    from datetime import date
    import gzip

    cos = H.Cosechadora(ClienteFalso(), con, guardar_crudo=True)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    cos.planificar_detalles()
    cos.ejecutar(H.TIPO_DETALLE)

    # El HTML archivado en si es la pagina de busqueda comun (asi paso en
    # produccion): reparsear no puede "arreglar" eso, solo no debe
    # confirmarlo como si fuera un detalle valido.
    con.execute(
        "UPDATE respuestas_crudas SET cuerpo_gz=? WHERE tipo=?",
        (
            gzip.compress(F.PAGINA_BUSQUEDA_SIN_TESAURO_HTML.encode("utf-8")),
            H.TIPO_DETALLE,
        ),
    )
    con.commit()

    r = H.reparsear_crudos(con, H.TIPO_DETALLE)

    assert r["errores"] == 2 and r["procesados"] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM fallos WHERE caratula='Sumario'"
    ).fetchone()[0] == 0, "reparsear no debe escribir la caratula invalida"


def test_sin_guardar_crudo_no_se_archiva(con):
    from datetime import date

    cos = H.Cosechadora(ClienteFalso(), con, guardar_crudo=False)
    cos.planificar_listados(date(2019, 3, 1), date(2019, 3, 31))
    cos.ejecutar(H.TIPO_LISTA)
    assert con.execute("SELECT COUNT(*) FROM respuestas_crudas").fetchone()[0] == 0
