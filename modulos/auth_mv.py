"""
modulos/auth_mv.py — Login Relay para Mesa Virtual
====================================================

Permite que cada usuario autentique su propia cuenta de Mesa Virtual
desde dentro de la app, sin que el servidor tenga que hacer login manual.

Flujo (Login Relay):
    1. Usuario ingresa su usuario + contraseña de MV en la app
    2. Servidor abre Chrome headless, navega a MV, llena el formulario
    3. Keycloak pide 2FA → servidor devuelve "2fa_requerido" al cliente
    4. Usuario ingresa el código de 6 dígitos de su autenticador
    5. Servidor completa el login, captura todas las cookies
    6. Cookies guardadas en BD asociadas al usuario
    7. Descargas futuras reutilizan esas cookies hasta que expiren

Importante:
    - La contraseña NUNCA se guarda en BD
    - El driver Selenium se mantiene en memoria entre paso 2 y paso 4
      (~30-60 segundos mientras el usuario tipea el código 2FA)
    - Los drivers inactivos más de 5 minutos se limpian automáticamente
"""

import json
import logging
import time
import uuid
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

URL_MESA_VIRTUAL = "https://mesavirtual.jusentrerios.gov.ar/"
TIMEOUT_LOGIN = 30        # segundos para que cargue cada página
TIMEOUT_SESION_RELAY = 300  # segundos que guardamos el driver en memoria (5 min)

# ── Almacén en memoria de drivers en espera de 2FA ────────────────────────────
# Clave: session_id (string único por intento de login)
# Valor: {'driver': WebDriver, 'timestamp': float}
_drivers_pendientes: dict = {}


def _limpiar_drivers_viejos():
    """Elimina drivers que llevan más de TIMEOUT_SESION_RELAY segundos esperando."""
    ahora = time.time()
    ids_viejos = [
        sid for sid, datos in _drivers_pendientes.items()
        if ahora - datos['timestamp'] > TIMEOUT_SESION_RELAY
    ]
    for sid in ids_viejos:
        try:
            _drivers_pendientes[sid]['driver'].quit()
        except Exception:
            pass
        del _drivers_pendientes[sid]
        logger.info(f"[AUTH_MV] Driver expirado eliminado: {sid[:8]}...")


def _crear_driver_headless():
    """Crea un Chrome headless con las opciones correctas para Render."""
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    # Sin webdriver_manager: Selenium Manager elige el driver correcto
    return webdriver.Chrome(options=options)


def _capturar_todas_las_cookies(driver):
    """
    Captura cookies de TODOS los dominios usando CDP.
    Necesario porque Mesa Virtual usa Keycloak en otro dominio (ol-sso.jusentrerios.gov.ar).
    Solo las cookies del dominio actual no alcanzan para autenticarse.
    """
    try:
        resultado = driver.execute_cdp_cmd('Network.getAllCookies', {})
        cookies = resultado.get('cookies', [])
        logger.info(f"[AUTH_MV] {len(cookies)} cookies capturadas de todos los dominios")
        return cookies
    except Exception as e:
        # Fallback: solo cookies del dominio actual
        logger.warning(f"[AUTH_MV] CDP falló, usando cookies del dominio actual: {e}")
        return driver.get_cookies()


# ── Funciones principales ─────────────────────────────────────────────────────

def iniciar_login_mv(mv_usuario: str, mv_password: str) -> dict:
    """
    Paso 1 del Login Relay: abre Chrome, navega a Mesa Virtual y llena
    el formulario de usuario + contraseña de Keycloak.

    Args:
        mv_usuario: Usuario de Mesa Virtual del abogado
        mv_password: Contraseña de Mesa Virtual (NO se guarda)

    Returns:
        dict con una de estas estructuras:
        - {'estado': '2fa_requerido', 'session_id': '...'} → hay que pedir el código
        - {'estado': 'ok', 'cookies': [...]} → login completo sin 2FA (raro)
        - {'estado': 'error', 'mensaje': '...'} → algo falló
    """
    _limpiar_drivers_viejos()

    driver = None
    try:
        logger.info(f"[AUTH_MV] Iniciando login para usuario: {mv_usuario}")
        driver = _crear_driver_headless()

        # Habilitar CDP para capturar cookies cross-domain
        driver.execute_cdp_cmd('Network.enable', {})

        # Navegar a Mesa Virtual → redirige a Keycloak
        driver.get(URL_MESA_VIRTUAL)
        WebDriverWait(driver, TIMEOUT_LOGIN).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        # Esperar a estar en la página de login de Keycloak
        logger.info(f"[AUTH_MV] URL actual: {driver.current_url[:80]}")

        # Llenar usuario
        campo_usuario = WebDriverWait(driver, TIMEOUT_LOGIN).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input#username, input[name='username']"))
        )
        campo_usuario.clear()
        campo_usuario.send_keys(mv_usuario)

        # Llenar contraseña
        campo_password = driver.find_element(By.CSS_SELECTOR, "input#password, input[name='password']")
        campo_password.clear()
        campo_password.send_keys(mv_password)

        # Hacer clic en "Ingresar"
        boton_submit = driver.find_element(
            By.CSS_SELECTOR, "input[type='submit'], button[type='submit']"
        )
        boton_submit.click()

        # Esperar a que cambie la página
        time.sleep(3)
        WebDriverWait(driver, TIMEOUT_LOGIN).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        url_actual = driver.current_url
        logger.info(f"[AUTH_MV] Después de submit: {url_actual[:80]}")

        # Verificar si pide 2FA (hay un campo OTP en la página)
        try:
            campo_otp = driver.find_element(
                By.CSS_SELECTOR, "input#otp, input[name='otp'], input[id*='otp'], input[name*='totp']"
            )
            # Hay campo OTP → guardar el driver y pedir el código al usuario
            session_id = str(uuid.uuid4())
            _drivers_pendientes[session_id] = {
                'driver': driver,
                'timestamp': time.time(),
                'mv_usuario': mv_usuario
            }
            logger.info(f"[AUTH_MV] 2FA requerido, session_id: {session_id[:8]}...")
            return {'estado': '2fa_requerido', 'session_id': session_id}

        except Exception:
            # No hay campo OTP → puede ser que el login fue exitoso o hubo error
            pass

        # Verificar si ya estamos en Mesa Virtual (login exitoso sin 2FA)
        if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                "ol-sso" not in url_actual):
            cookies = _capturar_todas_las_cookies(driver)
            driver.quit()
            logger.info(f"[AUTH_MV] Login exitoso sin 2FA para {mv_usuario}")
            return {'estado': 'ok', 'cookies': cookies, 'mv_usuario': mv_usuario}

        # Verificar si hay mensaje de error en la página (credenciales incorrectas)
        try:
            error_elem = driver.find_element(
                By.CSS_SELECTOR, ".alert-error, #input-error, .kc-feedback-text, [class*='error']"
            )
            mensaje_error = error_elem.text.strip()
            driver.quit()
            logger.warning(f"[AUTH_MV] Credenciales incorrectas: {mensaje_error}")
            return {'estado': 'error', 'mensaje': 'Usuario o contraseña incorrectos'}
        except Exception:
            pass

        # Estado desconocido
        driver.quit()
        logger.error(f"[AUTH_MV] Estado desconocido después de login: {url_actual}")
        return {'estado': 'error', 'mensaje': 'No se pudo completar el login. Intentá de nuevo.'}

    except Exception as e:
        logger.error(f"[AUTH_MV] Error en iniciar_login_mv: {e}", exc_info=True)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return {'estado': 'error', 'mensaje': f'Error al conectar con Mesa Virtual: {str(e)}'}


def completar_login_mv(session_id: str, codigo_2fa: str) -> dict:
    """
    Paso 2 del Login Relay: recibe el código 2FA, lo ingresa en el
    formulario que está esperando, y captura las cookies de sesión.

    Args:
        session_id: ID de la sesión pendiente (de iniciar_login_mv)
        codigo_2fa: Código de 6 dígitos del autenticador del usuario

    Returns:
        dict con una de estas estructuras:
        - {'estado': 'ok', 'cookies': [...], 'mv_usuario': '...'} → éxito
        - {'estado': 'error', 'mensaje': '...'} → código incorrecto u otro error
    """
    _limpiar_drivers_viejos()

    datos = _drivers_pendientes.get(session_id)
    if not datos:
        logger.warning(f"[AUTH_MV] session_id no encontrado o expirado: {session_id[:8] if session_id else 'None'}...")
        return {'estado': 'error', 'mensaje': 'Sesión expirada. Ingresá tus credenciales de nuevo.'}

    driver = datos['driver']
    mv_usuario = datos.get('mv_usuario', '')

    try:
        # Ingresar el código 2FA
        campo_otp = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input#otp, input[name='otp'], input[id*='otp'], input[name*='totp']")
            )
        )
        campo_otp.clear()
        campo_otp.send_keys(codigo_2fa.strip())

        # Hacer clic en enviar
        boton_submit = driver.find_element(
            By.CSS_SELECTOR, "input[type='submit'], button[type='submit']"
        )
        boton_submit.click()

        # Esperar a que redirija a Mesa Virtual
        time.sleep(3)
        WebDriverWait(driver, TIMEOUT_LOGIN).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        url_actual = driver.current_url
        logger.info(f"[AUTH_MV] Después de 2FA: {url_actual[:80]}")

        # Verificar login exitoso
        if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                "ol-sso" not in url_actual):
            time.sleep(2)  # Dejar que se asienten todas las cookies
            cookies = _capturar_todas_las_cookies(driver)

            # Limpiar el driver de memoria
            del _drivers_pendientes[session_id]
            driver.quit()

            logger.info(f"[AUTH_MV] Login completo con 2FA para {mv_usuario}")
            return {'estado': 'ok', 'cookies': cookies, 'mv_usuario': mv_usuario}

        # Verificar si hay error (código incorrecto)
        try:
            error_elem = driver.find_element(
                By.CSS_SELECTOR, ".alert-error, #input-error, .kc-feedback-text, [class*='error']"
            )
            mensaje_error = error_elem.text.strip()
            logger.warning(f"[AUTH_MV] Código 2FA incorrecto: {mensaje_error}")
            # NO cerramos el driver → el usuario puede reintentar con otro código
            return {'estado': 'error', 'mensaje': 'Código incorrecto. Verificá tu app autenticadora.'}
        except Exception:
            pass

        # Si llegamos aquí, algo raro pasó
        del _drivers_pendientes[session_id]
        driver.quit()
        return {'estado': 'error', 'mensaje': 'Error desconocido al verificar el código. Intentá de nuevo.'}

    except Exception as e:
        logger.error(f"[AUTH_MV] Error en completar_login_mv: {e}", exc_info=True)
        # Limpiar driver en caso de error
        try:
            if session_id in _drivers_pendientes:
                del _drivers_pendientes[session_id]
            driver.quit()
        except Exception:
            pass
        return {'estado': 'error', 'mensaje': f'Error al verificar el código: {str(e)}'}


# ── Funciones de BD ───────────────────────────────────────────────────────────

def crear_cliente_desde_cookies(cookies: list):
    """
    Crea un ClienteSelenium ya autenticado usando cookies guardadas.
    Usado por el pipeline para descargar expedientes sin hacer login de nuevo.

    Args:
        cookies: Lista de cookies capturadas con _capturar_todas_las_cookies()

    Returns:
        ClienteSelenium listo para usar, o None si las cookies expiraron
    """
    from modulos.login import ClienteSelenium

    driver = None
    try:
        driver = _crear_driver_headless()
        driver.execute_cdp_cmd('Network.enable', {})

        # Navegar al dominio para poder inyectar cookies
        driver.get(URL_MESA_VIRTUAL)
        time.sleep(1)

        # Inyectar cookies de TODOS los dominios capturados
        for cookie in cookies:
            try:
                cookie_limpia = {
                    k: v for k, v in cookie.items()
                    if k in ('name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'sameSite')
                }
                driver.add_cookie(cookie_limpia)
            except Exception:
                pass

        # Recargar con las cookies aplicadas
        driver.get(URL_MESA_VIRTUAL)
        time.sleep(3)

        url_actual = driver.current_url

        if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                "ol-sso" not in url_actual and
                "login" not in url_actual):
            # Sesión válida → crear cliente con este driver
            cliente = ClienteSelenium(URL_MESA_VIRTUAL)
            cliente.driver = driver
            logger.info("[AUTH_MV] Cliente creado desde cookies (sesión válida)")
            return cliente

        # La sesión expiró
        logger.warning(f"[AUTH_MV] Cookies expiradas (URL: {url_actual[:60]})")
        driver.quit()
        return None

    except Exception as e:
        logger.error(f"[AUTH_MV] Error creando cliente desde cookies: {e}")
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return None


def guardar_sesion_usuario(user_id: int, cookies: list, mv_usuario: str = None) -> bool:
    """
    Guarda las cookies de sesión de Mesa Virtual en la BD, asociadas al usuario.

    Args:
        user_id: ID del usuario en la app
        cookies: Lista de dicts con las cookies capturadas
        mv_usuario: Nombre de usuario en Mesa Virtual (opcional, para mostrar en UI)

    Returns:
        True si se guardó correctamente, False si hubo error
    """
    try:
        from modulos.models import SesionUsuarioMV
        from modulos.database import db

        # Buscar sesión existente del usuario
        sesion = SesionUsuarioMV.query.filter_by(user_id=user_id).first()

        if sesion:
            # Actualizar la existente
            sesion.cookies_json = json.dumps(cookies)
            sesion.actualizado_en = datetime.utcnow()
            if mv_usuario:
                sesion.mv_usuario = mv_usuario
        else:
            # Crear nueva
            sesion = SesionUsuarioMV(
                user_id=user_id,
                cookies_json=json.dumps(cookies),
                mv_usuario=mv_usuario
            )
            db.session.add(sesion)

        db.session.commit()
        logger.info(f"[AUTH_MV] Sesión guardada para user_id={user_id} ({len(cookies)} cookies)")
        return True

    except Exception as e:
        logger.error(f"[AUTH_MV] Error guardando sesión: {e}")
        return False


def obtener_cookies_usuario(user_id: int):
    """
    Obtiene las cookies de sesión guardadas para un usuario.

    Returns:
        list: Cookies guardadas, o None si no hay sesión
    """
    try:
        from modulos.models import SesionUsuarioMV

        sesion = SesionUsuarioMV.query.filter_by(user_id=user_id).first()
        if sesion:
            return json.loads(sesion.cookies_json)
        return None

    except Exception as e:
        logger.error(f"[AUTH_MV] Error obteniendo cookies: {e}")
        return None


def verificar_sesion_usuario(user_id: int) -> bool:
    """
    Verifica si el usuario tiene una sesión válida de Mesa Virtual.

    Abre Chrome headless, inyecta las cookies guardadas y verifica
    que no redirige a la página de login.

    Returns:
        True si la sesión es válida, False si expiró o no existe
    """
    cookies = obtener_cookies_usuario(user_id)
    if not cookies:
        logger.info(f"[AUTH_MV] user_id={user_id} no tiene sesión guardada")
        return False

    driver = None
    try:
        driver = _crear_driver_headless()
        driver.execute_cdp_cmd('Network.enable', {})

        # Navegar al dominio para poder inyectar cookies
        driver.get(URL_MESA_VIRTUAL)
        time.sleep(1)

        # Inyectar cada cookie
        for cookie in cookies:
            try:
                # Limpiar campos que Selenium no acepta
                cookie_limpia = {
                    k: v for k, v in cookie.items()
                    if k in ('name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'sameSite')
                }
                driver.add_cookie(cookie_limpia)
            except Exception:
                pass

        # Recargar con las cookies aplicadas
        driver.get(URL_MESA_VIRTUAL)
        time.sleep(3)

        url_actual = driver.current_url

        if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                "ol-sso" not in url_actual and
                "login" not in url_actual):
            logger.info(f"[AUTH_MV] Sesión válida para user_id={user_id}")
            return True

        logger.info(f"[AUTH_MV] Sesión expirada para user_id={user_id} (URL: {url_actual[:60]})")
        return False

    except Exception as e:
        logger.error(f"[AUTH_MV] Error verificando sesión: {e}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
