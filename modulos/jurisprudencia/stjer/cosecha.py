"""
Cosecha del corpus STJER
========================

Barre el buscador y llena el corpus local. Dos pasadas, a proposito separadas:

  **Pasada A - listados** (~1.060 requests, 3-6 h): una busqueda por mes,
  paginando. La fila del listado ya trae jurisdiccion, organismo, fecha,
  expediente, caratula, fuero y el extracto del sumario. **Con esto solo el
  buscador local ya funciona.**

  **Pasada B - detalles** (~14.800 requests, 10-16 h): abre cada fallo y suma
  voces, votos y el sumario sin truncar. Se puede cortar en cualquier momento
  y lo cosechado queda usable.

Particion por mes y nada mas: son ~56 fallos/mes, o sea 2-3 paginas.
Sub-particionar tambien por fuero quintuplicaria los requests sin ganar
informacion, porque el fuero ya viene como columna en la fila.

Todo el diseño esta armado alrededor de una idea: **cortar la corrida no
cuesta trabajo, cuesta demora.** La cola es durable, los upserts son
idempotentes y las respuestas crudas quedan archivadas.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from . import corpus, parser
from .cliente import ErrorCaptcha, ErrorCliente

logger = logging.getLogger(__name__)

TIPO_LISTA = "lista_mes"
TIPO_DETALLE = "detalle"
MAX_PAGINAS_POR_MES = 60  # freno por si la paginacion nunca dice que termino


class CosechaAbortada(Exception):
    """
    Se corto la corrida entera.

    Pasa con el disyuntor de fallos consecutivos o con el muro de captcha. No
    se perdio nada: la cola sabe donde estaba.
    """


@dataclass
class Resumen:
    tareas_ok: int = 0
    tareas_error: int = 0
    fallos_nuevos: int = 0
    fallos_actualizados: int = 0
    sumarios: int = 0
    requests: int = 0
    segundos: float = 0.0
    abortada_por: str = ""
    errores: list = field(default_factory=list)

    def como_dict(self) -> dict:
        return {
            "tareas_ok": self.tareas_ok,
            "tareas_error": self.tareas_error,
            "fallos_nuevos": self.fallos_nuevos,
            "fallos_actualizados": self.fallos_actualizados,
            "sumarios": self.sumarios,
            "requests": self.requests,
            "segundos": round(self.segundos, 1),
            "abortada_por": self.abortada_por,
            "errores": self.errores[:20],
        }


def meses_entre(desde: date, hasta: date) -> list:
    """['2004-01', '2004-02', ...] inclusive en ambos extremos."""
    meses = []
    a, m = desde.year, desde.month
    while (a, m) <= (hasta.year, hasta.month):
        meses.append(f"{a:04d}-{m:02d}")
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return meses


def rango_del_mes(mes: str):
    """'2019-03' -> (date(2019,3,1), date(2019,3,31))."""
    import calendar

    a, m = int(mes[:4]), int(mes[5:7])
    return date(a, m, 1), date(a, m, calendar.monthrange(a, m)[1])


class Cosechadora:
    """
    Orquesta la cosecha sobre un ClienteSTJER cualquiera.

    No sabe ni le importa si abajo hay requests o un navegador.
    """

    def __init__(
        self,
        cliente,
        con,
        guardar_crudo: bool = True,
        max_fallos_consecutivos: int = None,
        max_reintentos: int = None,
    ):
        from . import ajustes

        self.cliente = cliente
        self.con = con
        self.guardar_crudo = guardar_crudo
        self.max_fallos_consecutivos = (
            ajustes.MAX_FALLOS_CONSECUTIVOS
            if max_fallos_consecutivos is None
            else max_fallos_consecutivos
        )
        self.max_reintentos = (
            ajustes.MAX_REINTENTOS if max_reintentos is None else max_reintentos
        )
        self._fallos_consecutivos = 0

    # ── planificacion ─────────────────────────────────────────────────────

    def planificar_listados(self, desde: date, hasta: date) -> int:
        """
        Encola un mes por tarea, de una sola vez.

        Planificar antes de pedir hace que la cola sea durable e
        inspeccionable con `stjer estado` desde el minuto cero, en vez de ir
        descubriendose sobre la marcha.
        """
        nuevas = 0
        with corpus.transaccion(self.con):
            for mes in meses_entre(desde, hasta):
                # Prioridad por año: si se corta, quedan cosechados los
                # recientes, que son los que mas se consultan.
                if corpus.encolar(self.con, TIPO_LISTA, mes, prioridad=int(mes[:4])):
                    nuevas += 1
        logger.info(
            "Planificados %d meses nuevos (%s a %s)", nuevas, desde, hasta
        )
        return nuevas

    def planificar_detalles(self, limite: int = None) -> int:
        """Encola un detalle por cada fallo al que todavia le falta."""
        sql = (
            "SELECT clave_natural, anio FROM fallos "
            "WHERE detalle_ok=0 ORDER BY fecha_fallo DESC"
        )
        if limite:
            sql += f" LIMIT {int(limite)}"

        nuevas = 0
        with corpus.transaccion(self.con):
            for fila in self.con.execute(sql).fetchall():
                if corpus.encolar(
                    self.con, TIPO_DETALLE, fila["clave_natural"],
                    prioridad=fila["anio"] or 0,
                ):
                    nuevas += 1
        logger.info("Planificados %d detalles nuevos", nuevas)
        return nuevas

    # ── ejecucion ─────────────────────────────────────────────────────────

    def ejecutar(self, tipo: str, limite: int = None, orden: str = "reciente") -> Resumen:
        """
        Procesa tareas pendientes hasta agotarlas o llegar al limite.

        Cada tarea se cierra apenas termina, asi que un Ctrl-C cuesta como
        mucho un item.
        """
        resumen = Resumen()
        t0 = time.monotonic()

        liberadas = corpus.liberar_huerfanas(self.con)
        if liberadas:
            logger.info("Recuperadas %d tareas de una corrida anterior", liberadas)

        procesadas = 0
        try:
            while limite is None or procesadas < limite:
                tarea = corpus.tomar_tarea(self.con, tipo, orden=orden)
                if tarea is None:
                    break
                procesadas += 1
                self._procesar(tarea, tipo, resumen)
        except CosechaAbortada as e:
            resumen.abortada_por = str(e)
            logger.error("Cosecha abortada: %s", e)
        finally:
            resumen.segundos = time.monotonic() - t0

        return resumen

    def _procesar(self, tarea, tipo: str, resumen: Resumen) -> None:
        """Ejecuta una tarea y la cierra segun como haya salido."""
        clave = tarea["clave"]
        try:
            if tipo == TIPO_LISTA:
                self._cosechar_mes(clave, resumen)
            elif tipo == TIPO_DETALLE:
                self._cosechar_detalle(clave, resumen)
            else:
                raise ValueError(f"Tipo de tarea desconocido: {tipo}")

        except ErrorCaptcha as e:
            # No fallo la tarea: se corto la sesion. Vuelve a la cola SIN
            # contarla como intento, y se corta la corrida.
            corpus.devolver_tarea(self.con, tarea["id"], str(e))
            raise CosechaAbortada(
                "El sitio pide verificacion. Reabri la sesion con:\n"
                "    python -m scripts.stjer sesion --abrir\n"
                "y volve a lanzar el mismo comando: retoma donde iba."
            ) from e

        except (ErrorCliente, Exception) as e:
            self._fallos_consecutivos += 1
            resumen.tareas_error += 1
            resumen.errores.append(f"{tipo}:{clave}: {e}")
            logger.warning("Tarea %s:%s fallo: %s", tipo, clave, e)

            # Se marca 'error' y se sigue con la proxima. Los reintentos con
            # backoff ya los hizo el cliente a nivel de red; volver a encolar
            # aca haria que la corrida se trabe martillando el mismo item
            # cuatro veces seguidas, y de paso dispararia el disyuntor por
            # culpa de un solo mes roto. Para reintentarlos: `stjer reparar`.
            corpus.cerrar_tarea(self.con, tarea["id"], "error", str(e))

            if self._fallos_consecutivos >= self.max_fallos_consecutivos:
                raise CosechaAbortada(
                    f"{self._fallos_consecutivos} fallos seguidos. O nos "
                    f"bloquearon o se cayo el sitio; seguir seria martillarlos. "
                    f"Probá de nuevo mas tarde: la cola retoma donde iba."
                ) from e
        else:
            self._fallos_consecutivos = 0
            resumen.tareas_ok += 1
            corpus.cerrar_tarea(self.con, tarea["id"], "ok")

    # ── pasada A: listados ────────────────────────────────────────────────

    def _cosechar_mes(self, mes: str, resumen: Resumen) -> None:
        """
        Trae todas las paginas de un mes y guarda las filas.

        Al final registra el "Se encontraron N registros" que declaro el sitio
        para poder reconciliar: si lo guardado no coincide, hubo un bug de
        paginacion y el mes se vuelve a encolar.
        """
        desde, hasta = rango_del_mes(mes)
        pagina, total_paginas, esperados = 1, None, None

        while pagina <= MAX_PAGINAS_POR_MES:
            respuesta = self.cliente.buscar_listado(desde, hasta, pagina=pagina)
            resumen.requests += 1

            if self.guardar_crudo:
                with corpus.transaccion(self.con):
                    corpus.guardar_crudo(
                        self.con, TIPO_LISTA, f"{mes}#p{pagina}",
                        respuesta.crudo, respuesta.estado,
                    )

            listado = parser.parsear_listado(respuesta.html)
            if esperados is None and listado.total_registros is not None:
                esperados = listado.total_registros
            if total_paginas is None:
                total_paginas = listado.total_paginas

            if not listado.filas:
                break

            self._guardar_filas(listado.filas, pagina, resumen)

            if listado.total_paginas is not None:
                if pagina >= listado.total_paginas:
                    break
            elif not listado.hay_siguiente:
                break
            pagina += 1

        if esperados is not None:
            with corpus.transaccion(self.con):
                corpus.registrar_esperados(self.con, mes, esperados)

        logger.info(
            "Mes %s: %s paginas, %s registros declarados",
            mes, pagina, esperados if esperados is not None else "?",
        )

    def _guardar_filas(self, filas: list, pagina: int, resumen: Resumen) -> None:
        """Guarda las filas de una pagina de resultados."""
        with corpus.transaccion(self.con):
            for fila in filas:
                existia = self.con.execute(
                    "SELECT 1 FROM fallos WHERE clave_natural=?",
                    (corpus.clave_natural(fila),),
                ).fetchone()

                fila["pagina"] = pagina
                fallo_id = corpus.upsert_fallo(self.con, fila)
                if existia:
                    resumen.fallos_actualizados += 1
                else:
                    resumen.fallos_nuevos += 1

                extracto = fila.get("sumario_extracto")
                if extracto:
                    # agregar_extracto acumula sin borrar: el mismo fallo
                    # aparece N veces en el listado (una por sumario) y cada
                    # llamada agrega solo si el texto es nuevo.
                    if corpus.agregar_extracto(self.con, fallo_id, extracto):
                        resumen.sumarios += 1
                        corpus.reconstruir_documentos(self.con, fallo_id)

    # ── pasada B: detalles ────────────────────────────────────────────────

    def _cosechar_detalle(self, clave: str, resumen: Resumen) -> None:
        """Abre un fallo y guarda sumarios completos, voces y votos."""
        fila = self.con.execute(
            "SELECT id, ref_detalle, mes, pagina FROM fallos WHERE clave_natural=?", (clave,)
        ).fetchone()
        if fila is None:
            logger.warning("El fallo %s ya no esta en el corpus", clave)
            return

        respuesta = self.cliente.abrir_detalle(
            fila["ref_detalle"] or clave, mes=fila["mes"], pagina=fila["pagina"]
        )
        resumen.requests += 1

        if self.guardar_crudo:
            with corpus.transaccion(self.con):
                corpus.guardar_crudo(
                    self.con, TIPO_DETALLE, clave, respuesta.crudo, respuesta.estado
                )

        detalle = parser.parsear_detalle(respuesta.html)

        with corpus.transaccion(self.con):
            datos = detalle.como_fallo()
            datos["clave_natural"] = clave
            fallo_id = corpus.upsert_fallo(self.con, datos)

            if detalle.sumarios:
                resumen.sumarios += corpus.reemplazar_sumarios(
                    self.con, fallo_id, detalle.sumarios, truncado=False
                )
            corpus.reemplazar_votos(self.con, fallo_id, detalle.votos)
            corpus.marcar_detalle_ok(self.con, fallo_id)

        corpus.reconstruir_documentos(self.con, fallo_id)

    # ── mantenimiento ─────────────────────────────────────────────────────

    def reparar(self) -> dict:
        """Vuelve a encolar los errores recuperables y los meses descuadrados."""
        reencoladas = corpus.reencolar_errores(self.con, self.max_reintentos)

        diferencias = corpus.diferencias_reconciliacion(self.con)
        with corpus.transaccion(self.con):
            for d in diferencias:
                self.con.execute(
                    "UPDATE cosecha_tareas SET estado='pendiente', intentos=0 "
                    "WHERE tipo=? AND clave=?",
                    (TIPO_LISTA, d["mes"]),
                )
        if diferencias:
            logger.warning(
                "Re-encolados %d meses donde lo guardado no coincide con lo "
                "que declaro el sitio: %s",
                len(diferencias), ", ".join(d["mes"] for d in diferencias[:10]),
            )

        return {
            "tareas_reencoladas": reencoladas,
            "meses_descuadrados": len(diferencias),
            "detalle": diferencias[:20],
        }


def reparsear_crudos(con, tipo: str = TIPO_DETALLE) -> dict:
    """
    Vuelve a parsear las respuestas archivadas, sin tocar la red.

    Esta es la razon de ser de `respuestas_crudas`: el parser va a estar mal
    en la primera pasada. Con el archivo, corregirlo cuesta dos minutos de
    reproceso; sin el, cuesta cuarenta horas de re-cosecha.
    """
    procesados = errores = sumarios = 0

    for clave, html in corpus.iterar_crudos(con, tipo):
        try:
            if tipo == TIPO_DETALLE:
                detalle = parser.parsear_detalle(html)
                datos = detalle.como_fallo()
                datos["clave_natural"] = clave
                with corpus.transaccion(con):
                    fallo_id = corpus.upsert_fallo(con, datos)
                    if detalle.sumarios:
                        sumarios += corpus.reemplazar_sumarios(
                            con, fallo_id, detalle.sumarios, truncado=False
                        )
                    corpus.reemplazar_votos(con, fallo_id, detalle.votos)
                    corpus.marcar_detalle_ok(con, fallo_id)
            else:
                listado = parser.parsear_listado(html)
                with corpus.transaccion(con):
                    for f in listado.filas:
                        fallo_id = corpus.upsert_fallo(con, f)
                        if f.get("sumario_extracto"):
                            sumarios += corpus.reemplazar_sumarios(
                                con, fallo_id,
                                [{"texto": f["sumario_extracto"]}], truncado=True,
                            )
            procesados += 1
        except Exception as e:
            errores += 1
            logger.warning("No se pudo re-parsear %s:%s: %s", tipo, clave, e)

    corpus.reconstruir_documentos(con)
    return {"procesados": procesados, "errores": errores, "sumarios": sumarios}
