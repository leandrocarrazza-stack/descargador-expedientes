#!/usr/bin/env python3
"""
setup_sesion_mejorado.py
========================

Script mejorado para renovar sesión de Mesa Virtual con flags correctos de Chrome.
"""

import sys
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import pickle


def renovar_sesion():
    """Renovar sesión de Mesa Virtual con login manual."""
    print("\n" + "="*70)
    print("RENOVAR SESION DE MESA VIRTUAL")
    print("="*70 + "\n")

    archivo_sesion = Path.home() / ".mesa_virtual_sesion.pkl"

    try:
        # Paso 1: Crear navegador Chrome VISIBLE
        print("[PASO 1] Abriendo navegador Chrome...")
        print("-" * 70)

        options = webdriver.ChromeOptions()
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        # NO --headless, así Chrome es visible

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )

        # Navegar a Mesa Virtual
        url_mesa_virtual = "https://mesavirtual.jusentrerios.gov.ar/"
        print("[NET] Navegando a Mesa Virtual...")
        driver.get(url_mesa_virtual)

        # Esperar a que el usuario complete el login
        print("\n[WAIT] Esperando que completes el login en el navegador...")
        print("   (El script esperara 5 minutos maximo)")
        print("   Deberias ver: Mesa Virtual cargando despues del login\n")

        tiempo_esperado = 0
        intervalo_chequeo = 1
        urls_vistas = []

        while tiempo_esperado < 300:
            url_actual = driver.current_url

            if url_actual not in urls_vistas:
                urls_vistas.append(url_actual)
                print(f"  > {url_actual[:70]}...")

            # Detectar cuando estamos en Mesa Virtual (no en Keycloak)
            if "mesavirtual.jusentrerios.gov.ar" in url_actual and \
               "ol-sso" not in url_actual:
                print(f"\n[OK] Login completado (en Mesa Virtual)\n")
                # Esperar a que cargue completamente
                time.sleep(3)
                break

            time.sleep(intervalo_chequeo)
            tiempo_esperado += intervalo_chequeo
        else:
            print("[TIMEOUT] Timeout: No se completo el login despues de 5 minutos")
            driver.quit()
            return False

        # Paso 2: Guardar sesión (cookies)
        print("[PASO 2] Guardando sesion...")
        print("-" * 70)

        cookies = driver.get_cookies()
        if not cookies:
            print("[ERROR] No hay cookies para guardar")
            driver.quit()
            return False

        # Crear directorio si no existe
        archivo_sesion.parent.mkdir(parents=True, exist_ok=True)

        with open(archivo_sesion, 'wb') as f:
            pickle.dump(cookies, f)

        print(f"[OK] Cookies guardadas en: {archivo_sesion}")

        # Paso 3: Cerrar navegador
        print("\n[PASO 3] Cerrando navegador...")
        print("-" * 70)
        driver.quit()
        print("[OK] Navegador cerrado correctamente")

        # Resumen final
        if archivo_sesion.exists():
            tamanio = archivo_sesion.stat().st_size
            print(f"\n[OK] SESION RENOVADA EXITOSAMENTE!")
            print(f"   Archivo: {archivo_sesion}")
            print(f"   Tamanio: {tamanio} bytes")
            print(f"\n   Ahora puedes descargar expedientes\n")
            return True
        else:
            print(f"\n[ERROR] Archivo de sesion no encontrado: {archivo_sesion}")
            return False

    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            driver.quit()
        except:
            pass
        return False


if __name__ == "__main__":
    try:
        exito = renovar_sesion()
        sys.exit(0 if exito else 1)
    except KeyboardInterrupt:
        print("\n\n[PAUSE] Cancelado por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
