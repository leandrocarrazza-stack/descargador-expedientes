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
from urllib.parse import urljoin
from typing import List, Optional

from modulos.logger import crear_logger
from modulos.excepciones import ErrorDescarga
from modulos.modelos import Archivo, Movimiento
from modulos.conversion import memoria_disponible_mb

logger = crear_logger(__name__)


class DescargadorArchivos:
    """Cliente para descargar archivos de un expediente (Web Scraping)."""

    def __init__(self, cliente_selenium, carpeta_temp, timeout=60, tamanio_lote=3, crear_cliente_fn=None):
        """
        Inicializa el descargador de archivos.

        Args:
            cliente_selenium: Instancia de ClienteSelenium con navegador abierto
            carpeta_temp: Path de la carpeta temporal
            timeout: Timeout para esperados (segundos)
            tamanio_lote: Cantidad de archivos a descargar antes de reciclar navegador
            crear_cliente_fn: Función lambda para recrear cliente (para reciclaje de navegador)
        """
        self.cliente = cliente_selenium
        self.carpeta_temp = Path(carpeta_temp)
        self.timeout = timeout
        self.tamanio_lote = tamanio_lote  # Descargar N archivos, luego reciclar
        self.crear_cliente_fn = crear_cliente_fn  # Función para reciclar navegador
        self.contador_descargas = 0  # Contador de descargas para reciclaje preventivo
        self._ultimo_fallo_fue_auth = False  # True si el último intento falló por sesión expirada
        self.carpeta_temp.mkdir(parents=True, exist_ok=True)

    def obtener_movimientos(self, expediente_id, max_movimientos=30):
        """
        Obtiene movimientos del expediente extrayendo del HTML de TODAS las páginas.

        Implementa paginación automática:
        1. Extrae movimientos de la página actual
        2. Detecta si hay más páginas (botón "Siguiente" o indicador de página)
        3. Navega a la siguiente página y repite
        4. Detiene cuando alcanza max_movimientos (evita memory crash)
        5. Devuelve lista completa de movimientos encontrados

        Args:
            expediente_id: ID del expediente (puede ser el número o ID interno)
            max_movimientos: Máximo de movimientos a obtener (default 30, evita crashes)

        Retorna:
            list: Lista de movimientos con sus archivos adjuntos

        Lanza:
            ErrorDescarga: Si hay error en la navegación
        """
        print("\n[LIST] Obteniendo lista de movimientos (con paginación)...")
        print(f"     [LIMIT] Máximo {max_movimientos} movimientos (previene crashes)")

        try:
            driver = self.cliente.driver
            movimientos = []
            pagina_actual = 1

            while len(movimientos) < max_movimientos:
                print(f"\n    Procesando página {pagina_actual}...")
                time.sleep(1)

                # Extraer HTML de la página
                html = driver.page_source
                soup = BeautifulSoup(html, "html.parser")

                # Buscar la tabla de movimientos
                tablas = soup.find_all("table")
                if not tablas:
                    print("   [WARN]  No se encontró tabla de movimientos")
                    break

                tabla = tablas[0]  # Primera tabla es la de movimientos
                filas = tabla.find_all("tr")

                if not filas:
                    print("   [WARN]  No hay filas en la tabla")
                    break

                print(f"      > Encontradas {len(filas)} filas en esta página")

                # ESTRATEGIA: Solo obtener enlaces de DESCARGA (segundo <a> en cada fila)
                # El primer <a> es preview, el segundo es descarga
                try:
                    # Buscar solo el segundo <a> de cada fila (descarga)
                    enlaces_descarga = driver.find_elements(By.XPATH, "//table//tbody//tr//a[2]")
                    print(f"      > Total enlaces de DESCARGA encontrados: {len(enlaces_descarga)}")
                except:
                    enlaces_descarga = []

                movimientos_pagina = 0

                for fila_idx, fila in enumerate(filas, 1):
                    # Obtener descripción de la fila
                    texto_fila = fila.get_text(strip=True)

                    # Extraer información del movimiento
                    movimiento = {
                        'indice': len(movimientos) + fila_idx,  # Índice global
                        'descripcion': texto_fila[:150],  # Primeros 150 caracteres
                        'enlaces_descarga': [],
                        'pagina': pagina_actual,  # Registrar en qué página estaba
                    }

                    # Obtener el enlace de descarga de esta fila
                    if fila_idx <= len(enlaces_descarga):
                        try:
                            elem = enlaces_descarga[fila_idx - 1]  # Índice 0-based
                            href = elem.get_attribute('href') or ''
                            print(f"         [FILA {fila_idx}] [DESCARGA] {href[:60]}...")

                            # Agregar enlace de descarga
                            if href:
                                movimiento['enlaces_descarga'].append({
                                    'href': href,
                                    'texto': f'descarga_{fila_idx}',
                                    'es_pdf': True,
                                })
                                print(f"                [OK] Agregado para descargar")

                            # Agregar si tiene enlace
                            if movimiento['enlaces_descarga']:
                                movimientos.append(movimiento)
                                movimientos_pagina += 1
                        except Exception as e:
                            print(f"         [ERROR] Error procesando fila {fila_idx}: {str(e)[:50]}")
                    else:
                        print(f"         [SKIP] Fila {fila_idx} sin enlace de descarga disponible")

                print(f"      [OK] {movimientos_pagina} movimiento(s) con archivos en esta página")

                # Verificar si alcanzamos el límite de movimientos
                if len(movimientos) >= max_movimientos:
                    print(f"\n    [LIMIT] Límite de {max_movimientos} movimientos alcanzado")
                    break

                # Detectar si hay siguiente página
                hay_siguiente = self._navegar_siguiente_pagina(driver)

                if not hay_siguiente:
                    print(f"\n    Fin de la paginación")
                    break

                pagina_actual += 1

            print(f"\n    Total movimientos con archivos (todas las páginas): {len(movimientos)}")
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

                        print(f"      [INFO]  Página {pagina_actual} de {total_paginas}")

                        if pagina_actual >= total_paginas:
                            print(f"      [OK] Última página alcanzada (página {pagina_actual}/{total_paginas})")
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
                # Selectores más genéricos para Material-UI
                "//button[@aria-label='Next page']",
                "//button[@aria-label='next page']",
                "//button[@aria-label='Siguiente página']",
                # Buscar por clase o contenido
                "//button[contains(., '>')]",  # Botón con >
            ]

            for selector in selectores_siguiente:
                try:
                    elemento = WebDriverWait(driver, 2).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    # Verificar que esté visible y habilitado
                    if elemento.is_enabled() and elemento.is_displayed():
                        print(f"       Navegando a siguiente página (selector: {selector[:40]}...)")
                        # Hacer scroll hasta el botón para asegurarse de que es clickeable
                        driver.execute_script("arguments[0].scrollIntoView(true);", elemento)
                        time.sleep(0.5)
                        elemento.click()
                        time.sleep(2)  # Esperar a que cargue la nueva página
                        return True
                except Exception as e:
                    continue

            # ESTRATEGIA 3: Usar JavaScript para hacer click en botón siguiente
            # (Más robusto para interfaces React/Material-UI)
            try:
                print(f"       Intentando navegar con JavaScript...")
                resultado = driver.execute_script("""
                    // Buscar botón siguiente en varios lugares
                    let botones = document.querySelectorAll('button');
                    for (let btn of botones) {
                        let texto = btn.textContent.toLowerCase();
                        let aria = (btn.getAttribute('aria-label') || '').toLowerCase();

                        // Buscar palabras clave
                        if (texto.includes('siguiente') || aria.includes('siguiente') ||
                            texto.includes('next') || aria.includes('next') ||
                            texto.includes('>')) {

                            // Verificar que no esté deshabilitado
                            if (!btn.disabled && !btn.getAttribute('aria-disabled')) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                    return false;
                """)

                if resultado:
                    print(f"       Navegado exitosamente con JavaScript")
                    time.sleep(2)
                    return True
            except Exception as e:
                print(f"       Error con JavaScript: {str(e)[:40]}")
                pass

            # ESTRATEGIA 4: Detectar si el botón "Siguiente" está deshabilitado
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
                    print(f"      [OK] Botón siguiente deshabilitado, última página detectada")
                    return False
                except:
                    continue

            # Si no encontró nada, asumir que no hay más páginas
            print(f"      [OK] No se encontró botón siguiente habilitado, asumiendo última página")
            return False

        except Exception as e:
            print(f"      [WARN]  Error navegando: {str(e)[:50]}")
            return False

    def descargar_archivos(self, numero: str, movimientos: List[Movimiento]) -> List[Path]:
        """
        Descarga todos los archivos de los movimientos CON RECICLAJE DE NAVEGADOR.

        Estrategia:
        1. Preferir URLs absolutas con token JWT
        2. Fallback a URLs relativas navegando con Selenium
        3. Usar descarga directa con requests

        RECICLAJE:
        - Cada N descargas, recicla el navegador para evitar memory leaks
        - Si el driver muere, lo detecta y recrea automáticamente

        Args:
            numero: Número del expediente
            movimientos: Lista de movimientos con archivos

        Retorna:
            List[Path]: Lista de rutas de archivos descargados
        """
        if not movimientos:
            print("[WARN]  No hay archivos para descargar.")
            return []

        print(f"\n Descargando archivos de {len(movimientos)} movimiento(s)...")
        print(f"   [INFO] Reciclaje automático cada {self.tamanio_lote} descargas\n")

        archivos_descargados = []

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
                    # Sanitizar: reemplazar caracteres inválidos en Windows
                    texto_sanitizado = texto[:30]
                    for char in '<>:"|?*\\':
                        texto_sanitizado = texto_sanitizado.replace(char, '_')
                    texto_sanitizado = texto_sanitizado.replace('/', '_')

                    nombre_archivo = f"{mov_idx:03d}_{enlace_idx:02d}_{texto_sanitizado}.pdf"
                    ruta_archivo = self.carpeta_temp / nombre_archivo

                    # RECICLAJE PREVENTIVO: Cada N descargas, reciclar navegador
                    self.contador_descargas += 1
                    if self.contador_descargas > 0 and self.contador_descargas % self.tamanio_lote == 0:
                        self._reciclar_navegador()

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
                                tipo_detectado = 'rtf'
                                print(f"      [OK] {nombre_archivo[:40]} > .rtf (contenido RTF detectado)")
                            else:
                                print(f"      [OK] {nombre_archivo[:50]}")
                        except Exception:
                            print(f"      [OK] {nombre_archivo[:50]}")

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
                        print(f"      [WARN]  Error descargando {nombre_archivo[:50]}")

                except Exception as e:
                    print(f"      [ERROR] {str(e)[:50]}")

        logger.info(f"Total descargados: {len(archivos_descargados)}/{len(movimientos)}")
        return archivos_descargados

    def _reciclar_navegador(self):
        """
        Recicla el navegador para evitar memory leaks.

        Estrategia:
        1. Cierra el navegador actual (libera memoria)
        2. Crea uno nuevo con la sesión guardada
        3. Actualiza self.cliente para que siguientes descargas usen el nuevo navegador

        Esta función se llama automáticamente cada N descargas.
        """
        print(f"\n      [RECYCLE] Reciclando navegador después de {self.contador_descargas} descargas...")

        try:
            # Cerrar navegador actual
            if self.cliente and self.cliente.driver:
                try:
                    self.cliente.cerrar()
                except Exception as e:
                    print(f"         [WARN] Error cerrando navegador anterior: {str(e)[:50]}")

            # Crear nuevo cliente con sesión guardada (sin necesidad de login manual)
            if self.crear_cliente_fn:
                print("      [NET] Creando nuevo navegador con sesión guardada...")
                nuevo_cliente = self.crear_cliente_fn()
                self.cliente = nuevo_cliente

                # Verificar que el nuevo cliente está funcional
                if self.cliente and self.cliente.driver:
                    print(f"      [OK] Navegador reciclado correctamente")
                else:
                    print(f"      [ERROR] Fallo creando nuevo navegador")
            else:
                print(f"      [WARN] No hay función para crear cliente, continuando sin reciclaje")

        except Exception as e:
            print(f"      [ERROR] Error reciclando navegador: {str(e)[:80]}")

    # El ícono de descarga de Material-UI (data-testid="GetAppIcon") es el
    # botón de descarga real de cada fila. Su <a href="#"> NO es una URL real:
    # la descarga la dispara el JS de la SPA al clickear (fetch + blob), así
    # que no hay ninguna URL a la que navegar o hacerle un requests.get().
    # El OTRO <a> de la fila (la celda "Descripción") sí es un link real, pero
    # lleva a la vista de detalle de la SPA, no a un archivo — de ahí que
    # extraer "el segundo <a> de la fila" por posición (estrategia vieja)
    # terminara agarrando el link equivocado.
    SELECTOR_BOTON_DESCARGA = "//table//*[@data-testid='GetAppIcon']/ancestor::a[1]"

    def _esperar_archivo_nuevo(self, carpeta, archivos_antes, timeout=60):
        """
        Espera a que aparezca un archivo NUEVO (no estaba en `archivos_antes`)
        y completo (no parcial) en `carpeta`.

        Chrome escribe descargas en curso como "*.crdownload" y las renombra
        al nombre final recién cuando terminan. Además, revisa que el tamaño
        se mantenga estable entre dos lecturas para no devolver un archivo
        que todavía se está escribiendo.

        Retorna:
            Path del archivo nuevo, o None si no llegó nada a tiempo.
        """
        limite = time.time() + timeout
        while time.time() < limite:
            try:
                candidatos = [
                    f for f in carpeta.iterdir()
                    if f.is_file()
                    and f.name not in archivos_antes
                    and not f.name.endswith((".crdownload", ".tmp"))
                ]
            except FileNotFoundError:
                candidatos = []

            if candidatos:
                archivo = candidatos[0]
                try:
                    tam1 = archivo.stat().st_size
                    time.sleep(0.5)
                    tam2 = archivo.stat().st_size
                except FileNotFoundError:
                    time.sleep(0.5)
                    continue
                if tam1 == tam2 and tam1 > 0:
                    return archivo

            time.sleep(0.5)

        return None

    def _validar_archivo_descargado(self, ruta_destino):
        """
        Valida que el archivo descargado sea un PDF/RTF utilizable.

        Retorna:
            True si es válido, False si hay que reintentar/abortar.
        """
        tamaño_descargado = ruta_destino.stat().st_size
        if tamaño_descargado < 100:
            return False

        with open(ruta_destino, "rb") as f:
            magic_bytes = f.read(10)

        # Si es RTF, no validar como PDF (la validación falla correctamente)
        if magic_bytes.startswith(b"{\\rtf"):
            return True

        # Verificar que sea un PDF válido (todo se nombra .pdf de entrada,
        # ver descargar_todo_por_paginas)
        if ruta_destino.name.endswith(".pdf"):
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(str(ruta_destino))
                if len(reader.pages) == 0:
                    return False
            except Exception:
                # PDF con errores menores (EOF) puede ser usable
                # Solo rechazar si es realmente pequeño
                if tamaño_descargado < 200:
                    return False

        return True

    def _contar_botones_descarga(self, driver):
        """Cuenta los botones de descarga (ícono GetAppIcon) en la página actual."""
        try:
            return len(driver.find_elements(By.XPATH, self.SELECTOR_BOTON_DESCARGA))
        except Exception:
            return 0

    def _descargar_archivo_selenium(self, indice_boton, ruta_destino):
        """
        Descarga un archivo haciendo click real en su botón de descarga
        (ícono Material-UI "GetAppIcon"), en la MISMA pestaña donde está la
        tabla de movimientos — no navega a ninguna URL ni abre pestañas.

        Por qué: el botón de descarga no es un link real (ver
        SELECTOR_BOTON_DESCARGA): dispara la descarga por JavaScript. No hay
        ninguna URL que replicar con `requests` ni a la que navegar; hay que
        clickear el elemento de verdad y dejar que el JS de la página maneje
        la descarga, con Browser.setDownloadBehavior configurado de antemano
        para que Chrome la guarde a disco en vez de intentar mostrar un
        diálogo "Guardar como" (imposible en headless).

        El botón se re-localiza por índice en cada intento (no se guarda el
        WebElement de una vez): si React re-renderiza la tabla entre un
        intento y otro, una referencia vieja quedaría "stale".

        Args:
            indice_boton: posición (0-based) del botón de descarga en la página actual
            ruta_destino: Path donde guardar el archivo

        Retorna:
            bool: True si se descargó exitosamente, False si falló
        """
        try:
            # DETECCIÓN REACTIVA: Verificar que el driver está vivo
            try:
                driver = self.cliente.driver
                if not driver:
                    print(f"         [DRIVER-DEAD] Detectado driver muerto, reciclando...")
                    self._reciclar_navegador()
                    driver = self.cliente.driver

                try:
                    _ = driver.current_url
                except:
                    print(f"         [DRIVER-CRASH] Detectado driver no responde, reciclando...")
                    self._reciclar_navegador()
                    driver = self.cliente.driver

            except Exception as e:
                print(f"         [DRIVER-ERROR] Error verificando driver: {str(e)[:50]}")
                self._reciclar_navegador()
                driver = self.cliente.driver

            carpeta_descargas = ruta_destino.parent
            carpeta_descargas.mkdir(parents=True, exist_ok=True)

            try:
                driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
                    'behavior': 'allow',
                    'downloadPath': str(carpeta_descargas),
                })
            except Exception:
                # Fallback para versiones viejas de Chrome sin Browser.*
                driver.execute_cdp_cmd('Page.setDownloadBehavior', {
                    'behavior': 'allow',
                    'downloadPath': str(carpeta_descargas),
                })

            max_intentos = 3
            for intento in range(max_intentos):
                try:
                    archivos_antes = {
                        f.name for f in carpeta_descargas.iterdir() if f.is_file()
                    }

                    botones = driver.find_elements(By.XPATH, self.SELECTOR_BOTON_DESCARGA)
                    if indice_boton >= len(botones):
                        print(f"         [ERROR] Botón #{indice_boton} ya no está en la página (hay {len(botones)})")
                        if intento < max_intentos - 1:
                            time.sleep(2)
                            continue
                        return False

                    boton = botones[indice_boton]
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton)
                    driver.execute_script("arguments[0].click();", boton)

                    archivo = self._esperar_archivo_nuevo(carpeta_descargas, archivos_antes, timeout=self.timeout)

                    if not archivo:
                        # No llegó ningún archivo nuevo: puede ser que la sesión
                        # expiró y el click disparó una redirección a login.
                        url_actual = ""
                        try:
                            url_actual = driver.current_url
                        except Exception:
                            pass
                        if "ol-sso" in url_actual or "/login" in url_actual:
                            print(f"         [AUTH] Redirigido a login tras el click (sesión expirada), abortando")
                            self._ultimo_fallo_fue_auth = True
                            return False

                        print(f"         [TIMEOUT] Sin archivo nuevo tras {self.timeout}s (intento {intento + 1}/{max_intentos})")
                        if intento < max_intentos - 1:
                            time.sleep(2)
                            continue
                        return False

                    archivo.replace(ruta_destino)

                    if self._validar_archivo_descargado(ruta_destino):
                        return True

                    if intento < max_intentos - 1:
                        time.sleep(1)
                        continue
                    return False

                except Exception as e:
                    print(f"         [DOWNLOAD-ERROR] Intento {intento + 1}/{max_intentos}: {str(e)[:80]}")
                    if intento < max_intentos - 1:
                        time.sleep(2)
                        continue
                    return False

            return False

        except Exception as e:
            print(f"         [DOWNLOAD-FATAL] Error fatal descargando: {str(e)[:80]}")
            return False

    def _purgar_memoria_chrome(self, driver):
        """
        Le pide a Chrome que libere memoria acumulada, sin cerrar la pestaña
        ni perder sesión/posición en la tabla de movimientos.

        Por qué: en expedientes muy largos (200+ páginas), Chrome va
        acumulando memoria a lo largo de la sesión (cachés, estado de cada
        página de la tabla ya visitada) hasta agotar los 512 MB del servidor
        cerca del final, aun con la sesión y la descarga funcionando bien.
        Esto es más simple y de menor riesgo que reciclar el navegador
        completo (que obligaría a recordar la página exacta donde se estaba
        y volver a navegar hasta ahí): no toca el flujo de navegación en
        absoluto, sólo le pide a Chrome que limpie lo que ya no usa.

        Sólo llama a HeapProfiler.collectGarbage (GC estándar, equivalente a
        lo que hace el botón "Collect garbage" de Chrome DevTools). Se
        probó también Memory.forciblyPurgeJavaScriptMemory, pero esa llamada
        simula una intervención de OOM y en la práctica tiró abajo el
        execution context del frame activo (la SPA quedó rota, con
        "no such execution context" en el siguiente comando de Selenium) —
        demasiado agresiva para una página React en uso.
        """
        antes = memoria_disponible_mb()

        try:
            driver.execute_cdp_cmd('HeapProfiler.enable', {})
            driver.execute_cdp_cmd('HeapProfiler.collectGarbage', {})
        except Exception as e:
            print(f"      [MEMORIA] HeapProfiler.collectGarbage falló: {str(e)[:60]}")

        despues = memoria_disponible_mb()
        if antes is not None and despues is not None:
            print(f"      [MEMORIA] Purgado Chrome: {antes} MB -> {despues} MB disponibles")

    def descargar_todo_por_paginas(self, numero: str) -> List[dict]:
        """
        Descarga archivos de TODAS las páginas, procesando cada página antes de navegar.

        CRITICO: los botones de descarga se ubican por índice dentro de la
        tabla React actual (ver SELECTOR_BOTON_DESCARGA). Si primero se
        recolectaran los de TODAS las páginas y se descargara después, los
        WebElements de páginas ya abandonadas quedarían "stale" en cuanto
        React re-renderice la tabla al paginar. Solución: descargar TODOS
        los archivos de la página actual ANTES de navegar a la siguiente.

        Orden cronologico:
        Mesa Virtual muestra mas nuevo primero (pagina 1 = mas reciente).
        Asignamos mov_idx secuencial: 1, 2, 3... donde 1 = mas reciente.
        El unificador usa reverse=True, poniendo el mas antiguo primero en el PDF.

        Sin límite de movimientos: recorre páginas hasta que Mesa Virtual
        no tenga más (o una página no traiga botones), pase lo que pase con
        el tamaño del expediente. El límite de 200 que existía antes cortaba
        expedientes reales a mitad de camino (213 movimientos, sólo bajaba
        200) — la protección contra quedarse sin memoria en expedientes
        gigantes ahora pasa por el purgado periódico y los flags de Chrome,
        no por truncar la descarga.

        Args:
            numero: Numero del expediente (solo para logs)

        Retorna:
            List[dict]: Lista de {path, tipo, movimiento} de archivos descargados
        """
        print(f"\n[DESCARGA POR PAGINAS] Expediente: {numero}")
        print(f"  [INFO] Estrategia: descargar cada pagina antes de navegar (evita WebElements obsoletos)")

        archivos_descargados = []
        pagina_actual = 1
        mov_idx_global = 0
        fallos_auth_consecutivos = 0
        # Si la sesión ya expiró, cada descarga falla con el mismo error de auth.
        # Sin este freno, el pipeline seguiría navegando/intentando cientos de
        # páginas inútilmente (agotando el timeout del worker y la RAM) en vez
        # de abortar apenas detecta que la sesión no sirve.
        MAX_FALLOS_AUTH_CONSECUTIVOS = 3
        fallos_consecutivos_totales = 0
        # Freno adicional para fallos que NO son de sesión expirada (ej: la URL
        # extraída no dispara ninguna descarga real). Sin esto, un expediente de
        # cientos de páginas recorrería TODAS agotando 3 reintentos x 60s en cada
        # una (horas), bloqueando el único slot de descarga del servidor para
        # todos los usuarios, en vez de abortar apenas queda claro que nada baja.
        MAX_FALLOS_CONSECUTIVOS_TOTALES = 5
        # Cada cuántas páginas pedirle a Chrome que libere memoria acumulada
        # (ver _purgar_memoria_chrome). En expedientes de 200+ páginas, Chrome
        # va acumulando memoria a lo largo de la sesión hasta agotar los 512 MB
        # cerca del final, aun con todo funcionando bien.
        PURGAR_MEMORIA_CADA_N_PAGINAS = 5

        try:
            driver = self.cliente.driver

            while True:
                print(f"\n  [PAG {pagina_actual}] Esperando a que cargue la tabla...")

                # Detectar si la sesión de Keycloak expiró (driver redirigido a login)
                url_actual = driver.current_url
                if "ol-sso" in url_actual or "/login" in url_actual:
                    raise Exception(f"SESION_MV_EXPIRADA: sesión expirada durante descarga (pág {pagina_actual}, URL: {url_actual[:80]})")

                # IMPORTANTE: Esperar a que cargue la tabla completamente
                # Puede haber diferentes estructuras según Material-UI/React rendering
                self._esperar_tabla_cargada(driver)

                # Purgar memoria ACA (tabla ya confirmada, contexto JS estable),
                # no apenas se navega: hacerlo a mitad de la transición de página
                # (React todavía re-renderizando/haciendo fetch de la tabla nueva)
                # rompió el execution context del frame en producción (ver
                # _purgar_memoria_chrome).
                if pagina_actual % PURGAR_MEMORIA_CADA_N_PAGINAS == 0:
                    self._purgar_memoria_chrome(driver)

                print(f"  [PAG {pagina_actual}] Buscando botones de descarga...")
                time.sleep(1)

                # 1. Contar botones de descarga de la pagina ACTUAL. No se
                # guardan los WebElements: se re-localizan por índice al
                # momento de cada click (ver _descargar_archivo_selenium),
                # porque React puede re-renderizar la tabla entre descargas
                # y una referencia vieja quedaría "stale".
                cantidad_botones = self._contar_botones_descarga(driver)
                print(f"  [PAG {pagina_actual}] {cantidad_botones} boton(es) de descarga encontrado(s)")

                if cantidad_botones == 0:
                    print(f"  [PAG {pagina_actual}] Sin archivos, terminando")
                    break

                # 2. Descargar TODOS los archivos de esta pagina ANTES de navegar
                for indice_boton in range(cantidad_botones):
                    mov_idx_global += 1

                    nombre_archivo = f"{mov_idx_global:04d}_pag{pagina_actual:02d}.pdf"
                    ruta_archivo = self.carpeta_temp / nombre_archivo

                    print(f"    > [{mov_idx_global}] boton de descarga #{indice_boton}")

                    self._ultimo_fallo_fue_auth = False
                    if self._descargar_archivo_selenium(indice_boton, ruta_archivo):
                        fallos_auth_consecutivos = 0
                        fallos_consecutivos_totales = 0
                        # Detectar tipo real por magic bytes
                        tipo = "pdf"
                        try:
                            with open(ruta_archivo, "rb") as f:
                                magic = f.read(10)
                            if magic.startswith(b"{\\rtf"):
                                ruta_rtf = ruta_archivo.with_suffix(".rtf")
                                ruta_archivo.rename(ruta_rtf)
                                ruta_archivo = ruta_rtf
                                tipo = "rtf"
                                print(f"      [OK] mov {mov_idx_global} (.rtf detectado)")
                            else:
                                print(f"      [OK] mov {mov_idx_global} (.pdf)")
                        except Exception:
                            print(f"      [OK] mov {mov_idx_global}")

                        archivos_descargados.append({
                            "path": ruta_archivo,
                            "tipo": tipo,
                            "movimiento": mov_idx_global,
                        })
                    else:
                        print(f"      [WARN] No se pudo descargar mov {mov_idx_global}")
                        fallos_consecutivos_totales += 1

                        if self._ultimo_fallo_fue_auth:
                            fallos_auth_consecutivos += 1
                            if fallos_auth_consecutivos >= MAX_FALLOS_AUTH_CONSECUTIVOS:
                                raise Exception(
                                    f"SESION_MV_EXPIRADA: {fallos_auth_consecutivos} descargas consecutivas "
                                    f"rechazadas por sesión expirada (mov {mov_idx_global}), abortando"
                                )
                        else:
                            fallos_auth_consecutivos = 0

                        if fallos_consecutivos_totales >= MAX_FALLOS_CONSECUTIVOS_TOTALES:
                            raise Exception(
                                f"DESCARGA_FALLIDA_CONSECUTIVA: {fallos_consecutivos_totales} descargas "
                                f"consecutivas fallidas sin ser por sesión expirada (mov {mov_idx_global}), "
                                f"abortando en vez de recorrer el resto del expediente en vano"
                            )

                # 3. RECIEN AHORA navegar a la siguiente pagina (tokens ya usados)
                hay_siguiente = self._navegar_siguiente_pagina(driver)
                if not hay_siguiente:
                    print(f"\n  [PAG {pagina_actual}] Ultima pagina, terminando")
                    break
                pagina_actual += 1

        except Exception as e:
            print(f"[ERROR] descargar_todo_por_paginas: {str(e)[:100]}")
            logger.error(f"Error en descargar_todo_por_paginas: {e}", exc_info=True)
            if "SESION_MV_EXPIRADA" in str(e):
                # No tragar este error: pipeline.py lo detecta explícitamente para
                # devolver tipo_error='auth_failed' ("Reconectá tu cuenta") en vez
                # de un genérico "no se pudieron descargar archivos".
                raise

        print(f"\n[OK] Total archivos descargados: {len(archivos_descargados)} ({pagina_actual} pagina(s))")
        return archivos_descargados

    def _esperar_tabla_cargada(self, driver, timeout=15):
        """
        Espera a que la tabla de movimientos esté completamente cargada.

        Estrategias (en orden):
        1. Esperar a que exista <table> tag
        2. Esperar a que exista div[role="table"]
        3. Esperar a que exista algún <a> en la tabla
        4. Esperar a que React termine de renderizar

        Args:
            driver: Selenium driver
            timeout: Máximo tiempo a esperar (segundos)
        """
        estrategias = [
            # Estrategia 1: HTML table
            (By.XPATH, "//table", "tabla HTML"),
            # Estrategia 2: Material-UI table
            (By.XPATH, "//div[contains(@class, 'MuiTableContainer')]", "MuiTableContainer"),
            # Estrategia 3: React div con role
            (By.XPATH, "//div[@role='table']", "div[role=table]"),
            # Estrategia 4: Cualquier tabla
            (By.XPATH, "//table | //div[@role='table']", "tabla genérica"),
            # Estrategia 5: Algún enlace en la página
            (By.XPATH, "//table//a[@href] | //div[@role='table']//a[@href]", "enlaces en tabla"),
        ]

        for by_method, selector, descripcion in estrategias:
            try:
                print(f"    > Esperando {descripcion}...")
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((by_method, selector))
                )
                print(f"    [OK] {descripcion} detectado")
                time.sleep(1)  # Pequeño delay adicional para que termine renderizado
                return True
            except:
                continue

        # Si ninguna estrategia funcionó, esperar a que React cargue
        try:
            print(f"    > Esperando renderizado React...")
            WebDriverWait(driver, 5).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            time.sleep(2)
            print(f"    [OK] React renderizado")
            return True
        except:
            pass

        print(f"    [WARN] No se detectó tabla, continuando de todas formas...")
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
