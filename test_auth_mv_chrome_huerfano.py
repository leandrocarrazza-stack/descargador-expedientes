#!/usr/bin/env python3
"""
Tests de limpiar_chrome_huerfano() en modulos/auth_mv.py.

En producción se vio un chromedriver quedarse completamente colgado (un
comando agotó los 3 reintentos automáticos de Selenium, ~4.5 minutos en
total antes de tirar la excepción) — y como driver.quit() en el bloque
except también depende de poder hablarle a ESE MISMO chromedriver por HTTP,
el proceso quedó vivo como zombie consumiendo RAM bien después de que el
usuario ya había visto el error de "sesión expirada". limpiar_chrome_huerfano()
barre procesos chrome/chromedriver que llevan vivos más de la cuenta.

No necesita un Chrome real instalado: lanza procesos de Python cuyo argv[0]
es exactamente "chromedriver"/"chrome" (subprocess.Popen(['chromedriver',
...], executable=sys.executable) — el mismo truco que usa cualquier binario
que quiera controlar su propio argv[0]), así /proc/<pid>/cmdline los
identifica igual que a un Chrome real, sin depender de que esté instalado.

Corre en cualquier lado con `python test_auth_mv_chrome_huerfano.py`.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modulos.auth_mv import limpiar_chrome_huerfano

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _lanzar_proceso_falso(nombre: str) -> subprocess.Popen:
    """
    Proceso de mentira cuyo argv[0] (lo que ve /proc/<pid>/cmdline) es
    EXACTAMENTE `nombre` -así limpiar_chrome_huerfano() lo reconoce sin
    necesitar un Chrome real instalado en este entorno. Duerme 120s; cada
    test lo mata a mano en su finally.
    """
    return subprocess.Popen([nombre, '-c', 'import time; time.sleep(120)'], executable=sys.executable)


def _sigue_vivo(proceso: subprocess.Popen) -> bool:
    return proceso.poll() is None


def _matar_si_sigue_vivo(proceso: subprocess.Popen):
    if _sigue_vivo(proceso):
        proceso.kill()
    proceso.wait()


# ═══════════════════════════════════════════════════════════════════════════

def test_no_toca_procesos_jovenes():
    print("\n[1] No mata un chromedriver recién arrancado (podría estar en uso de verdad)")
    proceso = _lanzar_proceso_falso("chromedriver")
    try:
        eliminados = limpiar_chrome_huerfano(edad_maxima_seg=60)
        check("no cuenta ningún eliminado", eliminados == 0, f"eliminó {eliminados}")
        check("el proceso sigue vivo", _sigue_vivo(proceso))
    finally:
        _matar_si_sigue_vivo(proceso)


def test_mata_procesos_viejos():
    print("\n[2] Mata chromedriver Y chrome que superaron la edad máxima")
    p_driver = _lanzar_proceso_falso("chromedriver")
    p_chrome = _lanzar_proceso_falso("chrome")
    try:
        # Reloj falso: "ahora" queda 1000s en el futuro -> cualquier proceso
        # real (arrancado hace milisegundos) tiene edad ~1000s, muy por
        # encima del umbral. Evita depender de esperar minutos de verdad.
        eliminados = limpiar_chrome_huerfano(edad_maxima_seg=60, fn_reloj=lambda: time.time() + 1000)
        check("elimina los 2 procesos viejos", eliminados == 2, f"eliminó {eliminados}")

        time.sleep(0.3)  # darle al kernel un instante para procesar el SIGKILL
        check("chromedriver ya no está vivo", not _sigue_vivo(p_driver))
        check("chrome ya no está vivo", not _sigue_vivo(p_chrome))
    finally:
        _matar_si_sigue_vivo(p_driver)
        _matar_si_sigue_vivo(p_chrome)


def test_no_toca_procesos_no_relacionados():
    print("\n[3] No toca procesos que no se llamen EXACTAMENTE chrome/chromedriver")
    # El nombre incluye la palabra "chrome" a propósito: no debe alcanzar
    # para que lo mate -mismo criterio anti-substring que
    # matar_procesos_soffice() en modulos/conversion.py.
    proceso = _lanzar_proceso_falso("mi_script_con_chrome_en_el_nombre")
    try:
        eliminados = limpiar_chrome_huerfano(edad_maxima_seg=60, fn_reloj=lambda: time.time() + 1000)
        check("no lo cuenta como eliminado", eliminados == 0, f"eliminó {eliminados}")
        check("sigue vivo", _sigue_vivo(proceso))
    finally:
        _matar_si_sigue_vivo(proceso)


def test_edad_maxima_por_defecto_es_generosa():
    print("\n[4] El umbral por defecto deja margen por encima de la espera legítima más larga")
    from modulos.auth_mv import EDAD_MAXIMA_CHROME_SEG, TIMEOUT_SESION_RELAY
    check("EDAD_MAXIMA_CHROME_SEG > TIMEOUT_SESION_RELAY (nunca mata un 2FA en curso)",
          EDAD_MAXIMA_CHROME_SEG > TIMEOUT_SESION_RELAY,
          f"EDAD_MAXIMA_CHROME_SEG={EDAD_MAXIMA_CHROME_SEG} TIMEOUT_SESION_RELAY={TIMEOUT_SESION_RELAY}")


def test_no_lanza_si_proc_no_esta():
    print("\n[5] No lanza excepción aunque /proc tenga entradas efímeras (condición de carrera normal)")
    # No se puede forzar la carrera real fácilmente, pero sí confirmar que
    # una corrida normal sobre el /proc real de este sistema no explota.
    try:
        limpiar_chrome_huerfano()
        ok = True
    except Exception as e:
        ok = False
        print(f"      excepción inesperada: {e}")
    check("corre sobre el /proc real sin lanzar", ok)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE LIMPIEZA DE CHROME HUÉRFANO")
    print("=" * 70)

    test_no_toca_procesos_jovenes()
    test_mata_procesos_viejos()
    test_no_toca_procesos_no_relacionados()
    test_edad_maxima_por_defecto_es_generosa()
    test_no_lanza_si_proc_no_esta()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
