#!/usr/bin/env python3
"""
subir_cookies.py — Script para subir sesión de Mesa Virtual al servidor Render
================================================================================

Uso:
    python subir_cookies.py

Qué hace:
1. Abre Chrome (visible) con Mesa Virtual
2. Esperás vos a hacer login + 2FA
3. Extrae las cookies de sesión
4. Las guarda en la BD de Render (PostgreSQL)

El servidor Render leerá esas cookies la próxima vez que alguien descargue
un expediente, sin necesitar que vos hagas login de nuevo.

Cuándo volver a correrlo:
- Cuando veas el error "Sesión de Mesa Virtual no disponible" en la app
- Aproximadamente cada 10-30 días (depende de la config de Keycloak)
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

# ─── Asegurarse de que podemos importar los módulos del proyecto ───────────
sys.path.insert(0, str(Path(__file__).parent))

# ─── Cargar .env para tener DATABASE_URL ──────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

URL_MESA_VIRTUAL = "https://mesavirtual.jusentrerios.gov.ar/"


def abrir_chrome_y_esperar_login():
    """
    Abre Chrome en modo visible, navega a Mesa Virtual y espera
    a que el usuario complete el login + 2FA.

    Returns:
        list: Cookies del navegador, o None si hubo error
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    import time

    print("\n[CHROME] Abriendo Chrome...")
    print("   Vas a ver la página de Mesa Virtual.")
    print("   Hacé login con tu usuario, contraseña y 2FA.")
    print("   El script detecta automáticamente cuando terminás.\n")

    # Chrome visible (sin headless) para que puedas interactuar
    options = webdriver.ChromeOptions()
    options.add_argument('--disable-blink-features=AutomationControlled')
    # Sin --headless para que sea visible

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    driver.get(URL_MESA_VIRTUAL)

    # Esperar hasta que el usuario complete el login (máximo 10 minutos)
    print("[WAIT] Esperando que completes el login (máximo 10 minutos)...")
    timeout = 600
    tiempo = 0
    intervalo = 2

    while tiempo < timeout:
        url_actual = driver.current_url

        # Detectar cuando estamos autenticados en Mesa Virtual
        if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                "ol-sso" not in url_actual and
                "login" not in url_actual):
            print(f"\n[OK] Login detectado. URL: {url_actual[:70]}")
            time.sleep(3)  # Dejar que cargue bien

            cookies = driver.get_cookies()
            driver.quit()
            return cookies

        time.sleep(intervalo)
        tiempo += intervalo

        # Mostrar progreso cada 30 segundos
        if tiempo % 30 == 0:
            minutos_restantes = (timeout - tiempo) // 60
            print(f"   ... esperando ({minutos_restantes} min restantes)")

    print("\n[TIMEOUT] No se completó el login en 10 minutos.")
    driver.quit()
    return None


def guardar_cookies_en_render(cookies):
    """
    Guarda las cookies en la base de datos de Render.

    Conecta directamente a PostgreSQL usando DATABASE_URL del .env.
    Borra las cookies anteriores e inserta las nuevas.

    Args:
        cookies: Lista de dicts con las cookies del navegador
    """
    database_url = os.getenv('DATABASE_URL')

    if not database_url:
        print("\n[ERROR] No se encontró DATABASE_URL en el .env")
        print("   Abrí el archivo .env y asegurate de tener:")
        print("   DATABASE_URL=postgresql://...")
        print("\n   Podés encontrar la URL en Render > tu servicio > Environment")
        return False

    # Render usa postgres:// pero psycopg2/sqlalchemy requiere postgresql://
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

    print(f"\n[DB] Conectando a la BD de Render...")

    try:
        # Usar SQLAlchemy para no necesitar instalar psycopg2 por separado
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url)

        with engine.connect() as conn:
            # Crear la tabla si no existe (por si el servidor no se deployó aún)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sesion_mesa_virtual (
                    id SERIAL PRIMARY KEY,
                    cookies_json TEXT NOT NULL,
                    actualizado_en TIMESTAMP DEFAULT NOW(),
                    info VARCHAR(255)
                )
            """))

            # Borrar cookies anteriores
            conn.execute(text("DELETE FROM sesion_mesa_virtual"))

            # Insertar las nuevas
            info = f"subidas el {datetime.now().strftime('%Y-%m-%d %H:%M')} desde PC local"
            conn.execute(
                text("INSERT INTO sesion_mesa_virtual (cookies_json, info) VALUES (:cookies, :info)"),
                {"cookies": json.dumps(cookies), "info": info}
            )

            conn.commit()

        print(f"[OK] {len(cookies)} cookies guardadas en la BD de Render")
        print(f"[OK] Info: {info}")
        return True

    except Exception as e:
        print(f"\n[ERROR] No se pudo conectar a la BD: {e}")
        print("\n   Posibles causas:")
        print("   - DATABASE_URL incorrecta en .env")
        print("   - La BD de Render no está accesible desde tu red")
        print("   - Render tiene acceso restringido por IP (poco común en el plan gratuito)")
        return False


def main():
    print("=" * 60)
    print("  SUBIR COOKIES DE MESA VIRTUAL A RENDER")
    print("=" * 60)
    print("\nEste script hace login en tu PC y sube las cookies")
    print("al servidor Render para que pueda hacer descargas.")
    print()

    # Paso 1: Login en Chrome
    cookies = abrir_chrome_y_esperar_login()

    if not cookies:
        print("\n[FALLO] No se obtuvieron cookies. Abortando.")
        sys.exit(1)

    print(f"\n[OK] {len(cookies)} cookies obtenidas del navegador")

    # Paso 2: Subir a Render
    exito = guardar_cookies_en_render(cookies)

    if exito:
        print("\n" + "=" * 60)
        print("  LISTO - Las cookies están en Render")
        print("=" * 60)
        print("\nEl servidor ya puede hacer descargas.")
        print("Si en unos días vuelve a fallar, corrés este script de nuevo.")
    else:
        print("\n[FALLO] No se pudieron subir las cookies.")
        print("Revisá los errores de arriba.")
        sys.exit(1)


if __name__ == '__main__':
    main()
