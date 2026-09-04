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
import os
import signal
import time
import uuid
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from modulos.concurrencia import gestor

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

URL_MESA_VIRTUAL = "https://mesavirtual.jusentrerios.gov.ar/"
TIMEOUT_LOGIN = 30        # segundos para que cargue cada página
# Antes 180 (3 min): en producción se vio un usuario escribir un código 2FA
# incorrecto, corregirlo ~2 minutos después (tiempo normal para notar el
# typo y volver a mirar la app del autenticador) y encontrarse la sesión ya
# expirada. El reloj de estos 180s arranca apenas se detecta el campo OTP,
# así que si el tramo previo (arrancar Chrome, cargar Keycloak) viene lento
# -bajo presión de memoria, por ejemplo- le come minutos al presupuesto
# antes de que el usuario llegue siquiera a ver el campo. 300s (5 min) deja
# margen real para un segundo intento sin que el costo sea alto: sigue
# siendo un techo, no una espera indefinida.
TIMEOUT_SESION_RELAY = 300  # segundos que guardamos el driver en memoria (5 min)
# Cuánto espera iniciar_login_mv() por un permiso de "navegador" libre (ver
# modulos/concurrencia.py) antes de decirle al usuario que reintente. El
# Chrome del login vive hasta TIMEOUT_SESION_RELAY compitiendo por RAM con
# el de cualquier descarga en curso -antes de esto, ni siquiera esperaba:
# arrancaba su propio Chrome sin importar si ya había otro abierto, lo que
# causó un OOM real en producción (dos usuarios con Chrome simultáneo). 60s
# alcanza para que una descarga que ya estaba usando el único cupo libere
# el suyo (cierra Chrome antes de convertir/unificar); no tiene sentido
# hacer esperar más a una request HTTP sincrónica como ésta.
TIMEOUT_ESPERA_NAVEGADOR_LOGIN = 60
# Selenium NO pone timeout de socket por defecto en el comando HTTP que manda a
# chromedriver (RemoteConnection._timeout queda sin setear = bloquea para
# siempre). Si el renderer de Chrome se cuelga (memoria, proceso zombie, etc.)
# CUALQUIER llamada (execute_script, get, click...) puede quedarse esperando
# una respuesta que nunca llega. Eso mantiene vivo el thread que la hizo sin
# pasar nunca por su finally, así que el permiso de concurrencia que esté
# reteniendo (ver modulos/concurrencia.py) queda tomado para siempre y
# bloquea a los demás usuarios hasta que se reinicie el servicio. Poniendo
# un techo acá cualquier comando colgado revienta con una excepción normal
# en vez de trabarse: el caller la captura como cualquier otro error, el
# job termina en 'error' y el permiso se libera solo.
#
# OJO: esto es un techo por COMANDO, no por operación. Selenium reintenta
# automáticamente (a nivel urllib3) hasta 3 veces un comando que falla por
# conexión rota, y CADA reintento vuelve a esperar el timeout completo — un
# chromedriver realmente colgado puede terminar tardando hasta 3x esto
# (~4.5 min) antes de que la excepción llegue a nuestro código. Visto en
# producción. Es la razón de ser de limpiar_chrome_huerfano() más abajo:
# ese driver.quit() del catch también depende de poder hablarle al mismo
# chromedriver colgado, así que puede fallar en silencio y dejar el
# proceso vivo consumiendo RAM mucho después de que el usuario ya vio el
# error.
TIMEOUT_COMANDO_SELENIUM = 90  # segundos
RemoteConnection.set_timeout(TIMEOUT_COMANDO_SELENIUM)
# Render starter (512 MB) ocasionalmente no tiene RAM libre en el instante exacto
# en que Chrome intenta arrancar (otro hilo descargando/convirtiendo PDFs), lo que
# produce "session not created: chrome not reachable". Es transitorio: reintentar
# con una pequeña espera (para que el otro proceso libere memoria) casi siempre
# resuelve el problema sin intervención del usuario.
REINTENTOS_CHROME = 3
ESPERA_ENTRE_REINTENTOS = 4  # segundos

# Techo de edad para considerar un chromedriver/chrome "huérfano" (ver
# limpiar_chrome_huerfano). Ningún Chrome legítimo de esta app vive más que
# esto: el caso más largo es el login-relay esperando el código 2FA
# (TIMEOUT_SESION_RELAY), y el pipeline de descarga cierra su Chrome bastante
# antes que eso. El margen extra (120s) es para no pisarle los talones a un
# caso legítimo apenas más lento de lo normal.
EDAD_MAXIMA_CHROME_SEG = TIMEOUT_SESION_RELAY + 120

# ── Almacén en memoria de drivers en espera de 2FA ────────────────────────────
# Clave: session_id (string único por intento de login)
# Valor: {'driver': WebDriver, 'timestamp': float}
_drivers_pendientes: dict = {}


def _cerrar_driver_relay(driver, session_id: str = None) -> None:
    """
    Cierra un driver del login relay Y libera el permiso de "navegador" que
    tomó al crearse (ver tomar_navegador_directo() en modulos/concurrencia.py).

    Centralizado acá a propósito: iniciar_login_mv/completar_login_mv tienen
    varios puntos de salida (éxito, credenciales/código incorrectos, estado
    desconocido, excepción, expiración por tiempo) y cada uno necesita esta
    misma pareja de pasos exactamente una vez. Hacerlo a mano en cada lugar
    arriesga un release-doble (deja pasar un Chrome de más) o uno faltante
    (el cupo queda tomado para siempre, bloqueando a todo el mundo).
    """
    if session_id is not None:
        _drivers_pendientes.pop(session_id, None)
    try:
        driver.quit()
    except Exception:
        pass
    gestor.soltar_navegador_directo()


def _limpiar_drivers_viejos():
    """Elimina drivers que llevan más de TIMEOUT_SESION_RELAY segundos esperando."""
    ahora = time.time()
    ids_viejos = [
        sid for sid, datos in _drivers_pendientes.items()
        if ahora - datos['timestamp'] > TIMEOUT_SESION_RELAY
    ]
    for sid in ids_viejos:
        datos = _drivers_pendientes.get(sid)
        if datos:
            _cerrar_driver_relay(datos['driver'], sid)
        logger.info(f"[AUTH_MV] Driver expirado eliminado: {sid[:8]}...")


_EJECUTABLES_CHROME = ('chromedriver', 'chrome', 'google-chrome', 'google-chrome-stable')


def limpiar_chrome_huerfano(edad_maxima_seg: float = None, fn_reloj=time.time) -> int:
    """
    Mata procesos chromedriver/chrome que llevan vivos más de edad_maxima_seg
    (por defecto EDAD_MAXIMA_CHROME_SEG).

    Por qué hace falta: cuando chromedriver mismo deja de responder -no sólo
    una página lenta, sino el propio proceso colgado (visto en producción:
    un comando agotó los 3 reintentos automáticos de Selenium, ~4.5 minutos
    en total antes de tirar la excepción)- el driver.quit() de cada bloque
    except también depende de poder hablarle a ESE MISMO chromedriver por
    HTTP. Si está colgado de verdad, quit() falla en silencio (está envuelto
    en un try/except en cada caller, a propósito, para no ocultar el error
    real detrás de uno secundario) y el proceso queda como zombie
    consumiendo RAM indefinidamente. En un servidor de 512 MB eso degrada
    TODO lo que venga después -otros logins, otras descargas- no sólo al
    usuario que tuvo la mala suerte original.

    Basado en EDAD, no en un registro de qué PID está "en uso": es más
    simple y no puede matar por error un Chrome legítimo, porque ningún
    Chrome legítimo de esta app vive más que EDAD_MAXIMA_CHROME_SEG (ver esa
    constante) — ni siquiera el que está esperando el código 2FA del
    usuario.

    Mismo patrón /proc que matar_procesos_soffice() en modulos/conversion.py
    (sin psutil: no está instalado en la imagen), comparando el ejecutable
    exacto para no matar por accidente algo que sólo mencione "chrome" en
    un argumento.

    Retorna la cantidad de procesos eliminados (para poder testear sin
    parsear logs).
    """
    if edad_maxima_seg is None:
        edad_maxima_seg = EDAD_MAXIMA_CHROME_SEG

    ahora = fn_reloj()
    eliminados = 0
    try:
        for entrada in os.listdir('/proc'):
            if not entrada.isdigit():
                continue
            pid = int(entrada)
            try:
                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                    argv = f.read().decode('utf-8', errors='ignore').split('\x00')
                ejecutable = os.path.basename(argv[0]) if argv and argv[0] else ''
                if ejecutable not in _EJECUTABLES_CHROME:
                    continue
                edad = ahora - os.stat(f'/proc/{pid}').st_ctime
            except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
                continue

            if edad < edad_maxima_seg:
                continue

            try:
                os.kill(pid, signal.SIGKILL)
                eliminados += 1
            except (ProcessLookupError, PermissionError):
                pass

        if eliminados:
            logger.info(f"[AUTH_MV] {eliminados} proceso(s) Chrome/chromedriver huérfano(s) eliminado(s)")
    except Exception as e:
        logger.warning(f"[AUTH_MV] Error limpiando Chrome huérfano: {str(e)[:60]}")

    return eliminados


def _crear_driver_headless():
    """Crea un Chrome headless con las opciones correctas para Render."""
    # Barrer zombies ANTES de pedirle RAM a un Chrome nuevo: es el mismo
    # criterio que matar_procesos_soffice() en pipeline.py (liberar memoria
    # justo antes del momento que más la necesita). Esta función es el único
    # lugar de la app que crea un driver -login-relay y pipeline de descarga
    # pasan los dos por acá- así que un solo llamado alcanza para cubrir
    # ambos caminos.
    limpiar_chrome_huerfano()
    options = webdriver.ChromeOptions()

    # ── Opciones base ──────────────────────────────────────────────────────────
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')       # Usa /tmp en vez de /dev/shm (limitado en Docker)
    options.add_argument('--disable-blink-features=AutomationControlled')  # Evita detección como bot

    # ── Ahorro de memoria (Render starter plan: 512 MB) ────────────────────────
    # El plan no va a subir de tamaño: hay que exprimir cada MB posible.
    options.add_argument('--disable-extensions')           # No cargar extensiones (~30 MB ahorrados)
    options.add_argument('--disable-plugins')              # No cargar plugins del sistema
    options.add_argument('--disable-background-networking') # Desactivar sync y updates en background
    options.add_argument('--disable-default-apps')        # No apps de Chrome por defecto
    options.add_argument('--disable-sync')                # Desactivar sincronización con cuenta Google
    options.add_argument('--no-first-run')                # Saltar wizard de primera vez
    options.add_argument('--mute-audio')                  # Sin audio (innecesario en headless)
    options.add_argument('--disable-gpu')                  # Sin GPU (irrelevante en servidor, ahorra RAM)
    # 192 MB seguía dejando poco margen para el resto (overhead nativo de Chrome +
    # Python/gunicorn) en un límite total de 512 MB. Bajar más arriesga que la SPA
    # (Material-UI, pesada) se quede sin heap para renderizar — si eso llega a pasar,
    # subir este número antes que ningún otro ajuste de esta función.
    options.add_argument('--js-flags=--max-old-space-size=160')
    # Todas las features a desactivar van en UNA sola flag: Chrome no fusiona
    # --disable-features repetidos, sólo el último add_argument gana.
    # - IsolateOrigins,site-per-process: Mesa Virtual delega el login en Keycloak
    #   (otro origen: ol-sso.jusentrerios.gov.ar) y carga recursos de terceros (ver
    #   origin-trial de google.com en su HTML). Con Site Isolation (activado por
    #   defecto en Chrome moderno) cada origen distinto corre en su PROPIO proceso
    #   renderer, multiplicando el overhead de memoria.
    # - BackForwardCache: Chrome guarda páginas completas en RAM para volver atrás
    #   instantáneo. No lo necesitamos (el flujo sólo avanza).
    # - Translate,AutofillServerCommunication,OptimizationHints: subsistemas que no
    #   usamos, cada uno con su propio overhead de memoria/red en background.
    options.add_argument(
        '--disable-features=IsolateOrigins,site-per-process,BackForwardCache,'
        'Translate,AutofillServerCommunication,OptimizationHints,'
        'MediaRouter,'                        # Descubrimiento de Chromecast en background
        'DialMediaRouteProvider,'             # Complementa a MediaRouter (otro descubrimiento de red)
        'OptimizationGuideModelDownloading,'  # Evita descarga de modelos de ML en background
        'OptimizationHintsFetching,'          # Cierra la familia "optimization guide" (fetch en background)
        'OptimizationTargetPrediction,'       # Idem, tercer miembro de la misma familia
        'PrivacySandboxSettings4,'            # Evita diálogo de privacidad que podría robar foco
        'HeavyAdPrivacyMitigations,'          # Detección de "heavy ads" sin uso (no hay ads en el flujo)
        'AudioServiceOutOfProcess'            # Audio service in-process en vez de proceso aparte
    )
    options.add_argument('--renderer-process-limit=1')
    # No cargar imágenes: el DOM no cambia (los íconos de Mesa Virtual son SVG
    # inline, no <img>), así que no afecta ningún selector — sólo evita que Chrome
    # descargue/decodifique/cachee assets que nunca miramos.
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--disk-cache-size=1')            # Sin caché de disco
    options.add_argument('--media-cache-size=1')           # Sin caché de media
    options.add_argument('--disable-component-update')     # No busca actualizaciones de componentes
    options.add_argument('--disable-domain-reliability')   # Sin telemetría interna de Chrome
    options.add_argument('--disable-hang-monitor')         # Sin monitor de "página no responde"
    options.add_argument('--disable-client-side-phishing-detection')  # Sin Safe Browsing local

    # ── GPU/rasterizador de software: sin GPU real, Chrome igual levanta un proceso
    # GPU con SwiftShader (rasterizador por software) como fallback para WebGL/3D.
    # Mesa Virtual no usa nada de esto (íconos son SVG inline, sin canvas 3D).
    options.add_argument('--disable-software-rasterizer')      # Evita el proceso GPU en modo fallback software
    options.add_argument('--disable-webgl')                    # Sin WebGL: no hay motivo para abrir contexto 3D
    options.add_argument('--disable-3d-apis')                  # Cierra el resto de superficie 3D a nivel renderer
    options.add_argument('--disable-accelerated-2d-canvas')    # Canvas 2D por CPU, sin vía a GPU
    options.add_argument('--disable-accelerated-video-decode') # Sin intento de decodificar video por GPU

    # Sin webdriver_manager: Selenium Manager elige el driver correcto automáticamente
    ultimo_error = None
    for intento in range(1, REINTENTOS_CHROME + 1):
        try:
            return webdriver.Chrome(options=options)
        except WebDriverException as e:
            ultimo_error = e
            if intento < REINTENTOS_CHROME:
                logger.warning(
                    f"[AUTH_MV] Chrome no arrancó (intento {intento}/{REINTENTOS_CHROME}): {e}. "
                    f"Reintentando en {ESPERA_ENTRE_REINTENTOS}s..."
                )
                time.sleep(ESPERA_ENTRE_REINTENTOS)
            else:
                logger.error(f"[AUTH_MV] Chrome no pudo arrancar tras {REINTENTOS_CHROME} intentos: {e}")
    raise ultimo_error


def _inyectar_cookies_cdp(driver, cookies):
    """
    Inyecta cookies de CUALQUIER dominio usando Chrome DevTools Protocol.

    driver.add_cookie() (API estándar de Selenium) sólo permite setear cookies
    cuyo dominio coincida con la página actualmente cargada, y falla en
    silencio para el resto. Como Mesa Virtual delega el login en Keycloak
    (dominio ol-sso.jusentrerios.gov.ar, distinto de mesavirtual.jusentrerios.gov.ar),
    las cookies de sesión SSO nunca se llegaban a inyectar: la búsqueda podía
    funcionar (con lo poco que quedaba seteado), pero cualquier descarga
    devolvía la página de login → "sesión expirada" en el primer archivo,
    incluso con cookies recién capturadas. CDP no tiene esa restricción.

    Ver login.py::cargar_sesion() donde se detectó y resolvió el mismo problema.

    Retorna la cantidad de cookies inyectadas correctamente.
    """
    try:
        driver.execute_cdp_cmd('Network.enable', {})
    except Exception as e:
        logger.warning(f"[AUTH_MV] CDP no disponible para inyección de cookies: {e}")

    cargadas = 0
    for cookie in cookies:
        try:
            cdp_cookie = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie.get('domain', ''),
                'path': cookie.get('path', '/'),
                'secure': cookie.get('secure', False),
                'httpOnly': cookie.get('httpOnly', False),
            }
            # Expiración: formato Selenium usa 'expiry', CDP usa 'expires'
            if 'expires' in cookie and cookie['expires'] and cookie['expires'] > 0:
                cdp_cookie['expires'] = cookie['expires']
            elif 'expiry' in cookie and cookie['expiry']:
                cdp_cookie['expires'] = cookie['expiry']
            driver.execute_cdp_cmd('Network.setCookie', cdp_cookie)
            cargadas += 1
        except Exception as e:
            logger.warning(f"[AUTH_MV] No se pudo inyectar cookie '{cookie.get('name', '?')}': {e}")

    logger.info(f"[AUTH_MV] {cargadas}/{len(cookies)} cookie(s) inyectada(s) via CDP")
    return cargadas


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

    # Tomar el mismo permiso de "navegador" que usan las descargas (ver
    # tomar_navegador_directo() en modulos/concurrencia.py): sin esto, este
    # Chrome se creaba sin importar si ya había otro abierto (el de una
    # descarga en curso, por ejemplo) — dos Chrome completos compitiendo
    # por los 512 MB del servidor causaron un OOM real en producción.
    if not gestor.tomar_navegador_directo(timeout=TIMEOUT_ESPERA_NAVEGADOR_LOGIN):
        logger.warning("[AUTH_MV] No se pudo tomar el permiso de navegador (servidor ocupado)")
        return {'estado': 'error', 'mensaje': 'El servidor está ocupado en este momento. Esperá unos segundos e intentá de nuevo.'}

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
            _cerrar_driver_relay(driver)
            logger.info(f"[AUTH_MV] Login exitoso sin 2FA para {mv_usuario}")
            return {'estado': 'ok', 'cookies': cookies, 'mv_usuario': mv_usuario}

        # Verificar si hay mensaje de error en la página (credenciales incorrectas)
        try:
            error_elem = driver.find_element(
                By.CSS_SELECTOR, ".alert-error, #input-error, .kc-feedback-text, [class*='error']"
            )
            mensaje_error = error_elem.text.strip()
            _cerrar_driver_relay(driver)
            logger.warning(f"[AUTH_MV] Credenciales incorrectas: {mensaje_error}")
            return {'estado': 'error', 'mensaje': 'Usuario o contraseña incorrectos'}
        except Exception:
            pass

        # Estado desconocido
        _cerrar_driver_relay(driver)
        logger.error(f"[AUTH_MV] Estado desconocido después de login: {url_actual}")
        return {'estado': 'error', 'mensaje': 'No se pudo completar el login. Intentá de nuevo.'}

    except Exception as e:
        logger.error(f"[AUTH_MV] Error en iniciar_login_mv: {e}", exc_info=True)
        if driver:
            _cerrar_driver_relay(driver)
        else:
            # _crear_driver_headless() falló antes de devolver un driver: el
            # permiso ya se había tomado más arriba, así que hay que
            # soltarlo igual aunque no haya nada que hacerle quit().
            gestor.soltar_navegador_directo()
        if isinstance(e, WebDriverException):
            # No mostrar el stacktrace crudo de Selenium al usuario
            mensaje = 'El servidor está ocupado en este momento. Esperá unos segundos e intentá de nuevo.'
        else:
            mensaje = f'Error al conectar con Mesa Virtual: {str(e)}'
        return {'estado': 'error', 'mensaje': mensaje}


def _recuperar_tras_timeout_2fa(driver, excepcion_original):
    """
    Si el click de "enviar código" o la espera del redirect posterior se
    cuelga, decide si hay que darse por vencido o si alcanza con seguir
    esperando el mismo redirect, en vez de tirar toda la sesión al toque.

    Por qué hace falta: en producción se vio dos veces seguidas que, bajo
    presión de memoria del servidor, un comando puntual de Selenium (el
    click o el chequeo de document.readyState) tarda más de la cuenta y
    tira una excepción — pero el login en Mesa Virtual muchas veces ya se
    había completado igual del lado del navegador. Como completar_login_mv
    borraba la sesión pendiente ante CUALQUIER excepción acá, el usuario
    perdía todo el progreso y tenía que volver a escribir usuario Y
    contraseña, no sólo el código 2FA — de ahí los reportes de "no puedo
    loguearme" cuando en realidad sólo hacía falta esperar un poco más.

    No reclickea el botón: el submit original puede haber llegado igual del
    lado del navegador aunque la respuesta a nuestro comando se haya
    perdido por el camino, y clickear de nuevo arriesga mandar el código
    2FA dos veces. En cambio, hace un sondeo liviano (current_url, sin
    ejecutar JS) para decidir:
      - el driver no responde ni a esto -> está colgado de verdad, se
        relanza la excepción original para que el caller limpie la sesión
        (mismo comportamiento que antes de este cambio).
      - ya llegamos a Mesa Virtual -> el timeout fue sólo del chequeo, no
        hace falta nada más.
      - todavía en Keycloak -> se espera de nuevo el mismo redirect, UNA
        sola vez; si también expira, se deja propagar como antes.
    """
    try:
        url_sondeo = driver.current_url
    except Exception:
        raise excepcion_original

    ya_llego = "mesavirtual.jusentrerios.gov.ar" in url_sondeo and "ol-sso" not in url_sondeo
    if ya_llego:
        logger.info("[AUTH_MV] El timeout fue sólo del chequeo posterior: el login ya había llegado a destino")
        return

    logger.warning("[AUTH_MV] Reintentando una vez la espera del redirect tras el timeout")
    WebDriverWait(driver, TIMEOUT_LOGIN).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


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
        try:
            boton_submit.click()
            # Esperar a que redirija a Mesa Virtual
            time.sleep(3)
            WebDriverWait(driver, TIMEOUT_LOGIN).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception as e_timeout:
            # Un comando puntual se colgó (ver _recuperar_tras_timeout_2fa):
            # antes de tirar la sesión entera, evaluar si alcanza con
            # esperar un poco más.
            logger.warning(f"[AUTH_MV] Timeout tras enviar 2FA, evaluando recuperación: {e_timeout}")
            _recuperar_tras_timeout_2fa(driver, e_timeout)

        url_actual = driver.current_url
        logger.info(f"[AUTH_MV] Después de 2FA: {url_actual[:80]}")

        # Verificar login exitoso
        if ("mesavirtual.jusentrerios.gov.ar" in url_actual and
                "ol-sso" not in url_actual):
            time.sleep(2)  # Dejar que se asienten todas las cookies
            cookies = _capturar_todas_las_cookies(driver)

            # Limpiar el driver de memoria (y soltar el permiso de navegador)
            _cerrar_driver_relay(driver, session_id)

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
        _cerrar_driver_relay(driver, session_id)
        return {'estado': 'error', 'mensaje': 'Error desconocido al verificar el código. Intentá de nuevo.'}

    except Exception as e:
        logger.error(f"[AUTH_MV] Error en completar_login_mv: {e}", exc_info=True)
        # Limpiar driver en caso de error
        _cerrar_driver_relay(driver, session_id)
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

        # Navegar al dominio para tener un contexto donde inyectar cookies
        driver.get(URL_MESA_VIRTUAL)
        time.sleep(1)

        # Inyectar cookies de TODOS los dominios capturados (via CDP: necesario
        # porque las cookies de Keycloak son de otro dominio, ol-sso.jusentrerios.gov.ar)
        _inyectar_cookies_cdp(driver, cookies)

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

        # Navegar al dominio para tener un contexto donde inyectar cookies
        driver.get(URL_MESA_VIRTUAL)
        time.sleep(1)

        # Inyectar cookies de TODOS los dominios (via CDP: ver _inyectar_cookies_cdp)
        _inyectar_cookies_cdp(driver, cookies)

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
