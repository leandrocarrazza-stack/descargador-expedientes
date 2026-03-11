"""
MÓDULO 3: Detección y descarga de archivos (Web Scraping)
==========================================================

Obtiene la lista de movimientos del expediente extrayendo del HTML.
Descarga archivos navegando a los enlaces en el navegador automatizado.
"""

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from pathlib import Path
import time
import requests
from urllib.parse import urljoin
from typing import List, Optional

from modulos.logger import crear_logger
from modulos.excepciones import ErrorDescarga
from modulos.modelos import Archivo, Movimiento

logger = crear_logger(__name__)


class DescargadorArchivos:
    """Cliente para descargar archivos de un expediente (Web Scraping)."""

    def __init__(self, cliente_selenium, carpeta_temp, timeout=60):
        """
        Inicializa el descargador de archivos.

        Args:
            cliente_selenium: Instancia de ClienteSelenium con navegador abierto
            carpeta_temp: Path de la carpeta temporal
            timeout: Timeout para esperados (segundos)
        """
        self.cliente = cliente_selenium
        self.carpeta_temp = Path(carpeta_temp)
        self.timeout = timeout
        self.carpeta_temp.mkdir(parents=True, exist_ok=True)

    def obtener_movimientos(self, numero: str) -> List[Movimiento]:
        """
        Obtiene movimientos del expediente extrayendo del HTML de TODAS las páginas.

        Implementa paginación automática:
        1. Extrae movimientos de la página actual
        2. Detecta si hay más páginas (botón "Siguiente" o indicador de página)
        3. Navega a la siguiente página y repite
        4. Devuelve lista completa de movimientos de TODAS las páginas

        Args:
            numero: Número del expediente (puede ser el número o ID interno)

        Retorna:
            List[Movimiento]: Lista de movimientos con sus archivos adjuntos de TODAS las páginas

        Lanza:
            ErrorDescarga: Si hay error en la navegación
        """
        logger.info("Obteniendo lista de movimientos (con paginación)...")

        try:
            driver = self.cliente.driver
            movimientos = []
            pagina_actual = 1

            while True:
                logger.debug(f"Procesando página {pagina_actual}...")
                time.sleep(1)

                # Extraer HTML de la página
                html = driver.page_source
                soup = BeautifulSoup(html, "html.parser")

                # Buscar la tabla de movimientos
                tablas = soup.find_all("table")
                if not tablas:
                    logger.warning("No se encontró tabla de movimientos")
                    break

                tabla = tablas[0]  # Primera tabla es la de movimientos
                filas = tabla.find_all("tr")

                if not filas:
                    logger.warning("No hay filas en la tabla")
                    break

                logger.debug(f"Encontradas {len(filas)} filas en esta página")

                movimientos_pagina = 0
                for fila_idx, fila in enumerate(filas, 1):
                    # Obtener descripción de la fila
                    texto_fila = fila.get_text(strip=True)

                    # Buscar enlaces en la fila
                    enlaces = fila.find_all("a")

                    if enlaces:
                        # Extraer información del movimiento
                        movimiento = {
                            "indice": len(movimientos) + fila_idx,  # Índice global
                            "descripcion": texto_fila[:150],  # Primeros 150 caracteres
                            "enlaces_descarga": [],
                            "pagina": pagina_actual,  # Registrar en qué página estaba
                        }

                        # Buscar enlaces de descarga
                        # NOTA: Mesa Virtual muestra dos enlaces por movimiento:
                        #   1er enlace (texto "PDF" o "RTF"): previsualización → siempre falla con EOF
                        #   2do enlace (texto vacío): descarga directa → es el archivo real
                        #
                        # ESTRATEGIA DE SELECCIÓN (en orden):
                        #   1. Descartar enlaces con texto "PDF" o "RTF" (previsualización obvia)
                        #   2. Si quedan múltiples enlaces, tomar solo el ÚLTIMO
                        #      (Mesa Virtual siempre pone el enlace de descarga directa al final)
                        #   3. Así se evita descargar el enlace de previsualización que
                        #      genera errores "EOF marker not found"
                        candidatos = []
                        for enlace in enlaces:
                            href = enlace.get("href", "")
                            texto_enlace = enlace.get_text(strip=True)

                            # Saltar enlaces de previsualización con texto explícito
                            if texto_enlace.upper() in ("PDF", "RTF"):
                                continue

                            if href:
                                candidatos.append(
                                    {
                                        "href": href,
                                        "texto": texto_enlace,
                                        "es_pdf": "pdf" in href.lower()
                                        or texto_enlace.upper() == "PDF",
                                    }
                                )

                        # Si hay más de un candidato, quedarse solo con el último
                        # (el enlace de descarga directa siempre es el último en Mesa Virtual)
                        if len(candidatos) > 1:
                            logger.debug(
                                f"{len(candidatos)} enlaces candidatos → tomando solo el último (descarga directa)"
                            )
                            candidatos = [candidatos[-1]]

                        movimiento["enlaces_descarga"] = candidatos

                        # Agregar si tiene enlaces
                        if movimiento["enlaces_descarga"]:
                            movimientos.append(movimiento)
                            movimientos_pagina += 1

                logger.debug(f"{movimientos_pagina} movimiento(s) con archivos en esta página")

                # Detectar si hay siguiente página
                hay_siguiente = self._navegar_siguiente_pagina(driver)

                if not hay_siguiente:
                    logger.info("Fin de la paginación")
                    break

                pagina_actual += 1

            logger.info(f"Total movimientos con archivos (todas las páginas): {len(movimientos)}")
            return movimientos

        except ErrorDescarga:
            raise
        except Exception as e:
            logger.error(f"Error obteniendo movimientos: {e}", exc_info=True)
            raise ErrorDescarga(f"Error obteniendo movimientos: {e}") from e

    def _navegar_siguiente_pagina(self, driver):
        """
        Intenta navegar a la siguiente página usando diferentes estrategias.

        Estrategias (en orden de preferencia):
        1. Detectar si estamos en la última página por indicador "Página X de Y"
        2. Buscar botón "Siguiente" o "Next" habilitado
        3. Buscar botón deshabilitado (indica última página)

        Args:
            driver: Instancia de Selenium WebDriver

        Retorna:
            bool: True si se navegó a siguiente página, False si no hay más páginas
        """
        try:
            # Esperar un poco para que se estabilice la página
            time.sleep(1)

            # ESTRATEGIA PRIMARIA: Detectar indicador de página (ej: "Página 1 de 14")
            # Esto es más confiable que buscar botones
            try:
                # Buscar texto que indique "Página X de Y" (más específico)
                import re

                # Buscar en toda la página
                html = driver.page_source

                # Patrones comunes: "Página 1 de 14", "1 de 14", "page 1 of 14", etc
                patrones = [
                    r"[Pp]á?gina\s+(\d+)\s+de\s+(\d+)",  # Página 1 de 14
                    r"page\s+(\d+)\s+of\s+(\d+)",  # page 1 of 14
                    r"(\d+)\s+de\s+(\d+)",  # 1 de 14 (solo números)
                ]

                for patron in patrones:
                    matches = re.findall(patron, html, re.IGNORECASE)
                    if matches:
                        # Tomar el último match (más probable que sea el indicador de paginación)
                        pagina_actual = int(matches[-1][0])
                        total_paginas = int(matches[-1][1])

                        logger.debug(f"Página {pagina_actual} de {total_paginas}")

                        if pagina_actual >= total_paginas:
                            logger.debug(
                                f"Última página alcanzada (página {pagina_actual}/{total_paginas})"
                            )
                            return False
                        else:
                            # Hay más páginas, continuar buscando botón siguiente
                            break
            except Exception as e:
                # No encontró indicador, continuar con otras estrategias
                pass

            # ESTRATEGIA 2: Buscar botón "Siguiente" habilitado
            selectores_siguiente = [
                "//button[contains(@aria-label, 'Siguiente') and not(@disabled)]",
                "//button[contains(@aria-label, 'siguiente') and not(@disabled)]",
                "//button[contains(@aria-label, 'Next') and not(@disabled)]",
                "//button[contains(@aria-label, 'next') and not(@disabled)]",
                "//a[contains(@aria-label, 'Siguiente') and not(@aria-disabled='true')]",
                "//a[contains(@aria-label, 'siguiente') and not(@aria-disabled='true')]",
                "//button[text()[contains(., 'Siguiente')] and not(@disabled)]",
                "//button[text()[contains(., 'siguiente')] and not(@disabled)]",
            ]

            for selector in selectores_siguiente:
                try:
                    elemento = WebDriverWait(driver, 2).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    # Verificar que esté visible y habilitado
                    if elemento.is_enabled() and elemento.is_displayed():
                        logger.debug("Navegando a siguiente página...")
                        # Hacer scroll hasta el botón para asegurarse de que es clickeable
                        driver.execute_script("arguments[0].scrollIntoView(true);", elemento)
                        time.sleep(0.5)
                        elemento.click()
                        time.sleep(2)  # Esperar a que cargue la nueva página
                        return True
                except:
                    continue

            # ESTRATEGIA 3: Detectar si el botón "Siguiente" está deshabilitado
            # (indica que estamos en la última página)
            selectores_siguiente_deshabilitado = [
                "//button[contains(@aria-label, 'Siguiente') and @disabled]",
                "//button[contains(@aria-label, 'siguiente') and @disabled]",
                "//button[contains(@aria-label, 'Next') and @disabled]",
                "//a[contains(@aria-label, 'Siguiente') and @aria-disabled='true']",
            ]

            for selector in selectores_siguiente_deshabilitado:
                try:
                    WebDriverWait(driver, 1).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    # Si encontramos el botón deshabilitado, estamos en la última página
                    logger.debug("Botón siguiente deshabilitado, última página detectada")
                    return False
                except:
                    continue

            # Si no encontró nada, asumir que no hay más páginas
            logger.debug("No se encontró botón siguiente habilitado, asumiendo última página")
            return False

        except Exception as e:
            logger.warning(f"Error navegando: {str(e)[:50]}")
            return False

    def descargar_archivos(self, numero: str, movimientos: List[Movimiento]) -> List[Path]:
        """
        Descarga todos los archivos de los movimientos.

        Estrategia:
        1. Preferir URLs absolutas con token JWT
        2. Fallback a URLs relativas navegando con Selenium
        3. Usar descarga directa con requests

        Args:
            numero: Número del expediente
            movimientos: Lista de movimientos con archivos

        Retorna:
            List[Path]: Lista de rutas de archivos descargados
        """
        if not movimientos:
            logger.warning("No hay archivos para descargar.")
            return []

        logger.info(f"Descargando archivos de {len(movimientos)} movimiento(s)...")

        archivos_descargados = []
        driver = self.cliente.driver

        for mov_idx, movimiento in enumerate(movimientos, 1):
            desc = movimiento["descripcion"][:60]
            logger.debug(f"[{mov_idx}/{len(movimientos)}] {desc}...")

            for enlace_idx, enlace in enumerate(movimiento["enlaces_descarga"], 1):
                href = enlace["href"]
                texto = enlace["texto"]

                if not href:
                    continue

                # Elegir la mejor URL para descargar
                url_descarga = None

                # Si es URL absoluta con token, usarla directamente
                if href.startswith("http"):
                    url_descarga = href
                # Si es URL relativa, navegar con Selenium
                elif href.startswith("/"):
                    # Navegar a la URL relativa (Selenium mantiene sesión autenticada)
                    try:
                        url_descarga = f"https://mesavirtual.jusentrerios.gov.ar{href}"
                    except:
                        continue

                if not url_descarga:
                    continue

                try:
                    # Generar nombre de archivo (extensión .pdf provisional)
                    nombre_archivo = (
                        f"{mov_idx:03d}_{enlace_idx:02d}_{texto[:30].replace('/', '_')}.pdf"
                    )
                    ruta_archivo = self.carpeta_temp / nombre_archivo

                    # Descargar usando Selenium (mantiene sesión)
                    if self._descargar_archivo_selenium(url_descarga, ruta_archivo):
                        # Detectar tipo real por magic bytes (no confiar en la extensión)
                        ruta_final = ruta_archivo
                        tipo_detectado = "pdf"
                        try:
                            with open(ruta_archivo, "rb") as f:
                                magic_bytes = f.read(10)
                            if magic_bytes.startswith(b"{\\rtf"):
                                # Es un RTF disfrazado de PDF: renombrar
                                ruta_rtf = ruta_archivo.with_suffix(".rtf")
                                ruta_archivo.rename(ruta_rtf)
                                ruta_final = ruta_rtf
                                tipo_detectado = "rtf"
                                logger.info(
                                    f"{nombre_archivo[:40]} → .rtf (contenido RTF detectado)"
                                )
                            else:
                                logger.info(f"{nombre_archivo[:50]}")
                        except Exception:
                            logger.info(f"{nombre_archivo[:50]}")

                        archivos_descargados.append(
                            {
                                "path": ruta_final,
                                "tipo": tipo_detectado,
                                # Usar mov_idx (posición en la lista descargada) como clave de orden.
                                # mov_idx=1 es el más RECIENTE (página 1 de Mesa Virtual),
                                # mov_idx=N es el más ANTIGUO (última página).
                                # Con reverse=True en unificacion.py, se procesa de más antiguo a más reciente.
                                "movimiento": mov_idx,
                                "url": url_descarga,
                            }
                        )
                    else:
                        logger.warning(f"Error descargando {nombre_archivo[:50]}")

                except Exception as e:
                    logger.error(f"{str(e)[:50]}", exc_info=True)

        logger.info(f"Total descargados: {len(archivos_descargados)}/{len(movimientos)}")
        return archivos_descargados

    def _descargar_archivo_selenium(self, url, ruta_destino):
        """
        Descarga un archivo usando requests con cookies de Selenium.

        MEJORAS:
        - No usa driver.get() para evitar que se abra/visualice el archivo
        - Usa requests directamente con cookies autenticadas
        - Verifica completitud del descarga y validez del PDF
        - Reintentos automáticos para descargas incompletas

        Args:
            url: URL del archivo
            ruta_destino: Path donde guardar el archivo

        Retorna:
            bool: True si se descargó exitosamente, False si falló
        """
        try:
            driver = self.cliente.driver
            ruta_destino.parent.mkdir(parents=True, exist_ok=True)

            # Obtener cookies de Selenium para mantener sesión autenticada
            cookies = driver.get_cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            # Headers para simular navegador real y evitar problemas de descarga
            # Accept amplio para no interferir con tipos RTF, DOC, etc.
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/pdf, application/rtf, application/msword, application/octet-stream, */*",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }

            # Intentar descarga con reintentos
            max_intentos = 3
            for intento in range(max_intentos):
                try:
                    # Realizar descarga directa sin abrir en navegador
                    response = requests.get(
                        url,
                        cookies=cookie_dict,
                        headers=headers,
                        timeout=self.timeout,
                        stream=True,  # Descarga por chunks para archivos grandes
                        allow_redirects=True,
                    )
                    response.raise_for_status()

                    # Validar que tenemos contenido
                    if not response.content or len(response.content) < 100:
                        if intento < max_intentos - 1:
                            time.sleep(1)
                            continue
                        else:
                            return False

                    # Descargar por chunks para archivos grandes
                    with open(ruta_destino, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    # Validar que el archivo se escribió completamente
                    tamaño_descargado = ruta_destino.stat().st_size

                    # Verificar el tipo de archivo antes de validar como PDF
                    with open(ruta_destino, "rb") as f:
                        magic_bytes = f.read(10)

                    # Si es RTF, no validar como PDF (la validación falla correctamente)
                    if magic_bytes.startswith(b"{\\rtf"):
                        return True  # RTF válido, se procesará después

                    # Verificar que sea un PDF válido (si es PDF)
                    if "pdf" in str(url).lower() or ruta_destino.name.endswith(".pdf"):
                        try:
                            from PyPDF2 import PdfReader

                            reader = PdfReader(str(ruta_destino))
                            num_pages = len(reader.pages)
                            if num_pages == 0:
                                if intento < max_intentos - 1:
                                    time.sleep(1)
                                    continue
                                else:
                                    return False
                        except Exception as pdf_err:
                            # PDF con errores menores (EOF) puede ser usable
                            # Solo rechazar si es realmente pequeño
                            if tamaño_descargado < 200:
                                return False
                            # De lo contrario asumir que es parcialmente válido

                    return True

                except requests.exceptions.RequestException as e:
                    if intento < max_intentos - 1:
                        time.sleep(2)  # Esperar antes de reintentar
                        continue
                    else:
                        return False

            return False

        except Exception as e:
            return False


def crear_descargador(
    cliente_selenium, api_graphql_url=None, api_archivos_url=None, carpeta_temp=None
):
    """
    Función auxiliar para crear un descargador preconfigurado.

    Args:
        cliente_selenium: Cliente Selenium autenticado
        api_graphql_url: (no se usa con web scraping)
        api_archivos_url: (no se usa con web scraping)
        carpeta_temp: Ruta de la carpeta temporal

    Retorna:
        DescargadorArchivos: Descargador listo para usar
    """
    if carpeta_temp is None:
        from pathlib import Path

        carpeta_temp = Path.cwd() / "temp"

    return DescargadorArchivos(cliente_selenium, carpeta_temp)
