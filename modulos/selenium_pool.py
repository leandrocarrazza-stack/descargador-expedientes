"""
Pool reutilizable de navegadores Selenium
==========================================

Mantiene un máximo de 5 navegadores Chrome simultáneos.
Reutiliza conexiones para evitar overhead de crear/destruir.
Thread-safe para uso con Celery workers.

Uso:
    pool = SeleniumPool()
    driver = pool.obtener()
    # ... usar driver ...
    pool.devolver(driver)
    pool.limpiar()  # Al finalizar
"""

from queue import Queue, Empty
from threading import Lock
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from pathlib import Path
from typing import Optional

from modulos.logger import crear_logger

logger = crear_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════════

MAX_DRIVERS = 5  # Máximo de navegadores simultáneos
DRIVER_TIMEOUT = 300  # Timeout en segundos (5 minutos)


class SeleniumPool:
    """
    Pool de drivers Selenium reutilizables.

    Características:
    - Máximo 5 drivers simultáneos
    - Thread-safe (usa Lock)
    - Timeout automático
    - Manejo de errores

    Attributes:
        disponibles: Cola de drivers listos para usar
        en_uso: Contador de drivers activos
        total: Total de drivers creados
    """

    def __init__(self, max_drivers: int = MAX_DRIVERS):
        """
        Inicializa el pool.

        Args:
            max_drivers: Máximo de drivers simultáneos
        """
        self.max_drivers = max_drivers
        self.disponibles: Queue = Queue(maxsize=max_drivers)
        self.en_uso = 0
        self.total_creados = 0
        self.lock = Lock()

        logger.info(f"🔄 Pool de Selenium inicializado (máx {max_drivers} drivers)")

    def _crear_driver(self) -> webdriver.Chrome:
        """
        Crea un nuevo navegador Chrome.

        Returns:
            webdriver.Chrome: Driver nuevo

        Raises:
            Exception: Si no se puede crear el driver
        """
        try:
            options = Options()

            # Configuraciones para headless (sin UI visual)
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")

            # Configuraciones de seguridad
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)

            # Configuración de permisos
            prefs = {
                "download.default_directory": str(Path.home() / "Downloads"),
                "download.prompt_for_download": False,
                "profile.default_content_settings.popups": 0,
            }
            options.add_experimental_option("prefs", prefs)

            # Crear el driver
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(DRIVER_TIMEOUT)
            driver.set_script_timeout(DRIVER_TIMEOUT)

            self.total_creados += 1
            logger.debug(f"✅ Driver Selenium #{self.total_creados} creado")

            return driver

        except Exception as e:
            logger.error(f"❌ Error al crear driver Selenium: {e}")
            raise

    def obtener(self) -> webdriver.Chrome:
        """
        Obtiene un driver del pool.

        Si hay drivers disponibles, reutiliza uno.
        Si no, crea uno nuevo (hasta el límite MAX_DRIVERS).

        Returns:
            webdriver.Chrome: Driver listo para usar

        Raises:
            Exception: Si se alcanza el máximo de drivers y no hay disponibles
        """
        with self.lock:
            # Intentar obtener uno disponible sin bloquear
            try:
                driver = self.disponibles.get_nowait()
                logger.debug(f"♻️ Driver reutilizado (en uso: {self.en_uso + 1}/{self.max_drivers})")
                self.en_uso += 1
                return driver
            except Empty:
                pass

            # Si no hay disponible pero podemos crear uno nuevo
            if self.en_uso < self.max_drivers:
                driver = self._crear_driver()
                self.en_uso += 1
                logger.debug(f"🆕 Nuevo driver (en uso: {self.en_uso}/{self.max_drivers})")
                return driver

            # Error: límite alcanzado
            raise RuntimeError(
                f"❌ Límite de drivers alcanzado ({self.max_drivers}). "
                f"Espera a que se liberen otros."
            )

    def devolver(self, driver: webdriver.Chrome) -> None:
        """
        Devuelve un driver al pool para reutilización.

        Args:
            driver: Driver a devolver
        """
        with self.lock:
            if driver and self.en_uso > 0:
                try:
                    # Limpiar cookies y almacenamiento para próximo uso
                    driver.delete_all_cookies()
                    driver.execute_script("window.localStorage.clear();")

                    self.disponibles.put(driver, block=False)
                    self.en_uso -= 1
                    logger.debug(f"↩️ Driver devuelto al pool (en uso: {self.en_uso}/{self.max_drivers})")
                except Exception as e:
                    logger.warning(f"⚠️ Error al devolver driver: {e}")
                    self._eliminar_driver(driver)

    def _eliminar_driver(self, driver: Optional[webdriver.Chrome] = None) -> None:
        """
        Elimina un driver de forma segura.

        Args:
            driver: Driver a eliminar (opcional)
        """
        if driver:
            try:
                driver.quit()
                logger.debug("🗑️ Driver eliminado")
            except Exception as e:
                logger.warning(f"⚠️ Error al cerrar driver: {e}")

    def limpiar(self) -> None:
        """
        Cierra todos los drivers y limpia el pool.

        Se debe llamar al finalizar la aplicación.
        """
        with self.lock:
            logger.info(f"🧹 Limpiando pool de Selenium ({self.total_creados} drivers creados)")

            # Cerrar drivers disponibles
            while not self.disponibles.empty():
                try:
                    driver = self.disponibles.get_nowait()
                    self._eliminar_driver(driver)
                except Empty:
                    break

            self.en_uso = 0
            logger.info("✅ Pool limpiado")

    def estado(self) -> dict:
        """
        Retorna el estado actual del pool.

        Returns:
            dict: Info sobre drivers activos, disponibles, etc.
        """
        with self.lock:
            return {
                'drivers_activos': self.en_uso,
                'drivers_disponibles': self.disponibles.qsize(),
                'max_drivers': self.max_drivers,
                'total_creados': self.total_creados,
            }


# ═══════════════════════════════════════════════════════════════════════════
#  INSTANCIA GLOBAL (singleton)
# ═══════════════════════════════════════════════════════════════════════════

_pool_global: Optional[SeleniumPool] = None


def obtener_pool() -> SeleniumPool:
    """
    Obtiene la instancia global del pool.

    Returns:
        SeleniumPool: Pool singleton
    """
    global _pool_global
    if _pool_global is None:
        _pool_global = SeleniumPool()
    return _pool_global


def limpiar_pool() -> None:
    """Cierra la instancia global del pool."""
    global _pool_global
    if _pool_global:
        _pool_global.limpiar()
        _pool_global = None
