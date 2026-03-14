#!/usr/bin/env python3
"""
TEST EXPEDIENTE 14141 - Prueba de limpieza de filtros de fecha
==============================================================
Busca el expediente 14141 para validar que la limpieza de filtros
está funcionando correctamente.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modulos.login import crear_cliente_sesion
from modulos.navegacion import crear_buscador
from config import MESA_VIRTUAL_URL
import time

def main():
    print("\n" + "="*70)
    print(" TEST: Búsqueda del Expediente 14141")
    print("="*70 + "\n")

    cliente = None
    try:
        # Paso 1: Login
        print("1️⃣ AUTENTICANDO EN MESA VIRTUAL...")
        print("-" * 70)
        cliente = crear_cliente_sesion(url_mesa_virtual=MESA_VIRTUAL_URL)
        print("✅ Autenticación completada\n")

        # Paso 2: Crear buscador
        print("2️⃣ PREPARANDO BUSCADOR...")
        print("-" * 70)
        buscador = crear_buscador(cliente)
        print("✅ Buscador listo\n")

        # Paso 3: Buscar expediente
        print("3️⃣ BUSCANDO EXPEDIENTE 14141...")
        print("-" * 70)
        time.sleep(1)

        expediente = buscador.buscar("14141")

        # Paso 4: Resultado
        print("\n" + "="*70)
        print(" RESULTADO")
        print("="*70 + "\n")

        if expediente:
            print("✅ ¡ÉXITO! Expediente encontrado:\n")
            print(f"    Número: {expediente.get('numero')}")
            print(f"    Carátula: {expediente.get('caratula', 'N/A')}")
            print(f"   ️  Tribunal: {expediente.get('tribunal', 'N/A')}")
            print(f"    Fecha: {expediente.get('fecha', 'N/A')}")
            print("\n" + "="*70)
            print(" VALIDACIÓN EXITOSA")
            print("="*70)
            print("\n✅ La limpieza de filtros FUNCIONA correctamente.")
            print("✅ Puedes proceder a descargar este expediente.\n")
            print("Próximo paso: ejecuta 'python test_flujo_final.py'")
            print("e ingresa 14141 cuando te lo pida.\n")

        else:
            print("❌ NO SE ENCONTRÓ el expediente 14141\n")
            print("Esto puede significar:")
            print("  • El expediente 14141 no existe en Mesa Virtual")
            print("  • El número es incorrecto")
            print("  • Hay un problema con los filtros de fecha")
            print("\nVerifica que el número de expediente sea correcto.")
            print("Si es correcto, contacta al administrador del sistema.\n")

        print("="*70 + "\n")

    except Exception as e:
        import traceback
        print(f"\n❌ ERROR DURANTE LA BÚSQUEDA:\n")
        print(f"   {str(e)}\n")
        print("Traceback completo:")
        print("-" * 70)
        traceback.print_exc()
        print("-" * 70 + "\n")

    finally:
        if cliente:
            try:
                cliente.driver.quit()
                print("✅ Navegador cerrado\n")
            except:
                pass

if __name__ == '__main__':
    main()
