#!/usr/bin/env python3
"""
Tests del reporte de progreso de descarga ("47 de 213 archivos").

No tocan la red ni Selenium: usan un driver falso y una app Flask mínima, así
que corren en cualquier lado con `python test_progreso.py`.

Cubren las tres cosas que pueden salir mal sin que nadie se entere:
  1. La estimación del total (lo único que puede quedar corrido).
  2. Que el progreso no pueda tumbar una descarga ya pagada.
  3. Que publicar progreso no rompa el long-poll de /estado.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modulos.descarga import DescargadorArchivos

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


# ═══════════════════════════════════════════════════════════════════════════
#  Dobles de prueba
# ═══════════════════════════════════════════════════════════════════════════

class _Elemento:
    def __init__(self, text):
        self.text = text


class DriverFalso:
    """Driver mínimo: sólo una etiqueta de paginación y un page_source."""

    def __init__(self, etiqueta=None, page_source=""):
        self._etiqueta = etiqueta
        self.page_source = page_source

    def find_elements(self, by, selector):
        return [_Elemento(self._etiqueta)] if self._etiqueta else []


def _descargador():
    """DescargadorArchivos sin navegador, sólo para probar los helpers."""
    import tempfile
    return DescargadorArchivos(cliente_selenium=None, carpeta_temp=tempfile.mkdtemp())


class DriverContador:
    """
    Driver que cuenta cada round-trip que le costaría a Chrome de verdad
    (find_elements por selector, lecturas de page_source), para poder probar
    que _leer_rango_filas corta apenas encuentra un match utilizable y que
    _detectar_total_movimientos/_navegar_siguiente_pagina no repiten la
    lectura de paginación dentro de la misma página.
    """

    def __init__(self, textos_por_selector=None, page_source=""):
        self._textos_por_selector = textos_por_selector or {}
        self._page_source_valor = page_source
        self.llamadas_find_elements = []
        self.lecturas_page_source = 0

    def find_elements(self, by, selector):
        self.llamadas_find_elements.append(selector)
        texto = self._textos_por_selector.get(selector)
        return [_Elemento(texto)] if texto else []

    @property
    def page_source(self):
        self.lecturas_page_source += 1
        return self._page_source_valor


# ═══════════════════════════════════════════════════════════════════════════
#  1. Lectura de la etiqueta de paginación de Material-UI
# ═══════════════════════════════════════════════════════════════════════════

def test_leer_rango_filas():
    print("\n[1] Lectura de la etiqueta de paginación (MUI)")
    d = _descargador()

    check("'1–10 de 213' (guión en dash)",
          d._leer_rango_filas(DriverFalso("1–10 de 213")) == (1, 10, 213))
    check("'1-10 de 213' (guión común)",
          d._leer_rango_filas(DriverFalso("1-10 de 213")) == (1, 10, 213))
    check("'11–20 de 213' (página 2)",
          d._leer_rango_filas(DriverFalso("11–20 de 213")) == (11, 20, 213))
    check("'1–10 of 213' (inglés)",
          d._leer_rango_filas(DriverFalso("1–10 of 213")) == (1, 10, 213))
    check("'1.001–1.010 de 2.130' (miles con punto)",
          d._leer_rango_filas(DriverFalso("1.001–1.010 de 2.130")) == (1001, 1010, 2130))

    # MUI con count=-1: es una cota inferior, no un total. No sirve de denominador.
    check("'1–10 de más de 10' se descarta",
          d._leer_rango_filas(DriverFalso("1–10 de más de 10")) is None)

    # Una carátula NO tiene forma de rango con guión, así que no la pesca.
    check("carátula 'Expediente 21 de 2024' no se confunde con un total",
          d._leer_rango_filas(DriverFalso(page_source="Expediente 21 de 2024")) is None)
    check("sin paginación devuelve None",
          d._leer_rango_filas(DriverFalso(page_source="<html>nada</html>")) is None)
    check("total absurdo se descarta por la cota de cordura",
          d._leer_rango_filas(DriverFalso("1–10 de 999999")) is None)
    check("rango incoherente (hasta > total) se descarta",
          d._leer_rango_filas(DriverFalso("1–99 de 10")) is None)


def test_lecturas_minimas_por_pagina():
    """
    Regresión de rendimiento: cada find_elements/page_source es un viaje de
    ida y vuelta a Chrome. En un expediente descargado en producción (23035,
    116 archivos en 12 páginas) esto corría el doble de lo necesario por
    página. Estos tests fijan el comportamiento correcto para que no vuelva
    a desviarse.
    """
    print("\n[1b] Se leen el mínimo de veces por página (no se repite el viaje a Chrome)")
    d = _descargador()

    # _leer_rango_filas debe cortar apenas el PRIMER selector da un match
    # utilizable: no tiene que consultar los otros 3 ni caer al page_source.
    driver = DriverContador(textos_por_selector={
        d._SELECTORES_TOTAL_FILAS[0]: "1–10 de 213",
    })
    resultado = d._leer_rango_filas(driver)
    check("con match en el primer selector, no se consultan los demás",
          len(driver.llamadas_find_elements) == 1,
          f"consultó {len(driver.llamadas_find_elements)} selector(es): {driver.llamadas_find_elements}")
    check("...y no cae al page_source de respaldo",
          driver.lecturas_page_source == 0)
    check("...y el resultado es el correcto", resultado == (1, 10, 213))

    # Si ningún selector matchea, tiene que probarlos TODOS antes de caer al
    # page_source (no debe abandonar antes de tiempo).
    driver2 = DriverContador(page_source="1–10 de 213")
    resultado2 = d._leer_rango_filas(driver2)
    check("sin match en ningún selector, prueba los 4 antes de usar page_source",
          len(driver2.llamadas_find_elements) == len(d._SELECTORES_TOTAL_FILAS))
    check("...y encuentra el total en el page_source de respaldo",
          resultado2 == (1, 10, 213))

    # El caso que de verdad estaba duplicado: antes del fix, tanto
    # _detectar_total_movimientos como _navegar_siguiente_pagina llamaban a
    # _detectar_paginacion por su cuenta, cada una con su propia lectura de
    # driver.page_source, DOS VECES por página. _leer_rango_filas es un dato
    # aparte (el rango de filas de MUI) y sigue leyendo lo suyo — lo que se
    # comparte acá es sólo el "Página X de Y".
    #
    # _navegar_siguiente_pagina() empieza con un time.sleep(1) real; se lo
    # neutraliza para no frenar la corrida de tests por esto.
    import modulos.descarga as _descarga_mod
    sleep_original = _descarga_mod.time.sleep
    _descarga_mod.time.sleep = lambda *a, **k: None
    try:
        # Con la paginación ya calculada e indicando "última página", no
        # tiene que releer driver.page_source ni una vez: usa la caché y
        # devuelve False de inmediato.
        driver3 = DriverContador(page_source="Página 22 de 22")
        resultado3 = d._navegar_siguiente_pagina(driver3, paginacion_cacheada=(22, 22))
        check("con paginación cacheada, _navegar_siguiente_pagina no relee page_source",
              resultado3 is False and driver3.lecturas_page_source == 0,
              f"resultado={resultado3} lecturas={driver3.lecturas_page_source}")

        # Sin caché (comportamiento por defecto de quien la llame suelta,
        # como el otro call site en obtener_movimientos), sigue calculándola
        # ella misma y llega a la misma conclusión.
        driver4 = DriverContador(page_source="Página 22 de 22")
        resultado4 = d._navegar_siguiente_pagina(driver4)
        check("sin paginación cacheada, la calcula igual y llega al mismo resultado",
              resultado4 is False and driver4.lecturas_page_source >= 1)
    finally:
        _descarga_mod.time.sleep = sleep_original


# ═══════════════════════════════════════════════════════════════════════════
#  2. Estimación del total de archivos
# ═══════════════════════════════════════════════════════════════════════════

def test_detectar_total():
    print("\n[2] Estimación del total de archivos")
    d = _descargador()

    # Caso A: etiqueta MUI, expediente de varias páginas
    total, exacto, paginas = d._detectar_total_movimientos(
        DriverFalso("1–10 de 213", "Página 1 de 22"), botones_pagina=10, pagina_actual=1, ya_intentados=0)
    check("213 filas / 10 por página -> total 213, estimado", (total, exacto) == (213, False),
          f"total={total} exacto={exacto} paginas={paginas}")

    # Caso A con filas sin botón de descarga: se corrige por la proporción vista
    total, exacto, _ = d._detectar_total_movimientos(
        DriverFalso("1–10 de 200"), botones_pagina=8, pagina_actual=1, ya_intentados=0)
    check("sólo 8 de 10 filas traen archivo -> total se ajusta a ~160", total == 160,
          f"total={total}")

    # Caso A de una sola página: los botones SON el total, y eso sí es exacto
    total, exacto, _ = d._detectar_total_movimientos(
        DriverFalso("1–7 de 7"), botones_pagina=7, pagina_actual=1, ya_intentados=0)
    check("expediente de una sola página -> total exacto", (total, exacto) == (7, True),
          f"total={total} exacto={exacto}")

    # Caso B: sin etiqueta MUI pero con "Página X de Y"
    total, exacto, paginas = d._detectar_total_movimientos(
        DriverFalso(page_source="Página 1 de 22"), botones_pagina=10, pagina_actual=1, ya_intentados=0)
    check("sin etiqueta, 22 páginas x 10 botones -> 220 estimado",
          (total, exacto, paginas) == (220, False, 22), f"total={total} paginas={paginas}")

    # Caso B en página 2+: promedia lo realmente visto
    total, _, _ = d._detectar_total_movimientos(
        DriverFalso(page_source="Página 2 de 10"), botones_pagina=10, pagina_actual=2, ya_intentados=10)
    check("página 2 de 10 con 20 vistos -> 100 estimado", total == 100, f"total={total}")

    # Caso B envenenado por una carátula: mejor sin denominador que con uno delirante
    total, _, _ = d._detectar_total_movimientos(
        DriverFalso(page_source="Expediente 21 de 2024"), botones_pagina=10, pagina_actual=1, ya_intentados=0)
    check("carátula '21 de 2024' no produce un total de 20240", total != 20240, f"total={total}")
    check("...y el total que devuelve es razonable", total is None or total <= 20000, f"total={total}")

    # Caso C: sin nada -> página única, lo que se ve es lo que hay
    total, exacto, _ = d._detectar_total_movimientos(
        DriverFalso(page_source="<html>sin paginacion</html>"), botones_pagina=5, pagina_actual=1, ya_intentados=0)
    check("sin paginación -> total = botones de la página", total == 5, f"total={total}")

    # Nunca por debajo de lo ya recorrido (evita "215 de 213")
    total, _, _ = d._detectar_total_movimientos(
        DriverFalso("1–10 de 12"), botones_pagina=10, pagina_actual=3, ya_intentados=40)
    check("el total nunca queda por debajo de lo ya visto", total >= 50, f"total={total}")

    # Un driver que explota no debe romper nada
    class DriverRoto:
        @property
        def page_source(self):
            raise RuntimeError("boom")

        def find_elements(self, *a):
            raise RuntimeError("boom")

    total, exacto, paginas = d._detectar_total_movimientos(DriverRoto(), 10, 1, 0)
    check("driver que lanza excepción -> no propaga", True, f"total={total}")


# ═══════════════════════════════════════════════════════════════════════════
#  3. El progreso nunca puede tumbar una descarga
# ═══════════════════════════════════════════════════════════════════════════

def test_callback_roto_no_rompe():
    print("\n[3] Un callback roto no interrumpe el pipeline")

    from modulos.pipeline import PipelineDescargador

    def callback_explosivo(datos):
        raise RuntimeError("el frontend se cayó")

    p = PipelineDescargador()
    p._on_progreso = callback_explosivo
    try:
        p._emitir(fase='auth', actual=0, total=None)
        check("pipeline._emitir traga la excepción del callback", True)
    except Exception as e:
        check("pipeline._emitir traga la excepción del callback", False, repr(e))

    # El mismo contrato en el unificador
    from modulos.unificacion import UnificadorPDF
    import tempfile
    u = UnificadorPDF(tempfile.mkdtemp())
    try:
        resultado = u.unificar("1/24", [], on_progreso=callback_explosivo)
        check("unificar() acepta on_progreso y no rompe con lista vacía", resultado is None)
    except Exception as e:
        check("unificar() acepta on_progreso y no rompe con lista vacía", False, repr(e))


def test_firmas_compatibles():
    print("\n[4] Los llamadores viejos siguen funcionando")
    import inspect
    from modulos.pipeline import PipelineDescargador
    from modulos.unificacion import UnificadorPDF

    for func, nombre in (
        (DescargadorArchivos.descargar_todo_por_paginas, "descargar_todo_por_paginas"),
        (PipelineDescargador.ejecutar, "PipelineDescargador.ejecutar"),
        (UnificadorPDF.unificar, "UnificadorPDF.unificar"),
    ):
        sig = inspect.signature(func)
        param = sig.parameters.get('on_progreso')
        check(f"{nombre} recibe on_progreso opcional",
              param is not None and param.default is None)


# ═══════════════════════════════════════════════════════════════════════════
#  5. El endpoint /descargas/progreso
# ═══════════════════════════════════════════════════════════════════════════

def _app_minima():
    """App Flask mínima con el blueprint de descargas y un usuario logueado."""
    from flask import Flask
    from flask_login import LoginManager
    from modulos.database import db
    from modulos.models import User
    import rutas.descargas as rd

    app = Flask(__name__, template_folder='templates')
    app.config.update(
        TESTING=True,
        SECRET_KEY='test',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        WTF_CSRF_ENABLED=False,
        LOGIN_DISABLED=False,
    )
    db.init_app(app)

    lm = LoginManager()
    lm.init_app(app)

    @lm.user_loader
    def cargar(uid):
        return db.session.get(User, int(uid))

    app.register_blueprint(rd.descargas_bp)

    with app.app_context():
        db.create_all()
        u = User(email='test@foja.test', nombre='Test')
        u.establecer_password('x')
        u.creditos_disponibles = 5
        db.session.add(u)
        db.session.commit()
        user_id = u.id

    return app, rd, user_id


def test_endpoint_progreso():
    print("\n[5] Endpoint GET /descargas/progreso/<job_id>")

    app, rd, user_id = _app_minima()
    cliente = app.test_client()
    with cliente.session_transaction() as s:
        s['_user_id'] = str(user_id)
        s['_fresh'] = True

    rd._jobs['job-mio'] = {
        'estado': 'procesando', 'user_id': user_id, 'timestamp': time.time(),
        'progreso': {'fase': 'descarga', 'actual': 47, 'total': 213, 'total_exacto': False},
    }
    rd._jobs['job-ajeno'] = {
        'estado': 'procesando', 'user_id': user_id + 999, 'timestamp': time.time(),
        'progreso': {'fase': 'descarga', 'actual': 1, 'total': 2, 'total_exacto': True},
    }

    inicio = time.time()
    r = cliente.get('/descargas/progreso/job-mio')
    demora = time.time() - inicio
    datos = r.get_json()

    check("responde 200 con el progreso", r.status_code == 200 and datos['progreso']['actual'] == 47,
          f"status={r.status_code} datos={datos}")
    check("responde en el acto (no hace long-poll de 25s)", demora < 2, f"{demora:.2f}s")
    check("no filtra user_id ni timestamp",
          set(datos.keys()) == {'estado', 'progreso'}, f"claves={sorted(datos.keys())}")
    check("el total viaja para poder mostrar 'de 213'", datos['progreso']['total'] == 213)

    r = cliente.get('/descargas/progreso/job-ajeno')
    check("un job de otro usuario da 404", r.status_code == 404, f"status={r.status_code}")

    r = cliente.get('/descargas/progreso/no-existe')
    check("un job inexistente da 404 con la misma forma",
          r.status_code == 404 and r.get_json()['estado'] == 'no_encontrado')


def test_progreso_no_rompe_estado():
    """
    Regresión: /estado hace jsonify(job), que ITERA el dict del job. Si el thread
    del pipeline insertara una clave nueva justo durante esa iteración, CPython
    tiraría "dictionary changed size during iteration" -> 500 en HTML -> el
    "Unexpected token '<'" del frontend. Por eso 'progreso' se pre-siembra al
    crear el job y sólo se reasigna su valor, nunca se agregan claves nuevas.
    """
    print("\n[6] Publicar progreso no rompe la serialización de /estado")

    job = {
        'estado': 'procesando', 'user_id': 1, 'timestamp': time.time(),
        'progreso': {'fase': 'iniciando', 'actual': 0, 'total': None, 'total_exacto': False},
    }

    errores = []
    parar = threading.Event()

    def escritor():
        n = 0
        while not parar.is_set():
            n += 1
            # Igual que _publicar_progreso: dict nuevo, clave que ya existe.
            job['progreso'] = {'fase': 'descarga', 'actual': n, 'total': 213,
                               'total_exacto': False, 'actualizado': time.time()}

    def lector():
        import json
        try:
            for _ in range(4000):
                json.dumps(dict(job))     # equivalente a lo que hace jsonify
        except Exception as e:
            errores.append(e)

    t = threading.Thread(target=escritor, daemon=True)
    t.start()
    lector()
    parar.set()
    t.join(timeout=2)

    check("serializar el job mientras se publica progreso no lanza",
          not errores, repr(errores[:1]))

    claves_finales = set(job.keys())
    check("el conjunto de claves del job no cambió",
          claves_finales == {'estado', 'user_id', 'timestamp', 'progreso'},
          f"claves={sorted(claves_finales)}")


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE PROGRESO DE DESCARGA")
    print("=" * 70)

    test_leer_rango_filas()
    test_lecturas_minimas_por_pagina()
    test_detectar_total()
    test_callback_roto_no_rompe()
    test_firmas_compatibles()
    test_endpoint_progreso()
    test_progreso_no_rompe_estado()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
