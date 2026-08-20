"""
MÓDULO: Concurrencia de descargas
==================================

Reemplaza el semáforo global de 1 sola descarga (que rechazaba con HTTP 409
a cualquier segundo usuario) por una cola FIFO con posición visible, más dos
permisos que protegen la RAM del proceso:

- **permiso de navegador**: cuántos Chrome pueden estar abiertos a la vez
  (MAX_NAVEGADORES, hoy 1 — dos Chromes en simultáneo en un plan de 512 MB
  son un candidato real a OOM que tumba la instancia para todos).
- **permiso de conversión**: cubre PASO 4 (LibreOffice) + PASO 5 (merge de
  PyPDF2) de UN SOLO job a la vez, sin importar MAX_NAVEGADORES. Es el par
  de RAM peligroso opuesto: soffice de un job + merge de PyPDF2 de otro.

Como el pipeline ya cierra Chrome antes de convertir (ver
`modulos/pipeline.py`, "[CLEANUP] Cerrando navegador"), un job puede soltar
su permiso de navegador apenas cierra Chrome, dejando que el siguiente
arranque el suyo mientras el primero sigue conviertiendo/unificando SIN
Chrome abierto. Esa liberación temprana es opcional: se activa con
SOLAPE_NAVEGADOR_CONVERSION=true. Apagada (default), el comportamiento es
el de antes — un solo job de punta a punta — pero SIN el 409: los demás
esperan en cola en vez de ser rechazados.

Todo el estado vive en memoria de proceso (gunicorn corre con 1 solo
worker: ver Dockerfile). Si la app se reinicia con jobs en cola, esos jobs
desaparecen — el frontend lo detecta como un 404 en el polling y lo
muestra como "la sesión expiró", igual que ya hace con cualquier otro job
perdido por reinicio.
"""

import os
import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from modulos.logger import crear_logger
from modulos.conversion import memoria_disponible_mb

logger = crear_logger(__name__)

# ── Configuración por variable de entorno ──────────────────────────────────
MAX_NAVEGADORES = int(os.environ.get('MAX_NAVEGADORES', '1'))
MAX_COLA_DESCARGAS = int(os.environ.get('MAX_COLA_DESCARGAS', '10'))
# Bajado de 220 a 150 con evidencia real de producción: el 19/8 se midió
# "libre=208MB" en un momento sin NINGUNA otra actividad (ni navegador ni
# conversión en curso), y esa misma descarga arrancó y terminó bien sin
# ningún síntoma de falta de memoria. 220 estaba calibrado por encima de la
# memoria libre real que este servidor tiene en reposo, así que la puerta
# terminaba frenando 30s (ESPERA_RAM_SIN_ACTIVIDAD_SEG) TODAS las descargas,
# incluso siendo el único usuario. 150 deja margen de sobra contra un OOM
# real sin forzar esa espera innecesaria.
UMBRAL_RAM_NAVEGADOR_MB = int(os.environ.get('UMBRAL_RAM_NAVEGADOR_MB', '150'))
MAX_ESPERA_COLA_SEG = int(os.environ.get('MAX_ESPERA_COLA_SEG', '900'))
ESPERA_RAM_SIN_ACTIVIDAD_SEG = int(os.environ.get('ESPERA_RAM_SIN_ACTIVIDAD_SEG', '30'))
SOLAPE_NAVEGADOR_CONVERSION = os.environ.get('SOLAPE_NAVEGADOR_CONVERSION', 'false').lower() == 'true'

_INTERVALO_DESPERTAR_SEG = 3


class ErrorColaLlena(Exception):
    """La cola alcanzó MAX_COLA_DESCARGAS. Recién acá corresponde un 409."""


class ErrorColaTimeout(Exception):
    """El job esperó en cola más de lo permitido sin ser admitido."""


def memoria_para_admision_mb() -> Optional[int]:
    """
    Como memoria_disponible_mb() (modulos/conversion.py) pero corrigiendo
    el page cache recuperable.

    `memory.current` de cgroup v2 incluye páginas de cache de archivos que
    el kernel libera bajo presión sin necesidad de un OOM-kill — es la
    misma corrección de "working set" que hacen cAdvisor/Kubernetes al
    restar el cache reclamable del uso crudo. Tras el PASO 3 (decenas de
    archivos escritos a disco), la lectura cruda SUBESTIMA cuánta RAM hay
    realmente disponible para el próximo Chrome. Se le suma de vuelta
    `inactive_file` (v2) / `total_inactive_file` (v1) de memory.stat; si no
    se puede leer ese archivo, se usa el valor crudo tal cual — subestimar
    es seguro (en el peor caso, se espera de más), nunca al revés.
    """
    base = memoria_disponible_mb()
    if base is None:
        return None

    extra_mb = 0
    try:
        with open('/sys/fs/cgroup/memory.stat') as f:
            for linea in f:
                if linea.startswith('inactive_file '):
                    extra_mb = int(linea.split()[1]) // (1024 * 1024)
                    break
    except Exception:
        try:
            with open('/sys/fs/cgroup/memory/memory.stat') as f:
                for linea in f:
                    if linea.startswith('total_inactive_file '):
                        extra_mb = int(linea.split()[1]) // (1024 * 1024)
                        break
        except Exception:
            extra_mb = 0

    return base + extra_mb


@dataclass(eq=False)  # identidad, no igualdad por campos: cada encolada() es única
class EntradaCola:
    job_id: str
    creado: float


class _ControlJobNulo:
    """
    ControlJob de no-operación para callers fuera del flujo web (CLI, MCP,
    Celery legacy en modulos/tasks.py) que nunca pasaron por la cola.
    Nunca bloquea ni toca contadores. `permite_matar_soffice()` es True
    porque sin gestor de por medio no hay otro job con el que pueda chocar
    — esos caminos corren en su propio proceso, sin concurrencia real.
    """
    es_nulo = True

    def liberar_navegador(self) -> None:
        pass

    def adquirir_conversion(self, timeout: float = 600) -> bool:
        return True

    def permite_matar_soffice(self) -> bool:
        return True

    def nadie_mas_convirtiendo(self) -> bool:
        return True

    def liberar_todo(self) -> None:
        pass


_CONTROL_NULO = _ControlJobNulo()


class ControlJob:
    """
    Handle de los permisos de concurrencia de UN job, devuelto por
    `GestorConcurrencia.esperar_turno()` al ser admitido.

    Cada liberación es idempotente: da igual cuántas veces se llame (el
    punto de cierre normal de Chrome y el backstop del finally del
    pipeline llaman a lo mismo) — el contador del gestor nunca baja de más.
    Pensado para un solo hilo por instancia (el hilo del pipeline); no hace
    falta que sea thread-safe entre sí mismo llamado desde dos hilos a la
    vez, pero el Lock interno no cuesta nada y evita sorpresas.
    """

    def __init__(self, gestor: 'GestorConcurrencia'):
        self._gestor = gestor
        self._solape = gestor.solape_navegador_conversion
        self._lock = threading.Lock()
        self._navegador_liberado = False
        self._conversion_tomada = False
        self._conversion_liberada = False
        self._todo_liberado = False

    @property
    def es_nulo(self) -> bool:
        return False

    def liberar_navegador(self) -> None:
        """
        Suelta el permiso de navegador apenas Chrome se cierra (pipeline.py,
        justo después de "[CLEANUP] Cerrando navegador"), para que el
        siguiente job de la cola pueda arrancar el suyo mientras éste sigue
        en conversión/unificación.

        Con SOLAPE_NAVEGADOR_CONVERSION=false (default) esto es un no-op: el
        permiso se retiene hasta liberar_todo(), igual que el semáforo
        global de antes — nadie más arranca Chrome hasta que ESTE job
        termine por completo.
        """
        with self._lock:
            if self._navegador_liberado or self._todo_liberado or not self._solape:
                return
            self._navegador_liberado = True
        self._gestor._liberar_navegador()

    def adquirir_conversion(self, timeout: float = 600) -> bool:
        """
        Toma el permiso exclusivo de conversión+unificación (cap 1: sólo un
        job puede tener soffice/PyPDF2 en vuelo a la vez). Bloquea hasta
        `timeout` segundos; False si no se pudo conseguir a tiempo (el
        caller debe tratarlo como "servidor ocupado", no reintentar solo).
        """
        with self._lock:
            if self._conversion_tomada:
                return True
        ok = self._gestor._adquirir_conversion(timeout=timeout)
        if ok:
            with self._lock:
                self._conversion_tomada = True
        return ok

    def permite_matar_soffice(self) -> bool:
        """
        True sólo si este job tiene el permiso de conversión exclusivo
        tomado: en ese caso matar TODOS los soffice del contenedor es
        seguro, porque no puede haber otro job con soffice en vuelo.
        """
        with self._lock:
            return self._conversion_tomada and not self._conversion_liberada

    def nadie_mas_convirtiendo(self) -> bool:
        """
        True si AHORA MISMO ningún job del proceso (ni siquiera otro que
        no sea éste) tiene el permiso de conversión tomado — seguro para
        matar TODOS los soffice.bin del sistema sin arriesgar la conversión
        de otro job en vuelo. A diferencia de permite_matar_soffice() (que
        mira si ESTE job tiene el permiso), esto se usa ANTES de pedirlo,
        típicamente al arrancar un pipeline nuevo, para limpiar un
        soffice.bin huérfano que haya quedado de un job anterior que
        crasheó (excepción, OOM-kill) sin pasar por su propio cleanup.

        Ventana de carrera pequeña e intencional (mismo trade-off que
        permite_matar_soffice): entre este chequeo y el matar_procesos_soffice()
        del caller, otro job podría tomar el permiso y arrancar su soffice
        justo a tiempo de ser matado. Se acepta por ser un best-effort de
        limpieza, no una garantía dura.
        """
        return self._gestor.nadie_convirtiendo()

    def liberar_todo(self) -> None:
        """
        Backstop idempotente: suelta lo que quede tomado (navegador y/o
        conversión). Se llama siempre en el finally del pipeline, sin
        importar por qué camino salió (éxito, error, excepción, timeout de
        cola nunca llegó a admitirse — ver ControlJob.nulo() para ese caso).
        """
        with self._lock:
            if self._todo_liberado:
                return
            navegador_pendiente = not self._navegador_liberado
            conversion_pendiente = self._conversion_tomada and not self._conversion_liberada
            self._navegador_liberado = True
            self._conversion_liberada = True
            self._todo_liberado = True
        if navegador_pendiente:
            self._gestor._liberar_navegador()
        if conversion_pendiente:
            self._gestor._liberar_conversion()

    @staticmethod
    def nulo() -> '_ControlJobNulo':
        return _CONTROL_NULO


class GestorConcurrencia:
    """
    Cola FIFO de descargas + los permisos de navegador/conversión.

    Un solo `threading.Condition` protege TODO el estado (cola y
    contadores): así "soy cabeza de cola Y hay cupo de navegador Y hay RAM
    libre" se decide de forma atómica, sin ventana de carrera entre
    chequear y reservar. Un Semaphore aparte no alcanzaría para eso.

    Pensado como singleton de proceso (gunicorn corre con 1 solo worker:
    ver Dockerfile y el comentario junto a `_jobs` en rutas/descargas.py).
    Si algún día se sube a más de un worker, cada uno tendría su propia
    cola y los límites dejarían de ser globales a la instancia.
    """

    def __init__(self,
                 max_navegadores: int = MAX_NAVEGADORES,
                 max_cola: int = MAX_COLA_DESCARGAS,
                 umbral_ram_mb: int = UMBRAL_RAM_NAVEGADOR_MB,
                 espera_sin_actividad_seg: float = ESPERA_RAM_SIN_ACTIVIDAD_SEG,
                 solape_navegador_conversion: bool = SOLAPE_NAVEGADOR_CONVERSION,
                 fn_memoria: Callable[[], Optional[int]] = memoria_para_admision_mb,
                 fn_reloj: Callable[[], float] = time.monotonic):
        self._max_navegadores = max_navegadores
        self._max_cola = max_cola
        self._umbral_ram_mb = umbral_ram_mb
        self._espera_sin_actividad_seg = espera_sin_actividad_seg
        self.solape_navegador_conversion = solape_navegador_conversion
        self._fn_memoria = fn_memoria
        self._fn_reloj = fn_reloj

        self._cond = threading.Condition()
        self._cola: deque = deque()
        self._navegadores_en_uso = 0
        self._conversiones_en_uso = 0
        self._sin_ram_desde: Optional[float] = None  # primer rechazo de RAM sin actividad interna

    # ── Cola ─────────────────────────────────────────────────────────────

    def encolar(self, job_id: str):
        """Sincrónico, llamado desde el POST. Devuelve (entrada, puesto) o levanta ErrorColaLlena."""
        with self._cond:
            if len(self._cola) >= self._max_cola:
                raise ErrorColaLlena(f"Cola llena ({self._max_cola} descargas esperando)")
            entrada = EntradaCola(job_id=job_id, creado=self._fn_reloj())
            self._cola.append(entrada)
            puesto = len(self._cola)
        return entrada, puesto

    def abandonar(self, entrada: EntradaCola) -> None:
        """El thread del job nunca llegó a arrancar (espejo del t.start() fallido)."""
        with self._cond:
            try:
                self._cola.remove(entrada)
            except ValueError:
                pass
            self._cond.notify_all()

    def esperar_turno(self, entrada: EntradaCola,
                       on_posicion: Optional[Callable[[int], None]] = None,
                       timeout: float = MAX_ESPERA_COLA_SEG) -> ControlJob:
        """
        Bloquea hasta que `entrada` sea cabeza de cola Y haya cupo de
        navegador Y haya RAM suficiente (o el bypass de "sin actividad" lo
        permita). Despierta cada pocos segundos para poder publicar la
        posición aunque nada haya cambiado todavía.

        `on_posicion(puesto)` se llama con el lock tomado — debe ser
        rápido y no debe lanzar de forma que rompa el loop (se atrapa
        cualquier excepción igual, por las dudas).

        El try/finally SIEMPRE remueve la entrada de la cola al salir, sea
        cual sea el motivo (admitido, timeout, o una excepción del propio
        callback que se escapó): así un thread que muere a mitad de espera
        nunca deja bloqueados a los que siguen.
        """
        deadline = self._fn_reloj() + timeout
        ultimo_puesto = None
        try:
            with self._cond:
                while True:
                    if entrada not in self._cola:
                        raise ErrorColaTimeout(
                            f"Job {entrada.job_id[:8]}: entrada de cola ya no existe "
                            "(barrida por timeout externo o abandonada)"
                        )

                    puesto = self._cola.index(entrada) + 1
                    if puesto != ultimo_puesto:
                        ultimo_puesto = puesto
                        if on_posicion:
                            try:
                                on_posicion(puesto)
                            except Exception:
                                logger.debug("callback de posición de cola falló (ignorado)", exc_info=True)

                    if (self._cola[0] is entrada
                            and self._navegadores_en_uso < self._max_navegadores
                            and self._puede_admitir_ram()):
                        self._navegadores_en_uso += 1
                        self._cola.popleft()
                        self._sin_ram_desde = None
                        self._cond.notify_all()
                        logger.info(
                            f"[COLA] job={entrada.job_id[:8]} admitido tras "
                            f"{self._fn_reloj() - entrada.creado:.1f}s"
                        )
                        return ControlJob(self)

                    restante = deadline - self._fn_reloj()
                    if restante <= 0:
                        raise ErrorColaTimeout(
                            f"Job {entrada.job_id[:8]} esperó más de {timeout:.0f}s en cola"
                        )
                    self._cond.wait(min(_INTERVALO_DESPERTAR_SEG, restante))
        finally:
            with self._cond:
                try:
                    self._cola.remove(entrada)  # no-op si ya se sacó al admitir
                except ValueError:
                    pass
                self._cond.notify_all()

    def _puede_admitir_ram(self) -> bool:
        """
        Se asume llamado con self._cond ya tomado, y sólo para la cabeza
        de cola (ver esperar_turno). True si hay RAM libre suficiente, o
        si la RAM es lo ÚNICO que bloquea y no hay ningún navegador ni
        conversión en curso desde hace ESPERA_RAM_SIN_ACTIVIDAD_SEG (la
        lectura puede estar viciada por cache, o el consumidor real es el
        Chrome del relay de 2FA que vive fuera de este gestor hasta 180s —
        ver auth_mv.py). Con actividad real en curso, nunca hay bypass: se
        espera hasta el deadline de la cola.
        """
        libre = self._fn_memoria()
        if libre is None:
            return True  # sin cgroup (ej. desarrollo local): no se puede aplicar la puerta

        if libre >= self._umbral_ram_mb:
            self._sin_ram_desde = None
            return True

        hay_actividad = self._navegadores_en_uso > 0 or self._conversiones_en_uso > 0
        if hay_actividad:
            self._sin_ram_desde = None
            logger.debug(
                f"[ADMISION] libre={libre}MB umbral={self._umbral_ram_mb} "
                f"naveg={self._navegadores_en_uso}/{self._max_navegadores} "
                f"conv={self._conversiones_en_uso} decision=esperar"
            )
            return False

        ahora = self._fn_reloj()
        if self._sin_ram_desde is None:
            self._sin_ram_desde = ahora
            return False

        if ahora - self._sin_ram_desde >= self._espera_sin_actividad_seg:
            logger.info(
                f"[ADMISION] libre={libre}MB umbral={self._umbral_ram_mb} decision=bypass "
                f"(sin actividad hace {ahora - self._sin_ram_desde:.0f}s, posible cache viciado)"
            )
            return True

        return False

    # ── Permisos (llamados sólo desde ControlJob) ──────────────────────

    def _liberar_navegador(self) -> None:
        with self._cond:
            if self._navegadores_en_uso > 0:
                self._navegadores_en_uso -= 1
            self._cond.notify_all()

    def _adquirir_conversion(self, timeout: float = 600) -> bool:
        deadline = self._fn_reloj() + timeout
        with self._cond:
            while self._conversiones_en_uso > 0:
                restante = deadline - self._fn_reloj()
                if restante <= 0:
                    return False
                self._cond.wait(min(_INTERVALO_DESPERTAR_SEG, restante))
            self._conversiones_en_uso += 1
            return True

    def _liberar_conversion(self) -> None:
        with self._cond:
            if self._conversiones_en_uso > 0:
                self._conversiones_en_uso -= 1
            self._cond.notify_all()

    # ── Introspección (logs, tests) ─────────────────────────────────────

    def hay_actividad(self) -> bool:
        with self._cond:
            return self._navegadores_en_uso > 0 or self._conversiones_en_uso > 0

    def nadie_convirtiendo(self) -> bool:
        with self._cond:
            return self._conversiones_en_uso == 0

    def estado(self) -> dict:
        with self._cond:
            return {
                'cola': len(self._cola),
                'navegadores_en_uso': self._navegadores_en_uso,
                'max_navegadores': self._max_navegadores,
                'conversiones_en_uso': self._conversiones_en_uso,
            }


# Singleton de proceso: gunicorn corre con 1 worker, así que este estado es
# compartido por todos los requests (igual que _jobs en rutas/descargas.py).
gestor = GestorConcurrencia()
