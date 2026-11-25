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
import argparse
from pathlib import Path

# Agregar el proyecto al path para poder importar los módulos
sys.path.insert(0, str(Path(__file__).parent))

from selenium import webdriver
# Selenium 4.6+ incluye Selenium Manager: no necesitamos webdriver_manager.

URL_MESA_VIRTUAL = "https://mesavirtual.jusentrerios.gov.ar/"
ARCHIVO_SESION = Path.home() / ".mesa_virtual_sesion.pkl"
# Perfil propio de Chrome: recuerda el usuario (por "Seguir conectado") y, si VOS lo guardás en el
# administrador de contraseñas de Chrome, también la clave. El script nunca lee ni escribe credenciales.
PERFIL_CHROME = Path.home() / ".mesa_virtual_chrome"
TIMEOUT_SEGUNDOS = 600  # 10 minutos

# ── Login automático (sin intervención) ─────────────────────────────────────────
# Si guardaste usuario, clave y el secreto del 2FA con `python renovar_sesion.py --guardar-credenciales`,
# el modo --auto hace el login solo, en un Chrome invisible: el código de 6 dígitos lo calcula pyotp
# con el mismo secreto que usa la app autenticadora del celular.
# Las credenciales viven en el Administrador de credenciales de Windows (cifradas con tu usuario de
# Windows), nunca en archivos del proyecto ni en git.
SERVICIO_CREDENCIALES = "mesa_virtual_er"
CLAVES_CREDENCIALES = ("usuario", "clave", "totp")  # "dispositivo" es opcional


def leer_credenciales():
    """Devuelve {'usuario','clave','totp','dispositivo'} o None si falta alguna obligatoria."""
    try:
        import keyring
        datos = {k: keyring.get_password(SERVICIO_CREDENCIALES, k)
                 for k in CLAVES_CREDENCIALES + ("dispositivo",)}
    except Exception:
        return None
    return datos if all(datos[k] for k in CLAVES_CREDENCIALES) else None


def guardar_credenciales():
    """Pide los datos por teclado (sin mostrarlos en pantalla) y los guarda en Windows."""
    import getpass, keyring, pyotp
    print("Se guardan en el Administrador de credenciales de Windows (servicio "
          f"'{SERVICIO_CREDENCIALES}'). Nada queda en archivos del proyecto.\n")
    usuario = input("Usuario de Mesa Virtual: ").strip()
    clave = getpass.getpass("Clave (no se ve mientras escribís): ")
    secreto = getpass.getpass("Clave secreta del autenticador (el texto largo que muestra "
                              "'¿No podés escanear?'): ").replace(" ", "").upper()
    try:
        codigo = pyotp.TOTP(secreto).now()
    except Exception:
        print("✗ Esa clave secreta no es válida (debería ser letras A-Z y números 2-7). No se guardó nada.")
        return False
    print(f"\nCódigo que genera la PC ahora: {codigo}")
    print("Si tu app del celular usa el mismo secreto, tiene que mostrar el mismo número (o el anterior/siguiente).")
    dispositivo = input("Nombre que le pusiste a este autenticador en Mesa Virtual "
                        "(Enter si tenés uno solo): ").strip()
    for k, v in (("usuario", usuario), ("clave", clave), ("totp", secreto)):
        keyring.set_password(SERVICIO_CREDENCIALES, k, v)
    if dispositivo:
        keyring.set_password(SERVICIO_CREDENCIALES, "dispositivo", dispositivo)
    else:
        try:
            keyring.delete_password(SERVICIO_CREDENCIALES, "dispositivo")
        except Exception:
            pass
    print("✓ Credenciales guardadas. Probá ahora: python renovar_sesion.py --auto")
    return True


def _en_mesa_virtual(url):
    """True si la URL es Mesa Virtual ya logueada (se mira solo dominio y ruta, no el #fragmento)."""
    from urllib.parse import urlparse
    p = urlparse(url)
    return p.netloc == "mesavirtual.jusentrerios.gov.ar" and "login" not in p.path


def _guardar_cookies(driver):
    """Captura las cookies de TODOS los dominios (Mesa Virtual + Keycloak) y las guarda en el .pkl."""
    import datetime as dt
    cookies = driver.execute_cdp_cmd('Network.getAllCookies', {}).get('cookies', [])
    if not cookies:
        print("✗ No se encontraron cookies.")
        return False
    for c in cookies:
        if c['name'] == 'KEYCLOAK_SESSION' and c.get('expires', -1) > 0:
            print(f"  KEYCLOAK_SESSION vence {dt.datetime.fromtimestamp(c['expires']):%Y-%m-%d %H:%M}")
    ARCHIVO_SESION.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO_SESION, 'wb') as f:
        pickle.dump(cookies, f)
    print(f"✓ Sesión guardada en {ARCHIVO_SESION} ({len(cookies)} cookies)")
    return True


def login_automatico(cred):
    """Login completo sin intervención: usuario + clave + código 2FA calculado. True si quedó logueado."""
    import pyotp
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    print("Login automático (Chrome invisible, perfil limpio)...")
    options = webdriver.ChromeOptions()
    # Perfil temporal limpio (sin user-data-dir): así Keycloak siempre pide login y la sesión
    # nueva dura las 6 h completas, en vez de reusar una vieja a punto de vencer.
    options.add_argument('--headless=new')
    options.add_argument('--window-size=1200,800')
    options.add_argument('--disable-blink-features=AutomationControlled')
    driver = webdriver.Chrome(options=options)
    espera = WebDriverWait(driver, 30)
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        driver.get(URL_MESA_VIRTUAL)

        # 1) Usuario y clave en la página de Keycloak
        campo = espera.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input#username, input[name='username']")))
        campo.clear()
        campo.send_keys(cred["usuario"])
        clave = driver.find_element(By.CSS_SELECTOR, "input#password, input[name='password']")
        clave.clear()
        clave.send_keys(cred["clave"])
        driver.execute_script(  # "Seguir conectado", por las dudas (no alarga la sesión, pero no molesta)
            "var c=document.querySelector('input[name=rememberMe],#rememberMe'); if(c&&!c.checked)c.click();")
        driver.find_element(By.CSS_SELECTOR, "input[type='submit'], button[type='submit']").click()

        # 2) Esperar la pantalla del código 2FA (o Mesa Virtual directo, si no lo pidiera)
        sel_otp = "input#otp, input[name='otp'], input[id*='otp'], input[name*='totp']"
        espera.until(lambda d: _en_mesa_virtual(d.current_url) or d.find_elements(By.CSS_SELECTOR, sel_otp)
                     or d.find_elements(By.CSS_SELECTOR, "#input-error, .alert-error, .kc-feedback-text"))
        if not _en_mesa_virtual(driver.current_url):
            if not driver.find_elements(By.CSS_SELECTOR, sel_otp):
                print("✗ Keycloak rechazó usuario o clave.")
                return False
            # Si hay varios autenticadores cargados, Keycloak muestra para elegir: tomar el de la PC.
            if cred.get("dispositivo"):
                # Si hay un selector de autenticadores y el nuestro no está, NO mandar el código:
                # se validaría contra otro autenticador y sumaría un intento fallido (riesgo de bloqueo).
                nombres = driver.execute_script(
                    "return Array.from(document.querySelectorAll('input[name=selectedCredentialId]')).map(function(r){"
                    " var l=(r.closest('label')||document.querySelector('label[for=\"'+r.id+'\"]')||r.parentElement);"
                    " return l?l.textContent.trim().replace(/\\s+/g,' '):''});")
                if nombres and not any(cred["dispositivo"].lower() in n.lower() for n in nombres):
                    print(f"✗ El autenticador '{cred['dispositivo']}' no figura en la pantalla del código. "
                          f"Mesa Virtual ofrece: {nombres}. No se envió ningún código.")
                    return False
                driver.execute_script(
                    "var n=arguments[0].toLowerCase();"
                    "document.querySelectorAll('input[name=selectedCredentialId]').forEach(function(r){"
                    " var l=(r.closest('label')||document.querySelector('label[for=\"'+r.id+'\"]')||r.parentElement);"
                    " if(l&&l.textContent.toLowerCase().indexOf(n)>=0) r.click();});", cred["dispositivo"])
            # Si el código está por cambiar (quedan <3 s), esperar al siguiente para no mandarlo vencido.
            totp = pyotp.TOTP(cred["totp"])
            if totp.interval - (time.time() % totp.interval) < 3:
                time.sleep(4)
            otp = driver.find_element(By.CSS_SELECTOR, sel_otp)
            otp.clear()
            otp.send_keys(totp.now())
            driver.find_element(By.CSS_SELECTOR, "input[type='submit'], button[type='submit']").click()

        # 3) Esperar a estar dentro de Mesa Virtual
        try:
            WebDriverWait(driver, 40).until(lambda d: _en_mesa_virtual(d.current_url))
        except Exception:
            print("✗ No entró a Mesa Virtual después del código 2FA (¿secreto o dispositivo incorrecto?).")
            return False
        time.sleep(5)  # que se asienten todas las cookies
        return _guardar_cookies(driver)
    except Exception as e:
        print(f"✗ Error en el login automático: {type(e).__name__}: {str(e)[:200]}")
        return False
    finally:
        try:
            driver.quit()
        except Exception:
            pass


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
    options.add_argument(f'--user-data-dir={PERFIL_CHROME}')
    # Dejar activo el administrador de contraseñas de Chrome (Selenium lo apaga por defecto)
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('prefs', {'credentials_enable_service': True,
                                              'profile.password_manager_enabled': True})

    driver = webdriver.Chrome(options=options)  # Selenium Manager elige el driver automáticamente

    try:
        # Habilitar CDP Network para poder acceder a todas las cookies
        driver.execute_cdp_cmd('Network.enable', {})

        driver.get(URL_MESA_VIRTUAL)

        tiempo_esperado = 0
        intervalo = 1
        urls_vistas = []
        tildado = False  # ya se marcó "Seguir conectado"?

        while tiempo_esperado < TIMEOUT_SEGUNDOS:
            url_actual = driver.current_url

            # Tilda "Seguir conectado" (el "Recordarme" de Keycloak) en la página de login.
            # Solo marca la casilla: usuario, clave y código 2FA los ponés vos.
            # Sirve para que la sesión dure más de las 6 h normales.
            if not tildado and "ol-sso" in url_actual:
                try:
                    tildado = bool(driver.execute_script(
                        "var c=document.querySelector('input[name=rememberMe],#rememberMe')"
                        "||document.querySelector('input[type=checkbox]');"
                        "if(!c)return false; if(!c.checked)c.click(); return c.checked;"))
                    if tildado:
                        print("  ✓ Casilla 'Seguir conectado' marcada")
                except Exception:
                    pass

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

                # Cuánto va a durar la sesión según Keycloak (solo fechas, nunca valores)
                import datetime as dt
                for c in todas_las_cookies:
                    if c['name'] in ('KEYCLOAK_SESSION', 'KEYCLOAK_IDENTITY'):
                        v = c.get('expires', -1)
                        print(f"  {c['name']}: " + (f"vence {dt.datetime.fromtimestamp(v):%Y-%m-%d %H:%M}"
                              if v and v > 0 else "de sesión (se borra al cerrar Chrome)"))

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
    ap = argparse.ArgumentParser(description="Renueva la sesión de Mesa Virtual (login manual en Chrome).")
    ap.add_argument("--auto", action="store_true",
                    help="modo tarea programada: si hay credenciales guardadas hace el login solo; "
                         "si no, espera 30 min el login manual. No pide Enter al final")
    ap.add_argument("--guardar-credenciales", action="store_true",
                    help="guarda usuario, clave y secreto 2FA en el Administrador de credenciales de Windows")
    ap.add_argument("--ver-codigo", action="store_true",
                    help="muestra en vivo el código de 6 dígitos que calcula la PC (para activar el autenticador)")
    ap.add_argument("--manual", action="store_true",
                    help="con --auto: ignora las credenciales guardadas y abre Chrome para el login a mano")
    args = ap.parse_args()
    if args.guardar_credenciales:
        sys.exit(0 if guardar_credenciales() else 1)
    if args.ver_codigo:
        import keyring, pyotp
        secreto = keyring.get_password(SERVICIO_CREDENCIALES, "totp")
        if not secreto:
            sys.exit("No hay secreto guardado: corré primero --guardar-credenciales")
        totp = pyotp.TOTP(secreto)
        print("Código de la PC (se actualiza solo; Ctrl+C para salir):\n")
        try:
            while True:
                resto = int(totp.interval - time.time() % totp.interval)
                print(f"\r   {totp.now()}   (cambia en {resto:2d} s) ", end="", flush=True)
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)
    if args.auto:
        TIMEOUT_SEGUNDOS = 1800
        cred = None if args.manual else leer_credenciales()
        if cred:
            sys.exit(0 if login_automatico(cred) else 1)
    exito = renovar_sesion()
    if not exito:
        print("La sesión NO se renovó. Intentá de nuevo.")
    if not args.auto:
        input("\nPresioná Enter para cerrar...")
    sys.exit(0 if exito else 1)
