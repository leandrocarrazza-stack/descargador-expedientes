#!/usr/bin/env python3
"""
Tests de la recuperación de timeouts en el login por 2FA (modulos/auth_mv.py).

En producción se vio que, bajo presión de memoria del servidor, un comando
puntual de Selenium (el click de "enviar código" o el chequeo posterior de
document.readyState) podía tardar más de la cuenta y tirar una excepción —
pero el login en Mesa Virtual muchas veces ya se había completado igual del
lado del navegador. Antes de este fix, completar_login_mv() borraba la
sesión pendiente ante CUALQUIER excepción ahí, así que el usuario perdía
todo el progreso y tenía que volver a escribir usuario Y contraseña, no sólo
el código 2FA. _recuperar_tras_timeout_2fa() intenta recuperarse antes de
tirar todo abajo.

No toca la red ni un Chrome real: usa drivers falsos. Corre en cualquier
lado con `python test_auth_mv_2fa.py`.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from selenium.common.exceptions import TimeoutException

import modulos.auth_mv as auth_mv
from modulos.auth_mv import _recuperar_tras_timeout_2fa, completar_login_mv

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


# ═══════════════════════════════════════════════════════════════════════════
#  Dobles de prueba
# ═══════════════════════════════════════════════════════════════════════════

class _DriverSondeo:
    """Driver mínimo para probar _recuperar_tras_timeout_2fa() en aislado."""

    def __init__(self, url=None, falla_current_url=False):
        self._url = url
        self._falla = falla_current_url
        self.llamadas_execute_script = 0
        self._respuesta_execute_script = "complete"

    @property
    def current_url(self):
        if self._falla:
            raise RuntimeError("driver no responde ni a esto")
        return self._url

    def execute_script(self, script):
        self.llamadas_execute_script += 1
        return self._respuesta_execute_script


class _ElementoFalso:
    def __init__(self, lanza_en_click=None):
        self.texto_enviado = None
        self.clicks = 0
        self._lanza_en_click = lanza_en_click

    def clear(self):
        pass

    def send_keys(self, texto):
        self.texto_enviado = texto

    def click(self):
        self.clicks += 1
        if self._lanza_en_click is not None:
            raise self._lanza_en_click


class _DriverCompletarLogin:
    """
    Simula el driver completo durante completar_login_mv(): el campo OTP y
    el botón submit siempre se encuentran; el click del submit lanza la
    excepción configurada (el timeout real visto en producción); current_url
    ya muestra Mesa Virtual desde el sondeo (el submit sí había llegado del
    lado del navegador, sólo la respuesta a nuestro comando se perdió).
    """

    def __init__(self, excepcion_en_click):
        self.otp = _ElementoFalso()
        self.submit = _ElementoFalso(lanza_en_click=excepcion_en_click)
        self.current_url = "https://mesavirtual.jusentrerios.gov.ar/"
        self.cerrado = False

    def find_element(self, by, selector):
        if 'otp' in selector or 'totp' in selector:
            return self.otp
        return self.submit

    def execute_script(self, script):
        return "complete"

    def execute_cdp_cmd(self, cmd, params):
        if cmd == 'Network.getAllCookies':
            return {'cookies': [{'name': 'sid', 'value': 'abc'}]}
        return {}

    def quit(self):
        self.cerrado = True


# ═══════════════════════════════════════════════════════════════════════════
#  1. _recuperar_tras_timeout_2fa() en aislado
# ═══════════════════════════════════════════════════════════════════════════

def test_driver_no_responde_relanza_original():
    print("\n[1] Si el driver no responde ni al sondeo, se relanza la excepción ORIGINAL")
    driver = _DriverSondeo(falla_current_url=True)
    original = RuntimeError("read timeout original")
    try:
        _recuperar_tras_timeout_2fa(driver, original)
        check("debería haber lanzado", False)
    except RuntimeError as e:
        check("relanza exactamente la excepción original (no una nueva)", e is original)


def test_ya_llego_no_reintenta():
    print("\n[2] Si el sondeo muestra que ya llegó a Mesa Virtual, no reintenta el wait")
    driver = _DriverSondeo(url="https://mesavirtual.jusentrerios.gov.ar/expedientes")
    _recuperar_tras_timeout_2fa(driver, RuntimeError("no debería importar"))
    check("no se llama a execute_script si ya había llegado", driver.llamadas_execute_script == 0)


def test_todavia_en_keycloak_reintenta_una_vez():
    print("\n[3] Si todavía está en Keycloak, reintenta el wait UNA vez y no lanza si funciona")
    driver = _DriverSondeo(url="https://ol-sso.jusentrerios.gov.ar/realms/mesavirtual/login-actions/authenticate")
    _recuperar_tras_timeout_2fa(driver, RuntimeError("no debería importar"))
    check("reintenta el wait exactamente una vez", driver.llamadas_execute_script == 1,
          f"se llamó {driver.llamadas_execute_script} vez(veces)")


def test_reintento_tambien_expira_se_propaga():
    print("\n[4] Si el reintento TAMBIÉN expira, se propaga (no queda colgado en silencio)")

    class _DriverColgado:
        current_url = "https://ol-sso.jusentrerios.gov.ar/realms/mesavirtual/login-actions/authenticate"

        def execute_script(self, script):
            return "loading"  # nunca "complete"

    driver = _DriverColgado()
    with patch("modulos.auth_mv.TIMEOUT_LOGIN", 0.2):
        try:
            _recuperar_tras_timeout_2fa(driver, RuntimeError("original"))
            check("debería haber lanzado TimeoutException", False)
        except TimeoutException:
            check("el segundo timeout se propaga como TimeoutException (no la excepción original)", True)
        except Exception as e:
            check(f"excepción inesperada: {type(e).__name__}", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
#  2. completar_login_mv() de punta a punta con el driver falso
# ═══════════════════════════════════════════════════════════════════════════

def test_completar_login_recupera_tras_timeout_en_click():
    print("\n[5] completar_login_mv(): se recupera cuando el click se cuelga pero el login ya había llegado")

    driver = _DriverCompletarLogin(excepcion_en_click=RuntimeError("read timeout simulado"))
    session_id = "sesion-test-1"
    auth_mv._drivers_pendientes[session_id] = {
        'driver': driver, 'timestamp': time.time(), 'mv_usuario': 'testuser',
    }

    with patch("modulos.auth_mv.time.sleep"):
        resultado = completar_login_mv(session_id, "123456")

    check("el botón sólo se clickeó una vez (nunca se reclickea)", driver.submit.clicks == 1)
    check("el código 2FA se escribió en el campo correcto", driver.otp.texto_enviado == "123456")
    check("se recupera y devuelve éxito en vez de tirar la sesión",
          resultado.get('estado') == 'ok', f"resultado={resultado}")
    check("la sesión pendiente se limpió tras el éxito", session_id not in auth_mv._drivers_pendientes)
    check("el driver se cerró", driver.cerrado)


def test_completar_login_sin_sesion_no_rompe():
    print("\n[6] completar_login_mv() con un session_id inexistente sigue devolviendo el error de siempre")
    resultado = completar_login_mv("no-existe-este-id", "123456")
    check("devuelve estado error con mensaje de sesión expirada",
          resultado.get('estado') == 'error' and 'expirada' in resultado.get('mensaje', '').lower(),
          f"resultado={resultado}")


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE RECUPERACIÓN DE TIMEOUT EN LOGIN 2FA")
    print("=" * 70)

    test_driver_no_responde_relanza_original()
    test_ya_llego_no_reintenta()
    test_todavia_en_keycloak_reintenta_una_vez()
    test_reintento_tambien_expira_se_propaga()
    test_completar_login_recupera_tras_timeout_en_click()
    test_completar_login_sin_sesion_no_rompe()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
