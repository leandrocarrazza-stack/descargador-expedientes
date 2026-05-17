"""
renovar_sesion.py
=================
Script para renovar la sesión de Mesa Virtual manualmente.

Uso:
    python renovar_sesion.py

Qué hace:
    1. Abre Chrome visible en tu pantalla
    2. Navega a Mesa Virtual
    3. Espera hasta 10 minutos a que completes el login
    4. Captura TODAS las cookies de TODOS los dominios (Mesa Virtual + Keycloak)
    5. Guarda las cookies en ~/.mesa_virtual_sesion.pkl
    6. Muestra la ruta del archivo para que lo subas al panel admin de Render

Por qué capturar cookies de todos los dominios:
    Mesa Virtual usa Keycloak SSO (ol-sso.jusentrerios.gov.ar).
    Sin las cookies de Keycloak, el servidor no puede autenticarse aunque
    tenga las cookies de Mesa Virtual.
"""

import sys
import time
import pickle
from pathlib import Path

# Agregar el proyecto al path para poder importar los módulos
sys.path.insert(0, str(Path(__file__).parent))

from selenium import webdriver
# Selenium 4.6+ incluye Selenium Manager: no necesitamos webdriver_manager.

URL_MESA_VIRTUAL = "https://mesavirtual.jusentrerios.gov.ar/"
ARCHIVO_SESION = Path.home() / ".mesa_virtual_sesion.pkl"
TIMEOUT_SEGUNDOS = 600  # 10 minutos


def renovar_sesion():
    print("=" * 60)
    print("  RENOVAR SESIÓN DE MESA VIRTUAL")
    print("=" * 60)
    print()
    print("Abriendo Chrome...")
    print("→ Cuando se abra el navegador, completá el login normalmente.")
    print(f"→ Tenés {TIMEOUT_SEGUNDOS // 60} minutos.")
    print()

    # Abrir Chrome VISIBLE (sin headless)
    options = webdriver.ChromeOptions()
    options.add_argument('--window-size=1200,800')

    driver = webdriver.Chrome(options=options)  # Selenium Manager elige el driver automáticamente

    try:
        # Habilitar CDP Network para poder acceder a todas las cookies
        driver.execute_cdp_cmd('Network.enable', {})

        driver.get(URL_MESA_VIRTUAL)

        tiempo_esperado = 0
        intervalo = 1
        urls_vistas = []

        while tiempo_esperado < TIMEOUT_SEGUNDOS:
            url_actual = driver.current_url

            # Mostrar cada URL nueva que aparezca (para seguimiento)
            if url_actual not in urls_vistas:
                urls_vistas.append(url_actual)
                print(f"  > {url_actual[:80]}")

            # Detectar cuando estamos en Mesa Virtual (login exitoso)
            if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                    "ol-sso" not in url_actual and
                    "login" not in url_actual):

                print()
                print("✓ Login detectado. Esperando 5 segundos para que se asienten todas las cookies...")
                time.sleep(5)

                # Usar CDP para obtener TODAS las cookies de TODOS los dominios.
                # Esto incluye las cookies de Keycloak (ol-sso.jusentrerios.gov.ar)
                # que son necesarias para que el servidor pueda autenticarse.
                # driver.get_cookies() solo retorna las cookies del dominio actual.
                resultado = driver.execute_cdp_cmd('Network.getAllCookies', {})
                todas_las_cookies = resultado.get('cookies', [])

                if not todas_las_cookies:
                    print("✗ No se encontraron cookies. Intentá de nuevo.")
                    return False

                print(f"  Cookies capturadas: {len(todas_las_cookies)}")
                for c in todas_las_cookies:
                    print(f"    - {c['name']:35s} dominio={c.get('domain', '')}")

                ARCHIVO_SESION.parent.mkdir(parents=True, exist_ok=True)
                with open(ARCHIVO_SESION, 'wb') as f:
                    pickle.dump(todas_las_cookies, f)

                tamaño = ARCHIVO_SESION.stat().st_size
                print()
                print("=" * 60)
                print("  SESIÓN GUARDADA CORRECTAMENTE")
                print("=" * 60)
                print()
                print(f"Archivo: {ARCHIVO_SESION}")
                print(f"Tamaño:  {tamaño} bytes")
                print()
                print("PRÓXIMO PASO:")
                print("  1. Entrá al panel admin de Render:")
                print("     https://descargador-expedientes.onrender.com/admin")
                print("  2. Sección 'Sesión Mesa Virtual' → subí ese archivo")
                print()
                return True

            time.sleep(intervalo)
            tiempo_esperado += intervalo

        print("✗ Tiempo agotado. No se completó el login.")
        return False

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cerrar Chrome al terminar
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    exito = renovar_sesion()
    if not exito:
        print("La sesión NO se renovó. Intentá de nuevo.")
    input("\nPresioná Enter para cerrar...")
