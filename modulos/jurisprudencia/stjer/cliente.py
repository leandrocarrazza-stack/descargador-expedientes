"""
Cliente del buscador STJER
==========================

Una sola interfaz (`ClienteSTJER`) con tres implementaciones posibles. Cual se
usa lo decide la Fase 0, y cambiarla toca **una sola llamada al constructor**:
todo lo de arriba consume `RespuestaCruda` y no sabe como se obtuvo.

    Rama A  ClienteHTTP(..., ah_fijo=True)   requests puro, token constante
    Rama B  ClienteHTTP(...)                 requests + token ah arrastrado
    Rama C  ClienteNavegador(...)            Playwright maneja la UI real

La rama B es la mas probable (~55%): en Toba el hash de sesion suele venir
literal en la respuesta anterior, asi que alcanza con leerlo y reenviarlo.
"""

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from . import ajustes
from .parser import extraer_token_ah, hay_captcha

logger = logging.getLogger(__name__)


class ErrorCliente(Exception):
    """Fallo al hablar con el sitio."""


class ErrorCaptcha(ErrorCliente):
    """
    Llego la pared de verificacion en vez de datos.

    No es un fallo de la tarea: es que se corto la sesion. Quien lo atrape
    tiene que devolver la tarea a la cola SIN contarla como intento.
    """


@dataclass
class RespuestaCruda:
    """Lo que devuelve cualquier cliente, venga de donde venga."""

    url: str = ""
    estado: int = 0
    html: str = ""      # ya desenvuelto de Toba por el parser, si hizo falta
    crudo: str = ""     # el cuerpo tal cual llego, para archivar
    ms: int = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.estado < 300


class ClienteSTJER(Protocol):
    """Lo minimo que la cosecha necesita de un cliente."""

    def buscar_listado(
        self, desde: date, hasta: date, fuero=None, pagina: int = 1
    ) -> RespuestaCruda: ...

    def abrir_detalle(self, ref: str, mes: str = None, pagina: int = None) -> RespuestaCruda: ...

    def arbol_tesauro(self, ref=None) -> RespuestaCruda: ...


# ═══════════════════════════════════════════════════════════════════════════
#  Cortesia
# ═══════════════════════════════════════════════════════════════════════════

class Regulador:
    """
    Espera entre requests + techo duro por hora.

    ~15.900 requests en una noche son ~0,25 req/s. Es notorio pero no abusivo;
    el techo esta para que un bug de reintentos no se convierta en una
    inundacion.
    """

    def __init__(self, espera=None, jitter=None, max_req_hora=None):
        self.espera = ajustes.ESPERA_SEG if espera is None else espera
        self.jitter = ajustes.JITTER_SEG if jitter is None else jitter
        self.max_req_hora = (
            ajustes.MAX_REQ_HORA if max_req_hora is None else max_req_hora
        )
        self._ultimo = 0.0
        self._marcas = []

    def esperar(self) -> None:
        ahora = time.monotonic()

        # Techo por hora
        self._marcas = [t for t in self._marcas if ahora - t < 3600]
        if len(self._marcas) >= self.max_req_hora:
            dormir = 3600 - (ahora - self._marcas[0]) + 1
            logger.warning(
                "Techo de %d requests/hora alcanzado; durmiendo %.0f s",
                self.max_req_hora, dormir,
            )
            time.sleep(max(dormir, 0))
            ahora = time.monotonic()
            self._marcas = [t for t in self._marcas if ahora - t < 3600]

        # Espera entre requests, con jitter para no marcar un patron exacto
        objetivo = self.espera + random.uniform(0, self.jitter)
        transcurrido = ahora - self._ultimo
        if self._ultimo and transcurrido < objetivo:
            time.sleep(objetivo - transcurrido)

        self._ultimo = time.monotonic()
        self._marcas.append(self._ultimo)


# ═══════════════════════════════════════════════════════════════════════════
#  Rama A / B: HTTP puro
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FormatoConsulta:
    """
    Como se arma el POST de busqueda.

    Los nombres de los campos salen de la Fase 0 (paso B.5, "Copy as cURL").
    Se dejan aca como datos y no clavados en el codigo para que ajustarlos sea
    editar un dict, no parchear logica.
    """

    ai_busqueda: str = "jur||newpublica"
    campo_fecha_desde: str = "fecha_desde"
    campo_fecha_hasta: str = "fecha_hasta"
    campo_fuero: str = "fuero"
    campo_pagina: str = "pagina"
    campo_filas: str = "filas"          # si es configurable, subirlo al maximo
    filas_por_pagina: int = 25
    accion_buscar: str = "buscar"
    campo_accion: str = "toba_accion"
    formato_fecha: str = "%d/%m/%Y"
    extra: dict = field(default_factory=dict)

    @classmethod
    def cargar(cls, ruta=None) -> "FormatoConsulta":
        """Overrides desde descubrimiento/formato_consulta.json."""
        import json
        from pathlib import Path

        if ruta is None:
            ruta = ajustes.DESCUBRIMIENTO_DIR / "formato_consulta.json"
        ruta = Path(ruta)
        if not ruta.exists():
            return cls()
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("No se pudo leer %s: %s", ruta, e)
            return cls()
        conocidos = {k: v for k, v in datos.items() if k in cls.__dataclass_fields__}
        logger.info("Formato de consulta cargado desde %s", ruta)
        return cls(**conocidos)


class ClienteHTTP:
    """
    Cliente sobre requests.Session. Es el camino rapido.

    Necesita cookies de una sesion con el captcha ya resuelto: se sacan una
    sola vez con `SesionSTJER.cookies_para_requests()` y despues toda la
    cosecha corre sin navegador.

    `ah_fijo=True` es la rama A (el token no cambia nunca). Por defecto se
    re-lee de cada respuesta, que es la rama B.
    """

    def __init__(
        self,
        cookies: dict,
        ah: str = None,
        formato: FormatoConsulta = None,
        regulador: Regulador = None,
        ah_fijo: bool = False,
    ):
        import requests  # se importa aca para no exigirlo al buscar offline

        self.sesion = requests.Session()
        self.sesion.headers.update(
            {
                "User-Agent": ajustes.UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.sesion.cookies.update(cookies or {})
        self.ah = ah
        self.ah_fijo = ah_fijo
        self.formato = formato or FormatoConsulta.cargar()
        self.regulador = regulador or Regulador()

    # ── infraestructura ───────────────────────────────────────────────────

    def _pedir(self, datos: dict, etiqueta: str) -> RespuestaCruda:
        """
        POST con reintentos, backoff exponencial y deteccion de captcha.

        Los 4xx no se reintentan: si el servidor dice que la peticion esta
        mal, repetirla no la va a arreglar.
        """
        import requests

        params = {"ai": self.formato.ai_busqueda}
        if self.ah:
            params["ah"] = self.ah

        ultimo_error = None
        for intento in range(ajustes.MAX_REINTENTOS):
            self.regulador.esperar()
            t0 = time.monotonic()
            try:
                resp = self.sesion.post(
                    ajustes.URL_APLICACION,
                    params=params,
                    data=datos,
                    timeout=ajustes.TIMEOUT_SEG,
                )
            except requests.RequestException as e:
                ultimo_error = e
                espera = 2 ** intento
                logger.warning(
                    "%s: fallo de red (%s). Reintento en %ds", etiqueta, e, espera
                )
                time.sleep(espera)
                continue

            ms = int((time.monotonic() - t0) * 1000)

            if resp.status_code >= 500:
                ultimo_error = ErrorCliente(f"HTTP {resp.status_code}")
                espera = 2 ** intento
                logger.warning(
                    "%s: HTTP %d. Reintento en %ds", etiqueta, resp.status_code, espera
                )
                time.sleep(espera)
                continue

            if resp.status_code in (429, 503):
                espera = int(resp.headers.get("Retry-After", 2 ** (intento + 3)))
                logger.warning("%s: nos pidieron frenar. Durmiendo %ds", etiqueta, espera)
                time.sleep(espera)
                continue

            if resp.status_code >= 400:
                raise ErrorCliente(f"{etiqueta}: HTTP {resp.status_code}")

            crudo = resp.text
            if hay_captcha(crudo):
                raise ErrorCaptcha(
                    f"{etiqueta}: el sitio pide verificacion. Reabri la sesion con:\n"
                    f"    python -m scripts.stjer sesion --abrir"
                )

            if not self.ah_fijo:
                nuevo = extraer_token_ah(crudo)
                if nuevo and nuevo != self.ah:
                    logger.debug("Token ah actualizado: %s", nuevo)
                    self.ah = nuevo

            return RespuestaCruda(
                url=resp.url, estado=resp.status_code, html=crudo, crudo=crudo, ms=ms
            )

        raise ErrorCliente(f"{etiqueta}: agotados los reintentos ({ultimo_error})")

    # ── operaciones ───────────────────────────────────────────────────────

    def buscar_listado(self, desde, hasta, fuero=None, pagina=1) -> RespuestaCruda:
        f = self.formato
        datos = {
            f.campo_accion: f.accion_buscar,
            f.campo_fecha_desde: desde.strftime(f.formato_fecha),
            f.campo_fecha_hasta: hasta.strftime(f.formato_fecha),
            f.campo_pagina: str(pagina),
            f.campo_filas: str(f.filas_por_pagina),
        }
        if fuero:
            datos[f.campo_fuero] = fuero
        datos.update(f.extra)
        return self._pedir(datos, f"listado {desde:%Y-%m} p{pagina}")

    def abrir_detalle(self, ref: str, mes: str = None, pagina: int = None) -> RespuestaCruda:
        # mes/pagina no hacen falta aca: el POST no depende de estar parado
        # en ningun listado en pantalla (a diferencia de la rama navegador).
        datos = {self.formato.campo_accion: "detalle", "ref": ref}
        datos.update(self.formato.extra)
        return self._pedir(datos, f"detalle {ref[:40]}")

    def arbol_tesauro(self, ref=None) -> RespuestaCruda:
        datos = {self.formato.campo_accion: "tesauro"}
        if ref:
            datos["ref"] = ref
        datos.update(self.formato.extra)
        return self._pedir(datos, f"tesauro {ref or 'raiz'}")


# ═══════════════════════════════════════════════════════════════════════════
#  Rama C: navegador
# ═══════════════════════════════════════════════════════════════════════════

class ClienteNavegador:
    """
    Cliente que maneja la UI real con Playwright.

    Es el plan de contingencia: funciona aunque el POST de Toba tenga tokens
    calculados en JS que no se puedan replicar. Cuesta ~4x en tiempo de reloj
    (los detalles pasan de ~2,5 s a ~8-12 s), y por eso las pasadas de
    listados y de detalles estan separadas: sobre esta rama, la de listados
    igual termina en unas horas y ya deja la skill funcionando.
    """

    def __init__(self, sesion, regulador: Regulador = None):
        self.sesion = sesion  # SesionSTJER ya abierta
        self.regulador = regulador or Regulador()

    def _leer_pagina(self, etiqueta: str) -> RespuestaCruda:
        t0 = time.monotonic()
        html = self.sesion.html()
        ms = int((time.monotonic() - t0) * 1000)
        if hay_captcha(html):
            raise ErrorCaptcha(f"{etiqueta}: el sitio pide verificacion")
        return RespuestaCruda(
            url=self.sesion.url(), estado=200, html=html, crudo=html, ms=ms
        )

    def buscar_listado(self, desde, hasta, fuero=None, pagina=1) -> RespuestaCruda:
        self.regulador.esperar()
        etiqueta = f"listado {desde:%Y-%m} p{pagina}"
        if self.sesion.hay_captcha():
            # El captcha puede reaparecer en el formulario de busqueda (no
            # solo en la pagina de resultados). Si no se detecta aca, el
            # intento de llenar fecha_desde/fecha_hasta falla con un error
            # generico de "campos no encontrados" que oculta la causa real.
            raise ErrorCaptcha(f"{etiqueta}: el sitio pide verificacion")
        self.sesion.completar_busqueda(desde, hasta, fuero=fuero, pagina=pagina)
        return self._leer_pagina(etiqueta)

    def abrir_detalle(self, ref: str, mes: str = None, pagina: int = None) -> RespuestaCruda:
        self.regulador.esperar()
        if mes and pagina:
            # ref es el onclick de una fila del listado: solo tiene sentido
            # con ese listado (mes+pagina) en pantalla, no en una sesion
            # recien abierta. Import diferido para evitar import circular
            # (cosecha.py ya importa este modulo).
            from .cosecha import rango_del_mes

            desde, hasta = rango_del_mes(mes)
            self.sesion.completar_busqueda(desde, hasta, pagina=pagina)
        self.sesion.abrir_detalle(ref)
        return self._leer_pagina(f"detalle {ref[:40]}")

    def arbol_tesauro(self, ref=None) -> RespuestaCruda:
        self.regulador.esperar()
        self.sesion.abrir_tesauro(ref)
        return self._leer_pagina(f"tesauro {ref or 'raiz'}")
