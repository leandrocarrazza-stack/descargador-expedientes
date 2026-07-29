"""
Sesion de navegador contra el buscador STJER
============================================

Su unica razon de existir es el captcha: aparece una vez, y una vez resuelto
la sesion queda desbloqueada. Entonces:

    1. Se abre un navegador real y se resuelve el captcha UNA vez.
    2. Se exportan las cookies con `cookies_para_requests()`.
    3. Toda la cosecha corre sobre requests, sin navegador.

Eso deja lo mejor de los dos mundos: la robustez del navegador donde hace
falta, y la velocidad de HTTP donde importa.

Sobre resolver el captcha
-------------------------
Se resuelve **a mano**, y esta bien que asi sea. Es UN captcha por corrida de
varias horas: montar y tunear un OCR contra glifos distorsionados
desconocidos costaria un dia de trabajo para conseguir algo cercano a un
volado de moneda. `ResolvedorCaptcha` es un Protocol, asi que si alguna vez
la medicion lo justifica se enchufa un solver pago sin tocar nada mas.

Lo que si se arregla aca es el problema real que tenia la skill vieja: la
imagen se toma del `<img>` **a resolucion nativa**, no recortada de un
screenshot de pagina completa donde el texto queda ilegible.
"""

import json
import logging
import os
import platform
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from . import ajustes
from .parser import extraer_token_ah, hay_captcha

logger = logging.getLogger(__name__)

# PHP usa session.gc_maxlifetime = 1440 s por defecto. Se guarda el estado con
# margen: durante una cosecha activa no hay inactividad, asi que en la
# practica es un captcha por corrida.
VIDA_ESTADO = timedelta(hours=12)


class ErrorSesion(Exception):
    """No se pudo abrir o sostener la sesion."""


# ═══════════════════════════════════════════════════════════════════════════
#  Resolvedores de captcha
# ═══════════════════════════════════════════════════════════════════════════

class ResolvedorCaptcha(Protocol):
    def resolver(self, png: bytes) -> str: ...


class ResolvedorManual:
    """
    Guarda el PNG, lo abre con el visor del sistema y lo pide por consola.

    Es el default. Simple, sin dependencias y 100% confiable.
    """

    def __init__(self, destino=None, abrir_visor: bool = True):
        self.destino = Path(destino or (ajustes.DESCUBRIMIENTO_DIR / "captcha.png"))
        self.abrir_visor = abrir_visor

    def resolver(self, png: bytes) -> str:
        self.destino.parent.mkdir(parents=True, exist_ok=True)
        self.destino.write_bytes(png)

        if self.abrir_visor:
            self._abrir(self.destino)

        print(f"\n  Captcha guardado en: {self.destino}")
        print("  Miralo y escribi lo que dice (Enter vacio para reintentar).")
        try:
            return input("  Codigo: ").strip()
        except EOFError:
            # Sin terminal interactiva (cron, CI): no se puede resolver aca.
            raise ErrorSesion(
                f"No hay consola interactiva para resolver el captcha.\n"
                f"La imagen quedo en {self.destino}. Opciones:\n"
                f"  - Correr el comando a mano en una terminal.\n"
                f"  - Usar ResolvedorArchivo y escribir el codigo en un archivo."
            )

    @staticmethod
    def _abrir(ruta: Path) -> None:
        """Abre la imagen con el visor por defecto del sistema."""
        try:
            if platform.system() == "Windows":
                os.startfile(str(ruta))  # noqa: S606
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(ruta)], check=False)
            else:
                subprocess.run(["xdg-open", str(ruta)], check=False)
        except Exception as e:  # el visor es una comodidad, no un requisito
            logger.debug("No se pudo abrir el visor: %s", e)


class ResolvedorArchivo:
    """
    Escribe el PNG y espera a que aparezca el codigo en un archivo de texto.

    Sirve para desbloquear una corrida desatendida desde otra maquina: se
    mira la imagen y se escribe el codigo en `codigo.txt`.
    """

    def __init__(self, dir_trabajo=None, timeout_seg: int = 900, intervalo: int = 5):
        self.dir = Path(dir_trabajo or ajustes.DESCUBRIMIENTO_DIR)
        self.png = self.dir / "captcha.png"
        self.txt = self.dir / "captcha_codigo.txt"
        self.timeout_seg = timeout_seg
        self.intervalo = intervalo

    def resolver(self, png: bytes) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.png.write_bytes(png)
        if self.txt.exists():
            self.txt.unlink()

        logger.warning(
            "Esperando el codigo del captcha. Mira %s y escribi el texto en %s",
            self.png, self.txt,
        )
        limite = time.monotonic() + self.timeout_seg
        while time.monotonic() < limite:
            if self.txt.exists():
                codigo = self.txt.read_text(encoding="utf-8").strip()
                if codigo:
                    self.txt.unlink()
                    return codigo
            time.sleep(self.intervalo)

        raise ErrorSesion(
            f"Nadie escribio el codigo en {self.txt} en {self.timeout_seg} s"
        )


class ResolvedorFijo:
    """Devuelve siempre el mismo codigo. Solo para tests."""

    def __init__(self, codigo: str):
        self.codigo = codigo
        self.llamadas = 0

    def resolver(self, png: bytes) -> str:
        self.llamadas += 1
        return self.codigo


# ═══════════════════════════════════════════════════════════════════════════
#  Selectores del formulario
# ═══════════════════════════════════════════════════════════════════════════

# Listas de candidatos: se prueba en orden hasta que uno exista. Es lo que
# permite que esto tenga alguna chance de andar antes de ver el HTML real, y
# ajustarlo despues es agregar un selector a la lista.
SELECTORES = {
    "captcha_img": [
        "img[src*='captcha']", "img[alt*='captcha' i]",
        "img[src*='verifica']", "#captcha img", ".captcha img",
    ],
    "captcha_input": [
        "input[name*='captcha' i]", "input[name*='verifica' i]",
        "input[name*='codigo' i]", "form input[type='text']",
    ],
    "captcha_boton": [
        "input[type='submit'][value*='Aceptar' i]",
        "button:has-text('Aceptar')", "input[value*='Aceptar' i]",
        "button[type='submit']", "input[type='submit']",
    ],
    "fecha_desde": [
        "input[name*='fecha_desde' i]", "input[name*='desde' i]",
        "input[id*='desde' i]",
    ],
    "fecha_hasta": [
        "input[name*='fecha_hasta' i]", "input[name*='hasta' i]",
        "input[id*='hasta' i]",
    ],
    "fuero": ["select[name*='fuero' i]", "select[id*='fuero' i]"],
    "boton_buscar": [
        "input[value*='Buscar' i]", "button:has-text('Buscar')",
        "a:has-text('Buscar')",
    ],
    "siguiente": [
        "a:has-text('Siguiente')", "input[value*='Siguiente' i]",
        "a[title*='Siguiente' i]",
    ],
    "panel_tesauro": [
        "a:has-text('Tesauro')", "button:has-text('Tesauro')",
        "*:has-text('Buscar voces en el Tesauro')",
    ],
}


def cargar_selectores(ruta=None) -> dict:
    """Overrides desde descubrimiento/selectores.json (Fase 0)."""
    if ruta is None:
        ruta = ajustes.DESCUBRIMIENTO_DIR / "selectores.json"
    ruta = Path(ruta)
    sel = {k: list(v) for k, v in SELECTORES.items()}
    if not ruta.exists():
        return sel
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("No se pudo leer %s: %s", ruta, e)
        return sel
    for clave, valor in datos.items():
        if isinstance(valor, str):
            valor = [valor]
        if isinstance(valor, list):
            # Los de la Fase 0 van primero: son los que se vieron de verdad.
            sel[clave] = valor + [s for s in sel.get(clave, []) if s not in valor]
    logger.info("Selectores cargados desde %s", ruta)
    return sel


# ═══════════════════════════════════════════════════════════════════════════
#  Sesion
# ═══════════════════════════════════════════════════════════════════════════

class SesionSTJER:
    """
    Navegador Playwright contra el buscador, con estado persistido.

    Se usa como context manager:

        with SesionSTJER() as s:
            s.abrir()
            cookies = s.cookies_para_requests()
    """

    def __init__(
        self,
        ruta_estado=None,
        headless: bool = True,
        resolvedor: ResolvedorCaptcha = None,
        har=None,
        selectores: dict = None,
        timeout_ms: int = 60000,
    ):
        self.ruta_estado = Path(ruta_estado or ajustes.ESTADO_PATH)
        self.headless = headless
        self.resolvedor = resolvedor or ResolvedorManual()
        self.har = Path(har) if har else None
        self.selectores = selectores or cargar_selectores()
        self.timeout_ms = timeout_ms

        self._pw = None
        self._navegador = None
        self._contexto = None
        self._pagina = None

    # ── ciclo de vida ─────────────────────────────────────────────────────

    def __enter__(self) -> "SesionSTJER":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ErrorSesion(
                "Falta Playwright. Instalalo con:\n"
                "    pip install playwright && playwright install chromium\n"
                "Solo hace falta para abrir la sesion y resolver el captcha; "
                "la busqueda local no lo necesita."
            )

        self._pw = sync_playwright().start()
        self._navegador = self._pw.chromium.launch(headless=self.headless)

        opciones = {"user_agent": ajustes.UA, "locale": "es-AR"}
        if self._estado_vigente():
            opciones["storage_state"] = str(self.ruta_estado)
            logger.info("Reusando la sesion guardada en %s", self.ruta_estado)
        if self.har:
            self.har.parent.mkdir(parents=True, exist_ok=True)
            opciones["record_har_path"] = str(self.har)
            opciones["record_har_content"] = "embed"

        self._contexto = self._navegador.new_context(**opciones)
        self._contexto.set_default_timeout(self.timeout_ms)
        self._pagina = self._contexto.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self._contexto:
                self.guardar_estado()
                self._contexto.close()
        finally:
            if self._navegador:
                self._navegador.close()
            if self._pw:
                self._pw.stop()
        return False

    def _estado_vigente(self) -> bool:
        """True si hay estado guardado y todavia vale la pena reusarlo."""
        if not self.ruta_estado.exists():
            return False
        edad = datetime.now(timezone.utc) - datetime.fromtimestamp(
            self.ruta_estado.stat().st_mtime, timezone.utc
        )
        if edad > VIDA_ESTADO:
            logger.info("La sesion guardada tiene %s: se descarta", edad)
            return False
        return True

    def guardar_estado(self) -> None:
        if not self._contexto:
            return
        self.ruta_estado.parent.mkdir(parents=True, exist_ok=True)
        self._contexto.storage_state(path=str(self.ruta_estado))
        # Tiene cookies de sesion: que no la lea cualquiera.
        try:
            os.chmod(self.ruta_estado, 0o600)
        except OSError:
            pass

    def _guardar_diagnostico(self, motivo: str) -> None:
        """
        Vuelca el HTML actual a un archivo, para poder diagnosticar sin tener
        que repetir todo el flujo del captcha.
        """
        ruta = ajustes.DESCUBRIMIENTO_DIR / f"diagnostico_{motivo}.html"
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(self.html(), encoding="utf-8")
            logger.warning("HTML de diagnostico guardado en %s", ruta)
        except Exception as e:
            logger.debug("No se pudo guardar el diagnostico: %s", e)

    # ── helpers de pagina ─────────────────────────────────────────────────

    @property
    def pagina(self):
        if self._pagina is None:
            raise ErrorSesion("La sesion no esta abierta (falta el `with`)")
        return self._pagina

    def url(self) -> str:
        return self.pagina.url

    def html(self) -> str:
        return self.pagina.content()

    def _primero(self, clave: str, timeout: int = 3000):
        """Primer selector de la lista que exista en la pagina."""
        for selector in self.selectores.get(clave, []):
            try:
                loc = self.pagina.locator(selector).first
                loc.wait_for(state="attached", timeout=timeout)
                return loc
            except Exception:
                continue
        return None

    def esperar_quieto(self, ms: int = 1500) -> None:
        """
        Espera a que la app deje de moverse.

        Toba muestra un modal "Procesando. Por favor aguarde..." y recien
        despues pinta la tabla; salir antes es leer la pantalla vieja.
        """
        try:
            self.pagina.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except Exception:
            pass
        self.pagina.wait_for_timeout(ms)

    # ── captcha ───────────────────────────────────────────────────────────

    def abrir(self) -> None:
        """Entra al buscador y resuelve el captcha si aparece."""
        self.pagina.goto(ajustes.URL_INICIO, wait_until="domcontentloaded")
        self.esperar_quieto()

        if self.hay_captcha():
            logger.info("Apareció el captcha: hay que resolverlo una vez")
            if not self.resolver_captcha():
                raise ErrorSesion("No se pudo pasar el captcha")
        else:
            logger.info("Sin captcha: la sesion guardada seguia viva")

        self.guardar_estado()

    def hay_captcha(self) -> bool:
        if self._primero("captcha_img", timeout=1500) is not None:
            return True
        return hay_captcha(self.html())

    def imagen_captcha(self) -> bytes:
        """
        PNG del captcha a RESOLUCION NATIVA.

        Esto es lo que arregla el problema de la skill vieja: sacaba un
        screenshot de la pagina entera, donde la imagen quedaba a una fraccion
        de su tamaño y era casi ilegible. `locator.screenshot()` captura el
        elemento en su tamaño real.
        """
        img = self._primero("captcha_img")
        if img is None:
            raise ErrorSesion("No se encontro la imagen del captcha")

        src = img.get_attribute("src") or ""
        if src and not src.startswith("data:"):
            # Mejor todavia: pedir el archivo original al servidor. Sale sin
            # reescalado ni antialiasing del render.
            try:
                url = src if src.startswith("http") else ajustes.BASE_URL + src.lstrip("/")
                resp = self.pagina.request.get(url)
                if resp.ok:
                    cuerpo = resp.body()
                    if cuerpo:
                        return cuerpo
            except Exception as e:
                logger.debug("No se pudo bajar el captcha directo: %s", e)

        return img.screenshot()

    def resolver_captcha(self, intentos: int = 5) -> bool:
        """
        Resuelve el captcha, reintentando.

        Reintentar es barato: un codigo errado solo hace que se dibuje otro.
        """
        for intento in range(1, intentos + 1):
            try:
                png = self.imagen_captcha()
            except ErrorSesion:
                # No hay imagen para reintentar. Lo mas probable es que el
                # intento anterior en realidad SI funciono (la pagina navego
                # a la busqueda real) y `hay_captcha()` disparo por error en
                # el chequeo previo. Se vuelve a preguntar con calma antes de
                # darlo por fallido.
                if not self.hay_captcha():
                    logger.info(
                        "No hay una imagen nueva para reintentar, pero la "
                        "pagina ya no pide verificacion: se da por resuelto."
                    )
                    self.guardar_estado()
                    return True
                self._guardar_diagnostico("sin_imagen_pero_pide_captcha")
                raise

            codigo = self.resolvedor.resolver(png)
            if not codigo:
                logger.info("Codigo vacio: se pide otro captcha (%d/%d)", intento, intentos)
                self.pagina.reload(wait_until="domcontentloaded")
                self.esperar_quieto()
                continue

            campo = self._primero("captcha_input")
            if campo is None:
                raise ErrorSesion("No se encontro el campo del captcha")
            campo.fill(codigo)

            boton = self._primero("captcha_boton")
            if boton is not None:
                boton.click()
            else:
                campo.press("Enter")
            self.esperar_quieto(2500)

            if not self.hay_captcha():
                logger.info("Captcha resuelto en el intento %d", intento)
                self.guardar_estado()
                return True

            logger.warning("Captcha rechazado (%d/%d)", intento, intentos)

        return False

    # ── puente a requests ─────────────────────────────────────────────────

    def cookies_para_requests(self) -> dict:
        """
        Cookies de la sesion como dict, para pasarselas a ClienteHTTP.

        Este metodo es la bisagra de todo el diseño: resolves el captcha una
        vez en un navegador de verdad, y despues corres la cosecha entera
        sobre requests, que es 4x mas rapido.
        """
        if not self._contexto:
            raise ErrorSesion("La sesion no esta abierta")
        return {c["name"]: c["value"] for c in self._contexto.cookies()}

    def token_ah(self):
        """Hash de sesion de Toba, si aparece en la pagina actual."""
        return extraer_token_ah(self.html()) or extraer_token_ah(self.url())

    def exportar_credenciales(self, destino=None) -> Path:
        """
        Guarda cookies + token en un JSON para que la cosecha arranque sin
        volver a abrir el navegador.
        """
        destino = Path(destino or (ajustes.JURISPRUDENCIA_DIR / ".stjer_credenciales.json"))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(
                {
                    "cookies": self.cookies_para_requests(),
                    "ah": self.token_ah(),
                    "guardado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(destino, 0o600)
        except OSError:
            pass
        return destino

    # ── operaciones para la rama C ────────────────────────────────────────

    def completar_busqueda(self, desde, hasta, fuero=None, pagina=1) -> None:
        """Llena el formulario y busca. Solo lo usa ClienteNavegador."""
        campo_desde = self._primero("fecha_desde")
        campo_hasta = self._primero("fecha_hasta")
        if campo_desde is None or campo_hasta is None:
            raise ErrorSesion(
                "No se encontraron los campos de fecha. Ajusta los selectores "
                "en data/jurisprudencia/descubrimiento/selectores.json"
            )
        campo_desde.fill(desde.strftime("%d/%m/%Y"))
        campo_hasta.fill(hasta.strftime("%d/%m/%Y"))

        if fuero:
            sel = self._primero("fuero")
            if sel is not None:
                try:
                    sel.select_option(label=fuero)
                except Exception:
                    logger.warning("No se pudo seleccionar el fuero %r", fuero)

        boton = self._primero("boton_buscar")
        if boton is None:
            raise ErrorSesion("No se encontro el boton Buscar")
        boton.click()
        self.esperar_quieto(3000)

        # Paginar hasta la pagina pedida.
        for _ in range(max(0, pagina - 1)):
            siguiente = self._primero("siguiente")
            if siguiente is None:
                break
            siguiente.click()
            self.esperar_quieto(3000)

    def abrir_detalle(self, ref: str) -> None:
        """
        Abre el detalle de un fallo.

        Las filas no son <a href> normales, asi que primero se intenta
        ejecutar el handler que trae la referencia y recien despues se cae a
        buscar la fila por texto.
        """
        if ref and ("(" in ref or "javascript" in ref.lower()):
            try:
                self.pagina.evaluate(ref.replace("javascript:", ""))
                self.esperar_quieto(2500)
                return
            except Exception as e:
                logger.debug("No se pudo ejecutar la referencia %r: %s", ref, e)

        fila = self.pagina.locator(f"tr:has-text({ref!r})").first
        fila.click()
        self.esperar_quieto(2500)

    def abrir_tesauro(self, ref=None) -> None:
        """Abre el panel del tesauro (o expande un nodo)."""
        if ref:
            try:
                self.pagina.evaluate(ref.replace("javascript:", ""))
                self.esperar_quieto(1500)
                return
            except Exception:
                pass
        panel = self._primero("panel_tesauro")
        if panel is not None:
            panel.click()
            self.esperar_quieto(2000)


def cargar_credenciales(ruta=None) -> dict:
    """
    Lee las credenciales exportadas por `exportar_credenciales`.

    Devuelve {"cookies": {...}, "ah": "..."} o {} si no hay nada guardado.
    """
    ruta = Path(ruta or (ajustes.JURISPRUDENCIA_DIR / ".stjer_credenciales.json"))
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("No se pudieron leer las credenciales: %s", e)
        return {}
