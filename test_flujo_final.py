#!/usr/bin/env python3
"""
TEST FINAL: Flujo completo de descargador de expedientes.

Este script ejecuta el flujo completo:
1. Login en Mesa Virtual
2. Búsqueda de expediente
3. Extracción de movimientos
4. Descarga de archivos
5. Unificación en un solo PDF
6. Limpieza de temporales
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modulos.login import crear_cliente_sesion
from modulos.navegacion import crear_buscador
from modulos.descarga import crear_descargador
from modulos.unificacion import crear_unificador
from config import MESA_VIRTUAL_URL
import time


def main():
    print("═" * 70)
    print("  TEST FINAL: DESCARGADOR DE EXPEDIENTES COMPLETO")
    print("═" * 70)

    cliente = None
    pdf_unificado = None

    try:
        # 1. Login
        print("\n1️⃣  HACIENDO LOGIN...")
        cliente = crear_cliente_sesion(url_mesa_virtual=MESA_VIRTUAL_URL)
        print("   ✅ Login completado\n")

        # 2. Buscar expediente
        print("2️⃣  BUSCANDO EXPEDIENTE...")
        buscador = crear_buscador(cliente)
        numero = input("   Ingresa número de expediente (ej: 12881): ").strip() or "12881"

        expediente = buscador.buscar(numero)
        if not expediente:
            print("   ❌ No se encontró expediente")
            return

        print(f"   ✅ Expediente encontrado\n")

        # 3. Abrir el expediente CORRECTO (según la selección del usuario)
        print("3️⃣  ABRIENDO EXPEDIENTE...")
        driver = cliente.driver
        abierto = False

        # OPCIÓN A: Si se extrajo la URL directa, navegar a ella
        url_directa = expediente.get('url', '')
        if url_directa:
            try:
                driver.get(url_directa)
                time.sleep(4)
                print(f"   ✅ Expediente abierto (URL directa)\n")
                abierto = True
            except Exception as e:
                print(f"   ⚠️  Fallo URL directa ({str(e)[:40]}), intentando por índice...")

        # OPCIÓN B: Usar el índice de resultado para clickear el Nth link
        if not abierto:
            try:
                indice = expediente.get('_resultado_index', 0)
                # Buscar todos los links a expedientes en la página
                enlaces = driver.find_elements("xpath", "//a[contains(@href, '/expedientes/') and string-length(@href) > 20]")
                if enlaces and indice < len(enlaces):
                    enlaces[indice].click()
                    time.sleep(4)
                    print(f"   ✅ Expediente abierto (link índice {indice})\n")
                    abierto = True
                elif enlaces:
                    # Si el índice está fuera de rango, intentar con el primero
                    print(f"   ⚠️  Índice {indice} fuera de rango ({len(enlaces)} links), usando primero disponible")
                    enlaces[0].click()
                    time.sleep(4)
                    abierto = True
                else:
                    # Fallback: buscar divs clickeables de expediente
                    divs = driver.find_elements("xpath", "//div[@aria-label='Clic para abrir']")
                    if divs and indice < len(divs):
                        divs[indice].click()
                        time.sleep(4)
                        print(f"   ✅ Expediente abierto (div índice {indice})\n")
                        abierto = True
            except Exception as e:
                print(f"   ⚠️  Error al abrir por índice: {str(e)[:60]}\n")

        if not abierto:
            print("   ⚠️  No se pudo abrir automáticamente — verificar manualmente\n")

        # 4. Obtener movimientos (CON PAGINACIÓN AUTOMÁTICA)
        print("4️⃣  OBTENIENDO MOVIMIENTOS (con paginación automática)...")
        # Carpeta temporal fuera de OneDrive (evita problemas de sincronización)
        carpeta_temp = Path.home() / "AppData" / "Local" / "Temp" / "mesa_virtual_descarga"

        # Limpiar temp al inicio para evitar mezcla con archivos de sesiones anteriores
        if carpeta_temp.exists():
            import shutil
            shutil.rmtree(carpeta_temp)
            print("   🧹 Carpeta temporal limpiada\n")
        carpeta_temp.mkdir(parents=True, exist_ok=True)

        descargador = crear_descargador(cliente, carpeta_temp=str(carpeta_temp))
        # obtener_movimientos() ahora detecta y procesa TODAS las páginas automáticamente
        movimientos = descargador.obtener_movimientos(expediente.get('numero'))

        if not movimientos:
            print("   ❌ No se encontraron movimientos")
            return

        print(f"   ✅ Se encontraron {len(movimientos)} movimiento(s)\n")

        # 5. Descargar archivos
        print("5️⃣  DESCARGANDO ARCHIVOS...")
        archivos_descargados = descargador.descargar_archivos(
            expediente.get('numero'),
            movimientos
        )

        if not archivos_descargados:
            print("   ❌ No se descargaron archivos")
            return

        print(f"   ✅ Se descargaron {len(archivos_descargados)} archivo(s)\n")

        # 6. Unificar archivos
        print("6️⃣  UNIFICANDO ARCHIVOS EN PDF ÚNICO...")
        # Guardar PDF unificado en la carpeta de descargas del proyecto
        carpeta_salida = Path(__file__).parent / "descargas"
        carpeta_salida.mkdir(parents=True, exist_ok=True)
        unificador = crear_unificador(str(carpeta_temp), str(carpeta_salida))
        pdf_unificado = unificador.unificar(numero, archivos_descargados)

        if not pdf_unificado:
            print("   ❌ Error unificando archivos")
            return

        print(f"   ✅ Archivos unificados\n")

        # 7. Preguntar si limpiar temporales
        print("7️⃣  LIMPIEZA DE ARCHIVOS TEMPORALES")
        respuesta = input("   ¿Deseas eliminar los archivos temporales? (S/n): ").strip().lower()

        # Aceptar cualquier variante de "sí" (si, s, yes, y, etc.)
        limpiar = respuesta not in ['n', 'no', 'nope', 'nah', '']

        if limpiar:
            unificador.limpiar_temporales(mantener_originales=False)
        else:
            print("   ℹ️  Archivos temporales conservados")

        # Referencia a la carpeta temporal correcta
        carpeta_temp_ref = carpeta_temp

        # 8. Resumen final
        print("\n" + "═" * 70)
        print("  ✅ PROCESO COMPLETADO EXITOSAMENTE")
        print("═" * 70)
        print(f"""
📋 EXPEDIENTE: {expediente.get('numero')}
   Carátula: {expediente.get('caratula')}

📊 ESTADÍSTICAS:
   - Movimientos procesados: {len(movimientos)}
   - Archivos descargados: {len(archivos_descargados)}
   - PDF unificado: ✅

📁 ARCHIVO FINAL:
   {pdf_unificado.name}
   Ubicación: {pdf_unificado.parent.resolve()}
   Tamaño: {pdf_unificado.stat().st_size / (1024*1024):.2f} MB

🎉 ¡Descargador completado!
        """)

    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelado por el usuario")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if cliente:
            try:
                cliente.driver.quit()
            except:
                pass


if __name__ == "__main__":
    main()
