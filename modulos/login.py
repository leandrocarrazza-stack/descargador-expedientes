"""
MÓDULO 1: Gestión de Sesión con Selenium
=========================================

Usa Selenium directamente para hacer requests autenticadas.
Selenium maneja automáticamente todas las cookies del navegador.
"""

import requests
import json
import pickle
import os
from pathlib import Path
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# Nota: NO usamos webdriver_manager porque falla con versiones nuevas de Chrome.
# Selenium 4.6+ incluye "Selenium Manager" que descarga el driver correcto automáticamente.
import time
from modulos.logger import crear_logger

logger = crear_logger(__name__)


class ClienteSelenium:
    """Cliente que usa Selenium directamente para requests autenticadas."""

    def __init__(self, url_mesa_virtual=None):
        """
        Inicializa el cliente Selenium.

        Args:
            url_mesa_virtual: URL base de Mesa Virtual
        """
        self.url_mesa_virtual = url_mesa_virtual or "https://mesavirtual.jusentrerios.gov.ar/"
        self.driver = None
        self.timeout = 30
        self.archivo_sesion = Path.home() / ".mesa_virtual_sesion.pkl"

    def abrir_navegador_y_loguearse(self, timeout_segundos=600):
        """Abre el navegador y espera a que el usuario se loguee.

        Args:
            timeout_segundos: Máximo tiempo a esperar (default 10 min para 2FA)
        """
        try:
            options = webdriver.ChromeOptions()
            self.driver = webdriver.Chrome(
                # Sin Service() explícito: Selenium Manager elige el driver correcto automáticamente
                options=options
            )
            print("[NET] Abriendo navegador Chrome...")
            self.driver.get(self.url_mesa_virtual)

            print("\n[WAIT] Esperando que completes el login en el navegador...")
            print("   (El script esperará 5 minutos máximo)")
            print("   Deberías ver: Mesa Virtual cargando después del login\n")

            tiempo_esperado = 0
            intervalo_chequeo = 1
            urls_vistas = []

            while tiempo_esperado < timeout_segundos:
                url_actual = self.driver.current_url

                if url_actual not in urls_vistas:
                    urls_vistas.append(url_actual)
                    print(f"  > {url_actual[:70]}...")

                # Detectar cuando estamos en Mesa Virtual (no en Keycloak)
                if "mesavirtual.jusentrerios.gov.ar" in url_actual and \
                   "ol-sso" not in url_actual:
                    print(f"\n[OK] Login completado (en Mesa Virtual)\n")
                    # Esperar a que cargue completamente
                    time.sleep(3)
                    return True

                time.sleep(intervalo_chequeo)
                tiempo_esperado += intervalo_chequeo

            print("[TIMEOUT] Timeout: No se completó el login después de 5 minutos")
            return False

        except Exception as e:
            print(f"[ERROR] Error: {e}")
            return False

    def guardar_sesion(self):
        """Guarda las cookies de la sesión actual en un archivo."""
        if not self.driver:
            print("[WARN] No hay navegador para guardar sesión")
            return False
        try:
            cookies = self.driver.get_cookies()
            if not cookies:
                print("[WARN] No hay cookies para guardar")
                return False

            # Crear directorio si no existe
            self.archivo_sesion.parent.mkdir(parents=True, exist_ok=True)

            with open(self.archivo_sesion, 'wb') as f:
                pickle.dump(cookies, f)

            # Verificar que se guardó correctamente
            if self.archivo_sesion.exists():
                tamaño = self.archivo_sesion.stat().st_size
                print(f"[OK] Sesión guardada: {self.archivo_sesion} ({tamaño} bytes)")
                return True
            else:
                print("[WARN] No se pudo verificar que se guardó la sesión")
                return False
        except Exception as e:
            print(f"[WARN] No se pudo guardar sesión: {e}")
            import traceback
            traceback.print_exc()
            return False

    def cargar_sesion(self):
        """Carga las cookies guardadas en el navegador."""
        if not self.driver:
            print("[WARN] No hay navegador para cargar sesión")
            return False

        if not self.archivo_sesion.exists():
            print(f"[WARN] Archivo de sesión no encontrado: {self.archivo_sesion}")
            return False

        try:
            print(f"[DIR] Cargando sesión desde: {self.archivo_sesion}")

            # Navegar a la URL base primero
            self.driver.get(self.url_mesa_virtual)
            time.sleep(2)

            # Cargar las cookies
            with open(self.archivo_sesion, 'rb') as f:
                cookies = pickle.load(f)

            print(f"[COOKIES] Cargando {len(cookies)} cookie(s)...")

            cookies_cargadas = 0
            for cookie in cookies:
                try:
                    # Remover el campo 'expiry' si existe (puede causar problemas)
                    if 'expiry' in cookie:
                        del cookie['expiry']

                    self.driver.add_cookie(cookie)
                    cookies_cargadas += 1
                except Exception as e:
                    # Algunas cookies pueden no ser compatibles, continuar
                    pass

            print(f"[OK] {cookies_cargadas} cookie(s) cargada(s)")

            # Navegar de nuevo para aplicar las cookies
            self.driver.get(self.url_mesa_virtual)
            time.sleep(3)

            # Verificar que se cargó algo
            url_actual = self.driver.current_url
            print(f"[OK] URL actual: {url_actual[:80]}...")

            # Verificar que la sesión es válida
            # La sesión es válida solo si NO fuimos redirigidos a login/SSO
            if "ol-sso" in url_actual or "login" in url_actual or "keycloak" in url_actual.lower():
                print("[WARN] Sesión expirada - fuimos redirigidos a login")
                return False

            if "mesavirtual.jusentrerios.gov.ar" in url_actual:
                print("[OK] Sesión restaurada correctamente")
                return True

            # Si no estamos en login pero tampoco en la URL esperada, probablemente falló
            print(f"[WARN] URL inesperada después de cargar sesión: {url_actual[:80]}")
            return False

        except Exception as e:
            print(f"[WARN] No se pudo cargar sesión: {e}")
            import traceback
            traceback.print_exc()
            return False

    def sesion_existe(self):
        """Verifica si hay una sesión guardada."""
        return self.archivo_sesion.exists()

    def hacer_request_graphql(self, query, variables=None):
        """
        Hace un request GraphQL usando Selenium (que mantiene la sesión del navegador).

        Args:
            query: Query GraphQL
            variables: Variables de la query

        Retorna:
            dict: Respuesta JSON de la API
        """
        if not self.driver:
            raise Exception("Navegador no inicializado")

        try:
            # Crear un ID único para esta petición
            import uuid
            request_id = str(uuid.uuid4())

            # JavaScript que hace fetch y guarda el resultado en window
            setup_script = f"""
            window._graphql_requests = window._graphql_requests || {{}};
            window._graphql_requests['{request_id}'] = 'pending';

            fetch(arguments[0], {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                    'Accept': '*/*'
                }},
                body: JSON.stringify(arguments[1]),
                credentials: 'include'
            }})
            .then(response => {{
                if (!response.ok) {{
                    throw new Error('HTTP ' + response.status);
                }}
                return response.json();
            }})
            .then(data => {{
                window._graphql_requests['{request_id}'] = data;
            }})
            .catch(error => {{
                window._graphql_requests['{request_id}'] = {{error: error.message}};
            }});
            """

            # Ejecutar el fetch en el navegador
            self.driver.execute_script(
                setup_script,
                "https://mesavirtual.jusentrerios.gov.ar/api/graphql",
                {
                    "operationName": query.get("operationName"),
                    "variables": variables or {},
                    "query": query.get("query")
                }
            )

            # Esperar a que se complete la petición (máximo 30 segundos)
            import time
            start = time.time()
            while time.time() - start < 30:
                resultado = self.driver.execute_script(
                    f"return window._graphql_requests['{request_id}'];"
                )
                if resultado != 'pending':
                    # Limpiar
                    self.driver.execute_script(
                        f"delete window._graphql_requests['{request_id}'];"
                    )
                    return resultado if resultado else {"error": "No response"}
                time.sleep(0.5)

            # Timeout
            return {"error": "Request timeout"}

        except Exception as e:
            return {"error": str(e)}

    def cerrar(self):
        """Cierra el navegador."""
        if self.driver:
            self.driver.quit()


def _guardar_cookies_en_db(cookies, info=None):
    """
    Guarda las cookies de Mesa Virtual en la base de datos.
    Solo funciona dentro de un contexto Flask activo.

    Args:
        cookies: Lista de dicts con las cookies del navegador
        info: Texto opcional (ej: "subidas por admin el 2026-03-29")
    """
    try:
        import json
        from modulos.models import SesionMesaVirtual
        from modulos.database import db

        # Borrar sesiones anteriores e insertar la nueva
        SesionMesaVirtual.query.delete()
        nueva_sesion = SesionMesaVirtual(
            cookies_json=json.dumps(cookies),
            info=info or f"subidas el {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        db.session.add(nueva_sesion)
        db.session.commit()
        logger.info(f"[DB] Cookies guardadas en BD ({len(cookies)} cookies)")
        return True
    except Exception as e:
        logger.warning(f"[DB] No se pudieron guardar cookies en BD: {e}")
        return False


def _cargar_cookies_desde_db():
    """
    Carga las cookies de Mesa Virtual desde la base de datos.
    Solo funciona dentro de un contexto Flask activo.

    Returns:
        list: Lista de dicts con las cookies, o None si no hay sesión guardada
    """
    try:
        import json
        from modulos.models import SesionMesaVirtual

        sesion = SesionMesaVirtual.query.order_by(
            SesionMesaVirtual.actualizado_en.desc()
        ).first()

        if sesion:
            cookies = json.loads(sesion.cookies_json)
            logger.info(f"[DB] {len(cookies)} cookies cargadas desde BD (subidas: {sesion.actualizado_en})")
            return cookies
        else:
            logger.info("[DB] No hay cookies guardadas en BD")
            return None
    except Exception as e:
        logger.warning(f"[DB] No se pudieron cargar cookies desde BD: {e}")
        return None


def _inyectar_cookies_en_driver(driver, cookies, url_base):
    """
    Inyecta una lista de cookies en un webdriver de Selenium.

    Args:
        driver: WebDriver de Selenium
        cookies: Lista de dicts con las cookies
        url_base: URL del dominio (para que las cookies apliquen)

    Returns:
        bool: True si las cookies se inyectaron y la sesión es válida
    """
    try:
        # Navegar al dominio primero (requerido por Selenium para agregar cookies)
        driver.get(url_base)
        import time
        time.sleep(2)

        # Inyectar cada cookie
        cargadas = 0
        for cookie in cookies:
            try:
                # Selenium no acepta el campo 'expiry' en algunos casos
                cookie_limpia = {k: v for k, v in cookie.items() if k != 'expiry'}
                driver.add_cookie(cookie_limpia)
                cargadas += 1
            except Exception:
                pass

        logger.info(f"[COOKIES] {cargadas}/{len(cookies)} cookies inyectadas")

        # Recargar para que las cookies tomen efecto
        driver.get(url_base)
        time.sleep(3)

        # Verificar que la sesión sea válida (no redirigió a login)
        url_actual = driver.current_url
        if "ol-sso" in url_actual or "login" in url_actual or "keycloak" in url_actual.lower():
            logger.warning("[COOKIES] Sesión expirada - redirigió a login")
            return False

        if "mesavirtual.jusentrerios.gov.ar" in url_actual:
            logger.info("[COOKIES] Sesión válida")
            return True

        logger.warning(f"[COOKIES] URL inesperada: {url_actual[:80]}")
        return False

    except Exception as e:
        logger.warning(f"[COOKIES] Error inyectando cookies: {e}")
        return False


def crear_cliente_sesion(carpeta_cookies=None, api_graphql_url=None, url_mesa_virtual=None, usar_sesion_guardada=True, headless=True):
    """
    Crea un cliente Selenium para acceso autenticado.

    Usa Selenium directamente - sin necesidad de guardar/cargar cookies manualmente.
    Selenium maneja automáticamente todas las cookies del navegador.

    Intenta reutilizar sesión guardada si existe, sino requiere login manual.

    Args:
        carpeta_cookies: (no se usa ahora, para compatibilidad)
        api_graphql_url: (no se usa ahora, para compatibilidad)
        url_mesa_virtual: URL de Mesa Virtual
        usar_sesion_guardada: Si True, intenta cargar sesión anterior
        headless: Si True, usa modo headless (sin interfaz visible)

    Retorna:
        ClienteSelenium: Cliente listo para usar

    Lanza:
        Exception: Si no se puede crear sesión
    """
    if url_mesa_virtual is None:
        url_mesa_virtual = "https://mesavirtual.jusentrerios.gov.ar/"

    print("\n" + "=" * 70)
    print("  [AUTH] INICIANDO SESION EN MESA VIRTUAL")
    print("=" * 70)

    cliente = ClienteSelenium(url_mesa_virtual)

    # Opciones de Chrome (headless para servidor)
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')

    sesion_cargada = False

    # ── OPCIÓN 1: Archivo de cookies local (para desarrollo en tu PC) ──────
    if usar_sesion_guardada and cliente.sesion_existe():
        print("\n[ARCHIVO] Sesión local detectada, intentando...")
        try:
            cliente.driver = webdriver.Chrome(
                # Sin Service() explícito: Selenium Manager elige el driver correcto automáticamente
                options=options
            )
            if cliente.cargar_sesion():
                sesion_cargada = True
                print("[OK] Sesión desde archivo local\n")
            else:
                print("[WARN] Archivo de sesión expirado")
                if cliente.driver:
                    try:
                        cliente.driver.quit()
                    except:
                        pass
                cliente.driver = None
        except Exception as e:
            print(f"[WARN] Error cargando sesión local: {e}")
            if cliente.driver:
                try:
                    cliente.driver.quit()
                except:
                    pass
            cliente.driver = None

    # ── OPCIÓN 2: Cookies en BD (para servidor Render con 2FA) ────────────
    if not sesion_cargada:
        print("\n[DB] Buscando cookies en base de datos...")
        cookies_db = _cargar_cookies_desde_db()
        if cookies_db:
            try:
                cliente.driver = webdriver.Chrome(
                    # Sin Service() explícito: Selenium Manager elige el driver correcto automáticamente
                    options=options
                )
                if _inyectar_cookies_en_driver(cliente.driver, cookies_db, url_mesa_virtual):
                    sesion_cargada = True
                    print("[OK] Sesión desde BD (cookies subidas por admin)\n")
                else:
                    print("[WARN] Cookies de BD expiradas")
                    if cliente.driver:
                        try:
                            cliente.driver.quit()
                        except:
                            pass
                    cliente.driver = None
            except Exception as e:
                print(f"[WARN] Error usando cookies de BD: {e}")
                if cliente.driver:
                    try:
                        cliente.driver.quit()
                    except:
                        pass
                cliente.driver = None

    # ── OPCIÓN 3: Login manual (solo para uso local, requiere pantalla) ────
    if not sesion_cargada:
        # En servidor Render no hay pantalla ni usuario → falla con mensaje claro
        import os
        en_servidor = os.getenv('FLASK_ENV') == 'production' or os.getenv('RENDER')
        if en_servidor:
            raise Exception(
                "Sesión de Mesa Virtual no disponible. "
                "Ejecutá 'python subir_cookies.py' en tu PC para subir las cookies al servidor."
            )

        print("\n[NET] Requiere login manual...")
        print("[NET] Abriendo navegador Chrome (visible para 2FA)...\n")

        if not cliente.abrir_navegador_y_loguearse():
            raise Exception("[ERROR] No se completó el login a tiempo")

        # Guardar sesión localmente para próximas veces
        print("\n[SAVE] Guardando sesión para próximas descargas...")
        if cliente.guardar_sesion():
            print("[OK] Sesión guardada localmente\n")
        else:
            print("[WARN] No se pudo guardar sesión\n")

    # Verificar que la sesión funciona con un request simple
    test_query = {
        "operationName": "cantidadNotificacionesNoLeidasBusquedaAvanzada",
        "query": "query cantidadNotificacionesNoLeidasBusquedaAvanzada { cantidadNotificacionesNoLeidasBusquedaAvanzada { count } }"
    }

    resultado = cliente.hacer_request_graphql(test_query)

    # Verificar si la respuesta es válida
    if resultado and isinstance(resultado, dict):
        if "error" in resultado:
            # Contiene error - pero el login fue exitoso, continuamos de todas formas
            print(f"[WARN] Nota: {resultado.get('error')}")
            print("[OK] Login completado, continuando...\n")
            return cliente
        elif "data" in resultado:
            # Respuesta válida
            print("[OK] Sesión verificada correctamente\n")
            return cliente

    # Si llegamos aquí, respuesta inesperada pero continuamos
    print("[OK] Login completado, continuando...\n")
    return cliente


if __name__ == "__main__":
    # Prueba rápida
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from config import API_GRAPHQL

    try:
        cliente = crear_cliente_sesion(api_graphql_url=API_GRAPHQL)
        print(" Sesión válida")
    except Exception as e:
        print(f"Información:\n{e}")
