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
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
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
                service=Service(ChromeDriverManager().install()),
                options=options
            )
            print("[NET] Abriendo navegador Chrome...")
            self.driver.get(self.url_mesa_virtual)

            minutos_max = timeout_segundos // 60
            print("\n[WAIT] Esperando que completes el login en el navegador...")
            print(f"       (El script esperará {minutos_max} minutos máximo)")
            print("       Deberías ver: Mesa Virtual cargando después del login\n")
            logger.info(f"Esperando login manual (timeout: {minutos_max} min)")

            tiempo_esperado = 0
            intervalo_chequeo = 1
            urls_vistas = []

            while tiempo_esperado < timeout_segundos:
                url_actual = self.driver.current_url

                if url_actual not in urls_vistas:
                    urls_vistas.append(url_actual)
                    print(f"  → {url_actual[:70]}...")

                # Detectar cuando estamos en Mesa Virtual (no en Keycloak)
                if "mesavirtual.jusentrerios.gov.ar" in url_actual and \
                   "ol-sso" not in url_actual:
                    print(f"\n[OK] Login completado (en Mesa Virtual)\n")
                    # Esperar a que cargue completamente
                    time.sleep(3)
                    return True

                time.sleep(intervalo_chequeo)
                tiempo_esperado += intervalo_chequeo

            print(f"[TIMEOUT] No se completó el login después de {minutos_max} minutos")
            logger.error(f"Login timeout después de {minutos_max} minutos")
            return False

        except Exception as e:
            print(f"[ERROR] {e}")
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
            logger.warning("No hay navegador para cargar sesión")
            return False

        if not self.archivo_sesion.exists():
            print(f"[WARN] Archivo de sesión no encontrado: {self.archivo_sesion}")
            logger.warning(f"Archivo de sesión no encontrado: {self.archivo_sesion}")
            return False

        try:
            # Calcular edad de la sesión guardada
            mod_time = os.path.getmtime(self.archivo_sesion)
            edad_segundos = time.time() - mod_time
            edad_horas = edad_segundos / 3600

            print(f"[FILE] Cargando sesión desde: {self.archivo_sesion}")
            logger.info(f"Cargando sesión guardada ({edad_horas:.1f}h de antigüedad)")

            # Navegar a la URL base primero
            self.driver.get(self.url_mesa_virtual)
            time.sleep(2)

            # Cargar las cookies
            with open(self.archivo_sesion, 'rb') as f:
                cookies = pickle.load(f)

            print(f"[COOKIE] Cargando {len(cookies)} cookie(s)...")

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
                logger.error(f"Sesión expirada (redirigido a login). URL: {url_actual[:80]}")
                return False

            if "mesavirtual.jusentrerios.gov.ar" in url_actual:
                print("[OK] Sesión restaurada correctamente")
                logger.info("Sesión restaurada exitosamente")
                return True

            # Si no estamos en login pero tampoco en la URL esperada, probablemente falló
            print(f"[WARN] URL inesperada después de cargar sesión: {url_actual[:80]}")
            logger.warning(f"URL inesperada después de cargar sesión: {url_actual[:80]}")
            return False

        except Exception as e:
            print(f"[WARN] No se pudo cargar sesión: {e}")
            logger.error(f"Error al cargar sesión: {e}")
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


def limpiar_sesion_si_vieja(archivo_sesion, max_edad_horas=24):
    """Elimina sesión guardada si es muy vieja.

    Args:
        archivo_sesion: Ruta al archivo pickle de sesión
        max_edad_horas: Edad máxima permitida en horas
    """
    if not os.path.exists(archivo_sesion):
        return

    try:
        mod_time = os.path.getmtime(archivo_sesion)
        edad_segundos = time.time() - mod_time
        edad_horas = edad_segundos / 3600

        if edad_segundos > max_edad_horas * 3600:
            os.remove(archivo_sesion)
            logger.info(f"Sesión eliminada (más de {max_edad_horas}h de antigüedad)")
            print(f"[CLEAN] Sesión guardada eliminada (más de {max_edad_horas}h)")
    except Exception as e:
        logger.warning(f"Error al limpiar sesión vieja: {e}")


def crear_cliente_sesion(carpeta_cookies=None, api_graphql_url=None, url_mesa_virtual=None, usar_sesion_guardada=True):
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

    Retorna:
        ClienteSelenium: Cliente listo para usar

    Lanza:
        Exception: Si no se puede crear sesión
    """
    if url_mesa_virtual is None:
        url_mesa_virtual = "https://mesavirtual.jusentrerios.gov.ar/"

    print("\n" + "═" * 70)
    print("  [!] SESIÓN REQUERIDA")
    print("═" * 70)

    cliente = ClienteSelenium(url_mesa_virtual)

    # Intentar cargar sesión guardada primero
    sesion_cargada = False
    if usar_sesion_guardada:
        # Limpiar sesión si es muy vieja
        limpiar_sesion_si_vieja(cliente.archivo_sesion, max_edad_horas=24)

        if cliente.sesion_existe():
            print("\n[OK] Sesión guardada detectada, intentando cargar...")
            try:
                # Crear navegador en headless mode (sin interfaz visible)
                options = webdriver.ChromeOptions()
                options.add_argument('--headless')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--disable-blink-features=AutomationControlled')

                cliente.driver = webdriver.Chrome(
                    service=Service(ChromeDriverManager().install()),
                    options=options
                )
                print("[SILENT] Navegador en modo silencioso (headless)...")

                sesion_cargada = cliente.cargar_sesion()

                if sesion_cargada:
                    print("[OK] Usando sesión guardada (sin navegador visible)\n")
                else:
                    # Cerrar navegador y hacer login manual
                    print("[WARN] Sesión expirada, se requiere nuevo login")
                    if cliente.driver:
                        cliente.driver.quit()
                    cliente.driver = None

            except Exception as e:
                print(f"[WARN] No se pudo usar sesión guardada: {e}")
                logger.warning(f"Error al usar sesión guardada: {e}")
                if cliente.driver:
                    try:
                        cliente.driver.quit()
                    except:
                        pass
                cliente.driver = None
                sesion_cargada = False

    # Si no se cargó sesión, hacer login manual
    if not sesion_cargada:
        print("\n[NET] Requiere login manual...")
        print("[NET] Abriendo navegador Chrome (visible para 2FA)...\n")

        if not cliente.abrir_navegador_y_loguearse():
            raise Exception("[ERROR] No se completó el login a tiempo")

        # Guardar la sesión para próximas veces
        print("\n[SAVE] Guardando sesión para próximas descargas...")
        if cliente.guardar_sesion():
            print("[OK] Sesión guardada - próximas descargas serán silenciosas\n")
        else:
            print("[WARN] No se pudo guardar sesión\n")

    # Si llegamos aquí, el cliente está conectado a Mesa Virtual
    # La verificación de autenticación ya se hizo en cargar_sesion()
    # No hacemos validación GraphQL adicional para evitar loops infinitos
    logger.info("Sesión autenticada - Cliente listo para usar")
    print("[OK] Sesión autenticada\n")
    return cliente


if __name__ == "__main__":
    # Prueba rápida
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from config import API_GRAPHQL

    try:
        cliente = crear_cliente_sesion(api_graphql_url=API_GRAPHQL)
        print("[OK] Sesión válida")
    except Exception as e:
        print(f"Información:\n{e}")
