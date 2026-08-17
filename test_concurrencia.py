#!/usr/bin/env python3
"""
Tests de modulos/concurrencia.py: la cola FIFO + permisos de navegador y
conversión que reemplazan al semáforo global de 1 sola descarga.

No tocan la red, Selenium ni el filesystem real de cgroups: usan relojes y
lecturas de memoria falsas inyectadas (fn_reloj, fn_memoria), así que corren
en cualquier lado con `python test_concurrencia.py`.

Cubren lo que puede salir mal sin que nadie se entere:
  1. Orden FIFO real con hilos de verdad.
  2. La cola llena recién ahí devuelve error (nadie más recibe 409 antes).
  3. Un job que se cuelga en la cola (timeout) no bloquea a los que siguen.
  4. Los permisos nunca se liberan de más (contadores nunca negativos).
  5. El solape navegador/conversión se comporta distinto prendido y apagado.
  6. La puerta de RAM: bloquea, espera, y hace bypass sólo sin actividad.
  7. ControlJob.nulo() no rompe nada para los llamadores viejos (CLI/MCP).
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, mock_open

sys.path.insert(0, str(Path(__file__).parent))

import modulos.concurrencia as c
from modulos.concurrencia import ControlJob, ErrorColaLlena, ErrorColaTimeout, GestorConcurrencia

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def _gestor_sin_limite_ram(**kwargs):
    """Gestor de prueba con RAM siempre sobrando, para aislar la mecánica de cola."""
    kwargs.setdefault('fn_memoria', lambda: 10_000)
    return GestorConcurrencia(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════
#  1. Orden FIFO con hilos reales
# ═══════════════════════════════════════════════════════════════════════════

def test_fifo_tres_hilos():
    print("\n[1] Orden FIFO con 3 hilos reales")
    g = _gestor_sin_limite_ram(max_navegadores=1)

    # El orden que importa es el de encolar(), no el de arranque de los hilos.
    entradas = [g.encolar(f"job-{i}")[0] for i in range(3)]
    orden_admision = []
    lock_resultado = threading.Lock()

    def trabajar(entrada):
        control = g.esperar_turno(entrada, timeout=5)
        with lock_resultado:
            orden_admision.append(entrada.job_id)
        time.sleep(0.05)  # simula trabajo breve mientras retiene el permiso
        control.liberar_todo()

    hilos = [threading.Thread(target=trabajar, args=(e,)) for e in entradas]
    # Arrancarlos en orden inverso para probar que el orden de encolar() manda,
    # no el orden en que los hilos efectivamente llaman a esperar_turno().
    for h in reversed(hilos):
        h.start()
    for h in hilos:
        h.join(timeout=5)

    check("los 3 hilos terminaron", all(not h.is_alive() for h in hilos))
    check("se admitieron en el orden en que se encolaron",
          orden_admision == ["job-0", "job-1", "job-2"], f"orden={orden_admision}")
    check("no queda nadie en la cola", g.estado()['cola'] == 0)
    check("el permiso de navegador quedó liberado", g.estado()['navegadores_en_uso'] == 0)


# ═══════════════════════════════════════════════════════════════════════════
#  2. Cola llena
# ═══════════════════════════════════════════════════════════════════════════

def test_cola_llena():
    print("\n[2] La cola llena recién ahí rechaza (nadie más recibe 409 antes)")
    g = _gestor_sin_limite_ram(max_navegadores=1, max_cola=2)

    g.encolar("a")
    g.encolar("b")
    try:
        g.encolar("c")
        check("la 3ra encolada con max_cola=2 debería fallar", False)
    except ErrorColaLlena:
        check("la 3ra encolada lanza ErrorColaLlena", True)


# ═══════════════════════════════════════════════════════════════════════════
#  3. Posición publicada al avanzar la cola
# ═══════════════════════════════════════════════════════════════════════════

def test_posicion_publicada():
    print("\n[3] La posición se publica y se actualiza al avanzar la cola")
    g = _gestor_sin_limite_ram(max_navegadores=1)

    ea, _ = g.encolar("a")
    ca = g.esperar_turno(ea, timeout=2)  # sola en la cola, se admite al instante

    eb, puesto_inicial = g.encolar("b")
    check("b entra en la posición 1 (a ya fue admitida y desencolada)", puesto_inicial == 1)

    posiciones_vistas = []
    resultado = {}

    def esperar_b():
        resultado['control'] = g.esperar_turno(eb, on_posicion=posiciones_vistas.append, timeout=3)

    hilo = threading.Thread(target=esperar_b)
    hilo.start()
    time.sleep(0.1)  # darle tiempo a que el hilo entre al loop y publique la 1ra posición
    check("mientras a sigue activa, b ve la posición 1", posiciones_vistas == [1], f"{posiciones_vistas}")

    ca.liberar_todo()  # libera a -> notify_all -> b debería engancharse casi al instante
    hilo.join(timeout=2)

    check("el hilo de b terminó (no quedó colgado)", not hilo.is_alive())
    check("b fue admitida", resultado.get('control') is not None)
    resultado['control'].liberar_todo()


# ═══════════════════════════════════════════════════════════════════════════
#  4. Timeout de un job no bloquea a los que siguen
# ═══════════════════════════════════════════════════════════════════════════

def test_timeout_no_bloquea_a_los_demas():
    print("\n[4] Un job que se cuelga en la cola no traba a los que siguen")
    g = _gestor_sin_limite_ram(max_navegadores=1)

    ea, _ = g.encolar("a")
    ca = g.esperar_turno(ea, timeout=2)  # a se queda con el único permiso de navegador para siempre

    eb, _ = g.encolar("b")
    try:
        g.esperar_turno(eb, timeout=0.2)
        check("b debería haber expirado (a nunca libera)", False)
    except ErrorColaTimeout:
        check("b expira con ErrorColaTimeout tras su deadline", True)

    check("b ya no está en la cola tras expirar (el finally la sacó)", g.estado()['cola'] == 0)

    ec, puesto_c = g.encolar("c")
    check("c entra en la posición 1 (b ya no cuenta)", puesto_c == 1)

    ca.liberar_todo()
    cc = g.esperar_turno(ec, timeout=2)
    check("c es admitida en cuanto a libera, sin que el timeout de b la haya afectado",
          cc is not None)
    cc.liberar_todo()


# ═══════════════════════════════════════════════════════════════════════════
#  5. Los permisos nunca se liberan de más
# ═══════════════════════════════════════════════════════════════════════════

def test_no_sobre_libera():
    print("\n[5] Liberar dos veces no deja contadores negativos")
    g = _gestor_sin_limite_ram(max_navegadores=1, solape_navegador_conversion=True)

    entrada, _ = g.encolar("x")
    control = g.esperar_turno(entrada, timeout=2)
    check("navegador tomado", g.estado()['navegadores_en_uso'] == 1)

    control.liberar_navegador()
    check("navegador liberado", g.estado()['navegadores_en_uso'] == 0)
    control.liberar_navegador()  # de nuevo
    check("liberar_navegador() dos veces no baja de 0", g.estado()['navegadores_en_uso'] == 0)

    ok1 = control.adquirir_conversion(timeout=1)
    ok2 = control.adquirir_conversion(timeout=1)  # idempotente, ya la tiene
    check("adquirir_conversion() es idempotente", ok1 and ok2 and g.estado()['conversiones_en_uso'] == 1)

    control.liberar_todo()
    control.liberar_todo()  # de nuevo
    estado = g.estado()
    check("liberar_todo() dos veces no deja contadores negativos ni de más",
          estado['navegadores_en_uso'] == 0 and estado['conversiones_en_uso'] == 0,
          f"{estado}")


# ═══════════════════════════════════════════════════════════════════════════
#  6. Solape navegador/conversión: ON vs OFF
# ═══════════════════════════════════════════════════════════════════════════

def test_solape_on_permite_handoff():
    print("\n[6] Con SOLAPE=True, B arranca su navegador mientras A sigue convirtiendo")
    g = _gestor_sin_limite_ram(max_navegadores=1, solape_navegador_conversion=True)

    ea, _ = g.encolar("a")
    ca = g.esperar_turno(ea, timeout=2)
    ca.liberar_navegador()
    check("liberar_navegador() libera de verdad con solape ON",
          g.estado()['navegadores_en_uso'] == 0)
    check("A consigue el permiso de conversión", ca.adquirir_conversion(timeout=1))

    eb, _ = g.encolar("b")
    cb = g.esperar_turno(eb, timeout=1)
    check("B es admitida (navegador libre) mientras A sigue en conversión", cb is not None)

    ok_b_conversion = cb.adquirir_conversion(timeout=0.3)
    check("la conversión sigue siendo EXCLUSIVA aunque el navegador se haya solapado",
          not ok_b_conversion)

    ca.liberar_todo()
    cb.liberar_todo()


def test_solape_off_serializa_como_antes():
    print("\n[7] Con SOLAPE=False (default), B espera a que A termine TODO")
    g = _gestor_sin_limite_ram(max_navegadores=1, solape_navegador_conversion=False)

    ea, _ = g.encolar("a")
    ca = g.esperar_turno(ea, timeout=2)
    ca.liberar_navegador()  # debe ser no-op
    check("liberar_navegador() es no-op con solape OFF",
          g.estado()['navegadores_en_uso'] == 1)

    eb, _ = g.encolar("b")
    try:
        g.esperar_turno(eb, timeout=0.3)
        check("con solape OFF, b no debería entrar todavía", False)
    except ErrorColaTimeout:
        check("b espera (timeout) mientras a no libere todo", True)

    ca.liberar_todo()

    ec, _ = g.encolar("c")
    cc = g.esperar_turno(ec, timeout=2)
    check("tras liberar_todo() de a, el siguiente job entra normalmente", cc is not None)
    cc.liberar_todo()


# ═══════════════════════════════════════════════════════════════════════════
#  7. Puerta de RAM
# ═══════════════════════════════════════════════════════════════════════════

def test_puerta_ram():
    print("\n[8] Puerta de RAM: bloquea con actividad, hace bypass sin ella")
    reloj = {'t': 0.0}
    memoria = {'mb': 500}

    g = GestorConcurrencia(max_navegadores=1, umbral_ram_mb=200,
                            espera_sin_actividad_seg=30,
                            fn_memoria=lambda: memoria['mb'],
                            fn_reloj=lambda: reloj['t'])

    check("con RAM sobre el umbral, admite", g._puede_admitir_ram() is True)

    memoria['mb'] = 50  # cae bajo el umbral
    g._navegadores_en_uso = 1  # hay actividad real (otro job con Chrome abierto)
    check("bajo el umbral CON actividad, no admite", g._puede_admitir_ram() is False)
    reloj['t'] = 1000
    check("...ni aunque pase mucho tiempo mientras hay actividad", g._puede_admitir_ram() is False)

    g._navegadores_en_uso = 0
    reloj['t'] = 0.0
    g._sin_ram_desde = None
    check("bajo el umbral SIN actividad, todavía no (recién arranca la ventana)",
          g._puede_admitir_ram() is False)
    reloj['t'] = 29
    check("a los 29s sin actividad, todavía no", g._puede_admitir_ram() is False)
    reloj['t'] = 31
    check("a los 31s sin actividad, hace bypass", g._puede_admitir_ram() is True)

    memoria['mb'] = None
    check("sin lectura de cgroup (None), siempre admite (no se puede aplicar la puerta)",
          g._puede_admitir_ram() is True)


def test_memoria_para_admision_suma_cache():
    print("\n[9] memoria_para_admision_mb() corrige el page cache recuperable")

    with patch.object(c, 'memoria_disponible_mb', return_value=100):
        contenido = "anon 1000\ninactive_file 52428800\nactive_file 1000\n"  # 52428800 B = 50 MB
        with patch('builtins.open', mock_open(read_data=contenido)):
            resultado = c.memoria_para_admision_mb()
        check("suma inactive_file (50MB) al valor crudo (100MB)", resultado == 150, f"={resultado}")

    with patch.object(c, 'memoria_disponible_mb', return_value=100):
        with patch('builtins.open', side_effect=FileNotFoundError):
            resultado = c.memoria_para_admision_mb()
        check("sin memory.stat legible, devuelve el valor crudo sin sumar nada",
              resultado == 100, f"={resultado}")

    with patch.object(c, 'memoria_disponible_mb', return_value=None):
        resultado = c.memoria_para_admision_mb()
        check("si la lectura cruda no pudo leer nada, propaga None", resultado is None)


# ═══════════════════════════════════════════════════════════════════════════
#  8. ControlJob.nulo() — compatibilidad con CLI/MCP/Celery legacy
# ═══════════════════════════════════════════════════════════════════════════

def test_control_nulo():
    print("\n[10] ControlJob.nulo() no bloquea ni rompe nada")
    nulo = ControlJob.nulo()

    check("es_nulo es True", nulo.es_nulo is True)
    check("adquirir_conversion() siempre da True (no hay cola con la que competir)",
          nulo.adquirir_conversion() is True)
    check("permite_matar_soffice() es True (proceso propio, sin concurrencia)",
          nulo.permite_matar_soffice() is True)

    try:
        nulo.liberar_navegador()
        nulo.liberar_todo()
        ok = True
    except Exception as e:
        ok = False
        print(f"      excepción inesperada: {e}")
    check("liberar_navegador()/liberar_todo() no lanzan", ok)

    check("nulo() siempre devuelve el mismo singleton", ControlJob.nulo() is nulo)


# ═══════════════════════════════════════════════════════════════════════════
#  9. abandonar() — el thread nunca llegó a arrancar
# ═══════════════════════════════════════════════════════════════════════════

def test_abandonar():
    print("\n[11] abandonar() saca la entrada sin dejar rastro")
    g = _gestor_sin_limite_ram(max_navegadores=1)

    entrada, _ = g.encolar("x")
    check("hay 1 en cola", g.estado()['cola'] == 1)
    g.abandonar(entrada)
    check("tras abandonar(), la cola queda vacía", g.estado()['cola'] == 0)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE CONCURRENCIA (cola FIFO + permisos de navegador/conversión)")
    print("=" * 70)

    test_fifo_tres_hilos()
    test_cola_llena()
    test_posicion_publicada()
    test_timeout_no_bloquea_a_los_demas()
    test_no_sobre_libera()
    test_solape_on_permite_handoff()
    test_solape_off_serializa_como_antes()
    test_puerta_ram()
    test_memoria_para_admision_suma_cache()
    test_control_nulo()
    test_abandonar()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
