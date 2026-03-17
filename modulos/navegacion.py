"""
MÓDULO 2: Navegación y búsqueda de expediente (Web Scraping)
=============================================================

Busca expedientes navegando Mesa Virtual y extrayendo datos del HTML.
Sin dependencia de GraphQL - funciona con lo que el usuario ve en pantalla.
"""

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time


class BuscadorExpedientes:
    """Cliente para buscar expedientes en la Mesa Virtual (Web Scraping)."""

    def __init__(self, cliente_selenium, timeout=30):
        """
        Inicializa el buscador de expedientes.

        Args:
            cliente_selenium: Instancia de ClienteSelenium con navegador abierto
            timeout: Timeout para esperados (segundos)
        """
        self.cliente = cliente_selenium
        self.timeout = timeout

    def buscar(self, numero, indice_expediente=None):
        """
        Busca un expediente navegando en Mesa Virtual y extrayendo del HTML.

        Adaptado para Next.js con componentes dinámicos que se renderizan con React.

        Args:
            numero: Número del expediente (ej: "22066/14" o "5289")
            indice_expediente: (Opcional) Si hay múltiples resultados, cuál elegir (1-indexed)
                              Si es None, intenta inteligentemente (segundo si hay múltiples)

        Retorna:
            dict o None: El expediente encontrado, o None si no existe

        Lanza:
            Exception: Si hay error en la navegación
        """
        print(f"\n[SEARCH] Buscando expediente: {numero}")

        try:
            driver = self.cliente.driver

            # 1. Navegar a la búsqueda
            print("   > Abriendo buscador...")
            driver.get("https://mesavirtual.jusentrerios.gov.ar/expedientes")

            # Esperar a que React termine de renderizar (máximo 10 segundos)
            print("   > Esperando que cargue la interfaz...")
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            time.sleep(2)  # Tiempo adicional para que React renderice

            # IMPORTANTE: Cerrar cartel de notificaciones PRIMERO (puede bloquear otros clicks)
            print("   > Cerrando popup de notificaciones (si existe)...")
            self._cerrar_cartel_notificaciones(driver)

            # IMPORTANTE: Limpiar filtros de fecha (Mesa Virtual los mantiene activos)
            print("   > Limpiando filtros de fecha...")
            self._limpiar_filtros_fecha(driver)

            # 2. Encontrar y llenar el campo de búsqueda
            # Mesa Virtual usa componentes Material-UI, buscar input de diferentes formas
            search_input = None
            selectors_a_probar = [
                (By.CSS_SELECTOR, "input[placeholder*='expediente']"),
                (By.CSS_SELECTOR, "input[placeholder*='búsqueda']"),
                (By.CSS_SELECTOR, "input[placeholder*='Número']"),
                (By.CSS_SELECTOR, "input[type='search']"),
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.XPATH, "//input[@type='text']"),
                (By.XPATH, "//input[@type='search']"),
            ]

            print("   > Buscando campo de búsqueda...")
            for selector_tipo, selector_valor in selectors_a_probar:
                try:
                    elementos = WebDriverWait(driver, 3).until(
                        EC.presence_of_all_elements_located((selector_tipo, selector_valor))
                    )
                    if elementos:
                        search_input = elementos[0]
                        print(f"      [OK] Campo encontrado con: {selector_tipo.name} = {selector_valor}")
                        break
                except:
                    continue

            if not search_input:
                print("   [WARN]  No se encontró campo de búsqueda en ningún selector")
                self._mostrar_debug_info(driver)
                return None

            # 3. LIMPIAR completamente el campo de búsqueda (para múltiples búsquedas)
            try:
                # Método 1: Clear estándar de Selenium
                search_input.clear()

                # Método 2: Limpiar mediante JavaScript para asegurar que está completamente vacío
                driver.execute_script("""
                    var campo = arguments[0];
                    campo.value = '';
                    campo.dispatchEvent(new Event('input', { bubbles: true }));
                    campo.dispatchEvent(new Event('change', { bubbles: true }));
                """, search_input)

                print(f"   > Campo limpiado completamente")
                time.sleep(0.5)
            except Exception as e:
                print(f"   [WARN]  Error al limpiar campo: {e}")

            # 4. Ingresar el número de expediente
            try:
                search_input.send_keys(numero)
                print(f"   > Ingresado número: {numero}")
                time.sleep(1)

                # Buscar botón de búsqueda (lupa)
                # Mesa Virtual tiene un botón con icono de lupa (SVG)
                boton_encontrado = False
                selectors_boton = [
                    (By.XPATH, "//button[.//svg[@data-testid='SearchIcon']]"),  # Botón con icono Search
                    (By.XPATH, "//button[contains(@aria-label, 'búsqueda') or contains(@aria-label, 'buscar')]"),
                    (By.CSS_SELECTOR, "button[type='submit']"),
                    (By.XPATH, "//button[.//svg]"),  # Cualquier botón con SVG
                ]

                for selector_tipo, selector_valor in selectors_boton:
                    try:
                        boton_buscar = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((selector_tipo, selector_valor))
                        )
                        boton_buscar.click()
                        print(f"   > Clic en botón de búsqueda")
                        boton_encontrado = True
                        break
                    except:
                        continue

                if not boton_encontrado:
                    # Si no encuentra botón, presionar Enter
                    print("   > Presionando Enter (no se encontró botón de búsqueda)")
                    search_input.send_keys("\n")

                time.sleep(4)  # Esperar más tiempo para que carguen resultados

            except Exception as e:
                print(f"   [WARN]  Error al llenar búsqueda: {e}")
                return None

            # 5. Esperar a que se carguen los resultados
            print("   > Esperando resultados...")
            resultados_encontrados = False

            # Intentar con múltiples estrategias de espera
            selectors_resultados = [
                (By.CSS_SELECTOR, "tbody tr"),  # Tabla HTML
                (By.XPATH, "//*[contains(@class, 'MuiTableBody')]//tr"),  # Tabla Material-UI
                (By.XPATH, "//div[contains(@role, 'button') and .//div[contains(text(), '/')]]"),  # Items de expediente
                (By.CSS_SELECTOR, "[role='row']"),  # Filas genéricas
            ]

            for selector_tipo, selector_valor in selectors_resultados:
                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_any_elements_located((selector_tipo, selector_valor))
                    )
                    print(f"      [OK] Resultados detectados")
                    resultados_encontrados = True
                    break
                except:
                    continue

            if not resultados_encontrados:
                # A veces los resultados aparecen pero con estructura diferente
                # Intentar una espera más larga y genérica
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "[class*='Mui']")) > 10
                    )
                    print(f"      [OK] Contenido cargado (estructura diferente)")
                    resultados_encontrados = True
                except:
                    pass

            if not resultados_encontrados:
                print(f"   [WARN]  No se detectaron resultados visibles")
                self._mostrar_debug_info(driver)
                # Aún así continuar intentando extraer del HTML
                time.sleep(2)

            # 5. Extraer los resultados del HTML
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')

            # Buscar expedientes en el HTML
            expedientes = self._extraer_expedientes_del_html(soup, numero)

            if not expedientes:
                print(f"   [NO] No se encontró ningún expediente con el número '{numero}'")
                self._mostrar_debug_info(driver)
                return None

            if len(expedientes) == 1:
                exp = expedientes[0]
                print(f"    Encontrado: {exp.get('caratula', 'Sin descripción')}")
                print(f"      Número: {exp.get('numero')}")
                return exp

            # Si hay múltiples resultados, pedir que el usuario elija
            expediente_elegido = self._elegir_expediente(expedientes, driver, indice_expediente)

            if expediente_elegido:
                # AHORA: Hacer click en el expediente para entrar a los detalles
                print(f"\n   > Entrando a página de detalles del expediente...")
                resultado_index = expediente_elegido.get('_resultado_index', 0)
                self._clickear_expediente(driver, resultado_index)

                # Extraer los movimientos desde la página de detalles
                print(f"   > Extrayendo movimientos...")
                expediente_elegido['movimientos'] = self._extraer_movimientos_detalle(driver)

            return expediente_elegido

        except Exception as e:
            raise Exception(f"Error en búsqueda: {e}")

    def _mostrar_debug_info(self, driver):
        """Muestra información útil para debugging."""
        print("\n   [LIST] INFO PARA DEBUGGING:")
        print(f"      URL actual: {driver.current_url}")
        print(f"      Título: {driver.title}")

        # Intentar encontrar cualquier elemento visible
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')

        # Buscar inputs
        inputs = soup.find_all('input')
        if inputs:
            print(f"      Encontrados {len(inputs)} campos input:")
            for i, inp in enumerate(inputs[:3], 1):
                valor = inp.get('value', '(vacío)')
                placeholder = inp.get('placeholder', '')
                print(f"        [{i}] value='{valor}' placeholder='{placeholder}'")

        # Buscar tablas o contenedores de resultados
        print(f"\n      Buscando tablas/resultados:")
        tablas = soup.find_all('table')
        if tablas:
            print(f"        [OK] Se encontraron {len(tablas)} tabla(s)")
            for i, tabla in enumerate(tablas[:1], 1):
                filas = tabla.find_all('tr')
                print(f"          Tabla {i}: {len(filas)} filas")
        else:
            print(f"        [NO] No se encontraron tablas")

        # Buscar divs con role="button" (Material-UI list items)
        items_boton = soup.find_all(attrs={'role': 'button'})
        if items_boton:
            print(f"        [OK] Se encontraron {len(items_boton)} elementos clickeables")
            for i, item in enumerate(items_boton[:3], 1):
                texto = item.get_text(strip=True)[:50]
                print(f"          [{i}] {texto}")
        else:
            print(f"        [NO] No se encontraron elementos clickeables")

    def _extraer_expedientes_del_html(self, soup, numero_buscado):
        """
        Extrae expedientes del HTML usando BeautifulSoup.

        Mesa Virtual usa Material-UI con estructura de tabla Grid:
        <table> > <tbody> > <tr> > <td> > <div class="MuiGrid-container"> > ...

        Args:
            soup: Objeto BeautifulSoup del HTML
            numero_buscado: Número del expediente buscado

        Retorna:
            list: Lista de expedientes encontrados
        """
        expedientes = []

        # Buscar filas de tabla (especialmente Material-UI)
        # Mesa Virtual usa <tr class="MuiTableRow-root">
        filas = soup.find_all('tr', class_='MuiTableRow-root')

        if not filas:
            # Fallback: buscar cualquier <tr>
            filas = soup.find_all('tr')

        print(f"      > Encontradas {len(filas)} filas en la tabla")

        # Construir lista de índices de filas que corresponden a expedientes
        # (para poder referenciarlas luego con Selenium)
        indice_resultado = 0

        for fila_idx, fila in enumerate(filas, 1):
            # Obtener todo el texto de la fila
            texto_fila = fila.get_text(strip=True)

            # Verificar si contiene el número buscado o partes del mismo
            # (podría estar en formato "exp1: 12881" o "75650/26")
            if numero_buscado.lower() in texto_fila.lower():
                print(f"      [OK] Fila {fila_idx} contiene el número")

                # Extraer información de la estructura de Mesa Virtual
                # La estructura es: exp0: numero_alternativo | exp1: numero_buscado | caratula | etc
                expediente = self._extraer_datos_fila(fila, numero_buscado)

                # Guardar índice (0-based) para poder abrir la fila correcta con Selenium
                if expediente:
                    expediente['_resultado_index'] = indice_resultado
                    indice_resultado += 1

                # Evitar duplicados
                if expediente and expediente not in expedientes:
                    expedientes.append(expediente)

        return expedientes

    def _extraer_datos_fila(self, fila, numero_buscado):
        """
        Extrae datos de una fila de expediente.

        Estructura de Mesa Virtual:
        - Div md-6: Carátula + Info del expediente
        - Div md-2: Números (exp0, exp1, exp2)
        - Div md-1: Ciudad (Gualeguaychú)
        - Div md-3: TRIBUNAL (Juzgado Civil y Comercial 2 o Cámara de Apelaciones)

        Args:
            fila: Elemento <tr> con los datos del expediente
            numero_buscado: Número del expediente para referencia

        Retorna:
            dict: Información del expediente o None
        """
        try:
            # Obtener todo el texto para información general
            texto_completo = fila.get_text(strip=True)

            # Buscar números (exp0, exp1, exp2)
            numeros = {'exp0': '', 'exp1': '', 'exp2': ''}
            spans = fila.find_all('span')

            for span in spans:
                padre = span.parent
                if padre and padre.get('style') and 'word-break' in padre.get('style'):
                    texto_span = padre.get_text(strip=True)
                    if 'exp0:' in texto_span:
                        numeros['exp0'] = span.get_text(strip=True)
                    elif 'exp1:' in texto_span:
                        numeros['exp1'] = span.get_text(strip=True)
                    elif 'exp2:' in texto_span:
                        numeros['exp2'] = span.get_text(strip=True)

            # Obtener descripción/carátula (está en el div con estilo y aria-label)
            caratula = ''
            divs_contenido = fila.find_all('div', attrs={'aria-label': 'Clic para abrir'})
            if divs_contenido:
                # La carátula está en los spans dentro
                spans_contenido = divs_contenido[0].find_all('span')
                caratula = ' '.join([s.get_text(strip=True) for s in spans_contenido])

            # OBTENER TRIBUNAL - está en el último div MuiGrid-item
            # Estructura: ... <div md-1>Ciudad</div> <div md-3>TRIBUNAL</div>
            tribunal = ''
            divs_grid = fila.find_all('div', class_='MuiGrid-item')

            # El tribunal es el último div MuiGrid-item de la fila (después de ciudad)
            if len(divs_grid) >= 4:
                tribunal = divs_grid[-1].get_text(strip=True)

            # Fallback: buscar div específico con clase css-1ha4th6 (tribunal en esta estructura)
            if not tribunal:
                divs_tribunal = fila.find_all('div', class_='css-1ha4th6')
                if divs_tribunal:
                    tribunal = divs_tribunal[0].get_text(strip=True)

            # Intentar extraer URL directa del expediente (si hay <a href=/expedientes/...>)
            url = ''
            links = fila.find_all('a', href=True)
            for link in links:
                href = link.get('href', '')
                if '/expedientes/' in href and len(href) > len('/expedientes/') + 5:
                    url = f"https://mesavirtual.jusentrerios.gov.ar{href}" if href.startswith('/') else href
                    break

            expediente = {
                'numero': numeros['exp1'] if numeros['exp1'] else numeros['exp0'],
                'numero_alt': numeros['exp0'],
                'numero_alt2': numeros['exp2'],
                'caratula': caratula if caratula else texto_completo[:100],
                'tribunal': tribunal if tribunal else 'Tribunal no especificado',
                'texto_completo': texto_completo,
                'url': url,  # URL directa al expediente (puede estar vacía si es SPA)
            }

            return expediente

        except Exception as e:
            print(f"      [WARN]  Error extrayendo datos: {e}")
            return None

    def _cerrar_cartel_notificaciones(self, driver):
        """
        Cierra el cartel de notificaciones de Mesa Virtual.

        Mesa Virtual muestra un modal pidiendo permiso para notificaciones push (OneSignal).
        Este cartel puede bloquear la interfaz de búsqueda.

        Args:
            driver: Driver de Selenium
        """
        try:
            # ESTRATEGIA 0: Cerrar popup específico de OneSignal
            print("        (0) Buscando popup OneSignal específico...")
            try:
                # Buscar el botón "no, gracias" de OneSignal
                botones_onesignal = driver.find_elements(
                    By.ID,
                    "onesignal-slidedown-cancel-button"
                )
                if botones_onesignal:
                    for boton in botones_onesignal:
                        try:
                            if boton.is_displayed():
                                boton.click()
                                time.sleep(1)
                                print("        [OK] Popup OneSignal cerrado")
                                return
                        except:
                            pass
            except:
                pass

            # ESTRATEGIA 1: Buscar botón de cerrar (X)
            print("        (1) Buscando botón de cerrar...")
            try:
                # Buscar botón con aria-label "close" o "cerrar"
                botones_cerrar = driver.find_elements(
                    By.XPATH,
                    "//button[contains(@aria-label, 'close') or contains(@aria-label, 'cerrar') or contains(@aria-label, 'dismiss')]"
                )
                if botones_cerrar:
                    for boton in botones_cerrar:
                        if boton.is_displayed():
                            boton.click()
                            time.sleep(0.5)
                            print("        [OK] Cartel cerrado (botón X)")
                            return
            except:
                pass

            # ESTRATEGIA 2: Buscar botón "Denegar" o "No"
            print("        (2) Buscando botón de denegar...")
            try:
                botones_denegar = driver.find_elements(
                    By.XPATH,
                    "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'no, gracias') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'denegar') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'no') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'cancel')]"
                )
                if botones_denegar:
                    for boton in botones_denegar:
                        if boton.is_displayed():
                            boton.click()
                            time.sleep(0.5)
                            print("        [OK] Cartel cerrado (botón Denegar)")
                            return
            except:
                pass

            # ESTRATEGIA 3: Presionar ESC para cerrar modal
            print("        (3) Presionando ESC...")
            try:
                from selenium.webdriver.common.keys import Keys
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                time.sleep(0.5)
                print("        [OK] Cartel cerrado (ESC)")
                return
            except:
                pass

            # ESTRATEGIA 4: Buscar y cerrar modal mediante JavaScript
            print("        (4) Cerrando modal mediante JavaScript...")
            try:
                driver.execute_script("""
                    // Buscar modals o dialogs visibles
                    var modals = document.querySelectorAll('[role="dialog"], .MuiModal-root, .MuiBackdrop-root');
                    modals.forEach(function(modal) {
                        if (modal.style.display !== 'none') {
                            // Buscar botón de cerrar dentro
                            var closeBtn = modal.querySelector('button[aria-label*="close"], button[aria-label*="cerrar"], button:nth-child(1)');
                            if (closeBtn) {
                                closeBtn.click();
                            } else {
                                // Si no hay botón, esconder el modal
                                modal.style.display = 'none';
                            }
                        }
                    });

                    // También buscar backdrop y cerrarlo
                    var backdrop = document.querySelector('.MuiBackdrop-root');
                    if (backdrop) {
                        backdrop.style.display = 'none';
                    }

                    return true;
                """)
                print("        [OK] Cartel cerrado (JavaScript)")
                return
            except:
                pass

            print("        [INFO] No se encontró cartel de notificaciones (ya está cerrado o no existe)")

        except Exception as e:
            print(f"        [WARN] Error al intentar cerrar cartel: {e}")
            print("        [INFO] Continuando de todas formas...")

    def _limpiar_filtros_fecha(self, driver):
        """
        Limpia los filtros de fecha activos en Mesa Virtual.

        IMPORTANTE: El filtro de fecha está VISIBLE en la interfaz.
        Necesitamos buscar el botón "Limpiar filtros" y hacer click en él.

        Args:
            driver: Driver de Selenium
        """
        try:
            print("      > Buscando botón 'Limpiar filtros'...")
            time.sleep(1)

            # ESTRATEGIA 1: Buscar botón por texto exacto (con diferentes variantes)
            print("        (1) Buscando por texto 'Limpiar'...")
            try:
                # Intentar encontrar botones con "Limpiar" en el texto (case-insensitive)
                selectors_limpiar = [
                    "//button[contains(text(), 'Limpiar')]",
                    "//button[contains(text(), 'limpiar')]",
                    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'limpiar')]",
                    "//button/span[contains(text(), 'Limpiar')]/..",
                    "//button[.//text()[contains(., 'Limpiar')]]",
                ]

                for selector in selectors_limpiar:
                    try:
                        botones = driver.find_elements(By.XPATH, selector)
                        if botones:
                            for boton in botones:
                                try:
                                    if boton.is_displayed():
                                        print(f"           [OK] Botón encontrado: '{boton.text[:30]}'")

                                        # Intentar click directo primero
                                        try:
                                            boton.click()
                                            print("           [OK] Click directo realizado")
                                            time.sleep(2)
                                            return
                                        except Exception as e:
                                            # Si falla, usar JavaScript
                                            print(f"           [WARN]  Click directo falló, usando JavaScript: {str(e)[:30]}")
                                            driver.execute_script("arguments[0].click();", boton)
                                            print("           [OK] Click JavaScript realizado")
                                            time.sleep(2)
                                            return

                                except Exception as e:
                                    print(f"           [NO] Error al clickear: {str(e)[:20]}")
                    except:
                        pass
            except Exception as e:
                print(f"        [NO] Error en búsqueda de texto: {str(e)[:30]}")

            # ESTRATEGIA 2: Buscar por atributos y aria-labels comunes
            print("        (2) Buscando por aria-label...")
            try:
                selectors_aria = [
                    "//button[@aria-label*='Limpiar']",
                    "//button[@aria-label*='limpiar']",
                    "//button[@title*='Limpiar']",
                    "//button[@title*='limpiar']",
                ]

                for selector in selectors_aria:
                    try:
                        botones = driver.find_elements(By.XPATH, selector)
                        if botones:
                            for boton in botones:
                                try:
                                    if boton.is_displayed():
                                        print(f"           [OK] Botón encontrado")
                                        try:
                                            boton.click()
                                            print("           [OK] Click realizado")
                                            time.sleep(2)
                                            return
                                        except:
                                            driver.execute_script("arguments[0].click();", boton)
                                            print("           [OK] Click JavaScript realizado")
                                            time.sleep(2)
                                            return
                                except:
                                    pass
                    except:
                        pass
            except Exception as e:
                print(f"        [NO] Error en búsqueda por aria-label: {str(e)[:30]}")

            # ESTRATEGIA 3: Buscar todos los botones y usar JavaScript para clickear
            print("        (3) Buscando botones con JavaScript...")
            try:
                # Usar JavaScript para encontrar y clickear el botón de manera más robusta
                resultado = driver.execute_script("""
                    const botones = Array.from(document.querySelectorAll('button'));
                    const botonLimpiar = botones.find(b => b.textContent.toLowerCase().includes('limpiar'));
                    if (botonLimpiar && botonLimpiar.offsetParent !== null) {
                        botonLimpiar.click();
                        return 'clickeado';
                    }
                    return 'no_encontrado';
                """)

                if resultado == 'clickeado':
                    print("           [OK] Botón encontrado y clickeado con JavaScript")
                    time.sleep(2)
                    return
                else:
                    print(f"           [INFO]  Botón no encontrado con JavaScript")
            except Exception as e:
                print(f"        [NO] Error: {str(e)[:30]}")

            # ESTRATEGIA 4: Si no encuentra botón, hacer reload limpio
            print("        (4) Botón no encontrado, haciendo reload de página...")
            try:
                driver.get("https://mesavirtual.jusentrerios.gov.ar/expedientes/")
                time.sleep(2)
                print("        [OK] Página recargada")
            except Exception as e:
                print(f"        [NO] Error: {str(e)[:30]}")

            print("      [INFO]  Continuando con la búsqueda...")

        except Exception as e:
            print(f"      [WARN] Error general al limpiar filtros: {e}")
            print("      [INFO]  Continuando la búsqueda...")

    def _elegir_expediente(self, expedientes, driver, indice_expediente=None):
        """
        Selecciona un expediente de la lista.

        Si hay solo 1, lo devuelve automáticamente.
        Si hay múltiples y se especifica indice_expediente, usa ese.
        Si hay múltiples y NO se especifica, intenta inteligentemente:
          - Intenta segundo (índice 1) primero
          - Si falla, intenta el primero
        En modo interactivo, el usuario puede elegir.

        Args:
            expedientes: Lista de expedientes encontrados
            driver: Driver de Selenium
            indice_expediente: (Opcional) Índice 1-based a usar si hay múltiples

        Retorna:
            dict: El expediente elegido, o None si hay error
        """
        if not expedientes:
            return None

        if len(expedientes) == 1:
            print(f"\n   [OK] Un expediente encontrado, usando automáticamente")
            return expedientes[0]

        print(f"\n   Se encontraron {len(expedientes)} expediente(s):\n")

        for i, exp in enumerate(expedientes, 1):
            caratula = exp.get('caratula', 'Sin descripción')[:60]
            numero = exp.get('numero', 'N/A')
            tribunal = exp.get('tribunal', 'Tribunal no especificado')[:40]
            print(f"   [{i}] {caratula}")
            print(f"       Numero: {numero} | Tribunal: {tribunal}\n")

        # Si se especifica índice, usar ese
        if indice_expediente is not None:
            if 1 <= indice_expediente <= len(expedientes):
                exp_elegido = expedientes[indice_expediente - 1]
                print(f"   [SELEC] Usando expediente #{indice_expediente}")
                return exp_elegido
            else:
                print(f"   [WARN] Índice {indice_expediente} inválido, usando el segundo si existe")

        # Intentar leer input del usuario, si no hay (EOF), usar estrategia automática
        try:
            opcion = input(f"   Ingresa el numero (1-{len(expedientes)}, default=1): ").strip()
            if not opcion:
                opcion = "1"
            numero = int(opcion)
            if 1 <= numero <= len(expedientes):
                return expedientes[numero - 1]
            print(f"   [WARN] Numero invalido, usando estrategia automática")
        except (EOFError, ValueError):
            print(f"   [AUTO] Modo automático - eligiendo inteligentemente")

        # Estrategia automática: probar segundo si existe, luego primero
        if len(expedientes) > 1:
            print(f"   [AUTO] Intentando con expediente #2 primero (más probable que tenga archivos)")
            return expedientes[1]
        else:
            print(f"   [AUTO] Usando primer expediente")
            return expedientes[0]

    def _clickear_expediente(self, driver, resultado_index, expediente_elegido=None):
        """
        Hace click en un link de expediente para entrar a los detalles.

        Busca los links reales del expediente usando XPath y hace click en el correcto.

        Args:
            driver: Selenium WebDriver
            resultado_index: Índice (0-based) del resultado
            expediente_elegido: (Opcional) Dict con datos del expediente
        """
        try:
            # METODO 1: Si el expediente tiene URL directa, usarla
            url_directa = expediente_elegido.get('url', '') if expediente_elegido else ""
            if url_directa:
                print(f"      [INFO] Usando URL directa del expediente...")
                driver.get(url_directa)
                time.sleep(4)
                print(f"      [OK] Página de detalles cargada (URL directa)")
                return

            # METODO 2: Buscar links reales del expediente usando XPath
            print(f"      [INFO] Buscando links de expedientes...")
            WebDriverWait(driver, 5).until(
                EC.presence_of_all_elements_located((By.XPATH, "//a[contains(@href, '/expedientes/') and string-length(@href) > 20]"))
            )

            enlaces = driver.find_elements(By.XPATH, "//a[contains(@href, '/expedientes/') and string-length(@href) > 20]")

            if not enlaces:
                print(f"      [ERROR] No se encontraron links de expedientes")
                return

            if resultado_index >= len(enlaces):
                print(f"      [WARN] Indice {resultado_index} > {len(enlaces) - 1}, usando ultimo")
                resultado_index = len(enlaces) - 1

            # Hacer click en el link correcto
            enlace_objetivo = enlaces[resultado_index]
            driver.execute_script("arguments[0].scrollIntoView(true);", enlace_objetivo)
            time.sleep(0.5)

            print(f"      [INFO] Clickeando en link #{resultado_index + 1}...")
            print(f"           Texto: {enlace_objetivo.text[:60]}...")

            try:
                enlace_objetivo.click()
            except:
                driver.execute_script("arguments[0].click();", enlace_objetivo)

            print(f"      [OK] Click en link #{resultado_index + 1}")
            time.sleep(4)

            # Esperar a que la página cargue
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            print(f"      [OK] Página de detalles cargada")

        except Exception as e:
            print(f"      [ERROR] Click falló: {e}")

    def _extraer_movimientos_detalle(self, driver):
        """
        Extrae los movimientos desde la página de detalles del expediente.

        Extrae movimientos Y sus enlaces de descarga (como hace el módulo descarga).
        La pestaña Movimientos debería estar activa por defecto.

        Returns:
            list: Lista de movimientos con enlaces_descarga [{href, texto}, ...]
        """
        try:
            # Esperar MUCHO MAS tiempo a que React renderice (pueden ser segundos)
            print(f"      [INFO] Esperando renderizado de movimientos (React)...")
            time.sleep(10)  # Espera más larga para React

            # Esperar a que haya FILAS en la tabla (no solo el header)
            # Usar JavaScript para validar que haya contenido real
            def hay_movimientos(driver):
                try:
                    # Intentar encontrar filas en tabla
                    filas = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                    if len(filas) > 1:  # Más de 1 = más que header
                        return True

                    # Fallback: buscar [role='row']
                    filas_role = driver.find_elements(By.CSS_SELECTOR, "[role='row']")
                    if len(filas_role) > 1:
                        return True

                    # Último fallback: usar JavaScript
                    tiene_contenido = driver.execute_script("""
                        const filas = document.querySelectorAll('table tbody tr, [role="row"]');
                        return filas.length > 1;
                    """)
                    return tiene_contenido
                except:
                    return False

            WebDriverWait(driver, 15).until(hay_movimientos)

            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')

            movimientos = []

            # Buscar la tabla de movimientos
            # En Mesa Virtual, puede ser:
            # 1. <table> con <tr> tradicionales
            # 2. Divs con role="grid" o role="table"
            # 3. Estructura Material-UI con [role='row']

            tablas = soup.find_all('table')
            if not tablas:
                # Fallback: buscar estructuras React/Material-UI
                filas_grid = soup.find_all(attrs={'role': 'row'})
                print(f"      [INFO] No hay <table>, encontradas {len(filas_grid)} filas con role='row'")
                if filas_grid:
                    # Procesar como grid
                    for fila in filas_grid[1:]:  # Skip header
                        celdas = fila.find_all(['td', 'div'], {'role': 'gridcell'})
                        if not celdas:
                            celdas = fila.find_all(['div'])
                        # Aquí procesaríamos las celdas
                    tablas = [soup]  # Marker para entrar al loop

            print(f"      [INFO] Encontradas {len(tablas)} tabla(s) en la página")

            for tabla_idx, tabla in enumerate(tablas):
                filas = tabla.find_all('tr')[1:]  # Skip header

                if not filas:
                    continue

                print(f"      [INFO] Tabla {tabla_idx}: {len(filas)} fila(s)")

                for fila_idx, fila in enumerate(filas):
                    # Extraer células
                    celdas = fila.find_all('td')

                    if len(celdas) < 4:
                        continue

                    # Estructura: Fecha | Tipo | Fojas | Descripción | Opciones (botones/enlaces)
                    fecha = celdas[0].get_text(strip=True) if len(celdas) > 0 else ""
                    tipo = celdas[1].get_text(strip=True) if len(celdas) > 1 else ""
                    fojas = celdas[2].get_text(strip=True) if len(celdas) > 2 else ""
                    descripcion = celdas[3].get_text(strip=True) if len(celdas) > 3 else ""

                    # Buscar enlaces de descarga en toda la fila
                    enlaces = fila.find_all('a')
                    enlaces_descarga = []

                    for enlace in enlaces:
                        href = enlace.get('href', '')
                        texto_enlace = enlace.get_text(strip=True)

                        # Saltar enlaces de previsualización (texto exacto "PDF" o "RTF")
                        if texto_enlace.upper() in ('PDF', 'RTF'):
                            continue

                        if href:  # Si tiene href
                            enlaces_descarga.append({
                                'href': href,
                                'texto': texto_enlace,
                                'es_pdf': 'pdf' in href.lower() or texto_enlace.upper() == 'PDF',
                            })

                    # Crear movimiento solo si tiene enlaces de descarga
                    if enlaces_descarga:
                        movimiento = {
                            'fecha': fecha,
                            'tipo': tipo,
                            'fojas': fojas,
                            'descripcion': descripcion,
                            'enlaces_descarga': enlaces_descarga,
                            'indice': fila_idx + 1
                        }

                        movimientos.append(movimiento)

                        if movimiento['descripcion']:
                            print(f"        [{fila_idx + 1}] {fecha} - {descripcion[:40]} ({len(enlaces_descarga)} archivo(s))")

            print(f"      [OK] Total movimientos con archivos: {len(movimientos)}")
            return movimientos

        except Exception as e:
            print(f"      [ERROR] Error extrayendo movimientos: {e}")
            import traceback
            traceback.print_exc()
            return []


def crear_buscador(cliente_selenium, api_graphql_url=None):
    """
    Función auxiliar para crear un buscador preconfigurado.

    Args:
        cliente_selenium: Cliente Selenium autenticado
        api_graphql_url: (no se usa con web scraping, solo para compatibilidad)

    Retorna:
        BuscadorExpedientes: Buscador listo para usar
    """
    return BuscadorExpedientes(cliente_selenium)


if __name__ == "__main__":
    # Prueba rápida
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from login import crear_cliente_sesion
    from config import MESA_VIRTUAL_URL

    try:
        cliente = crear_cliente_sesion(url_mesa_virtual=MESA_VIRTUAL_URL)
        buscador = crear_buscador(cliente)
        expediente = buscador.buscar("22066")
        if expediente:
            print(f"\n Expediente obtenido: {expediente['numero']}")
    except Exception as e:
        print(f"\n[NO] Error: {e}")
        sys.exit(1)
