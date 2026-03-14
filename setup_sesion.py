#!/usr/bin/env python3
"""
setup_sesion.py
===============

Script para renovar la sesión guardada de Mesa Virtual.

Uso:
    python setup_sesion.py

Pasos:
    1. Abre Chrome
    2. Navega a Mesa Virtual
    3. Tu completas el login en el navegador
    4. El script guarda las cookies
    5. Listo para usar en el pipeline

Nota: El timeout es de 5 minutos. Si necesitas 2FA, sigue los pasos en el navegador.
"""

import sys
from pathlib import Path
from modulos.login import ClienteSelenium


def main():
    print("\n" + "="*70)
    print("RENOVAR SESION DE MESA VIRTUAL")
    print("="*70 + "\n")

    cliente = ClienteSelenium()

    # Paso 1: Abrir navegador y loguarse
    print("[PASO 1] Abriendo navegador Chrome...")
    print("-" * 70)
    exito_login = cliente.abrir_navegador_y_loguearse()

    if not exito_login:
        print("\n[ERROR] No se pudo completar el login")
        return False

    # Paso 2: Guardar sesión
    print("\n[PASO 2] Guardando sesión...")
    print("-" * 70)
    exito_guardar = cliente.guardar_sesion()

    if not exito_guardar:
        print("\n[ERROR] No se pudo guardar la sesión")
        return False

    # Paso 3: Cerrar navegador
    print("\n[PASO 3] Cerrando navegador...")
    print("-" * 70)
    try:
        cliente.cerrar()
        print("[OK] Navegador cerrado correctamente")
    except Exception as e:
        print(f"[WARN] Error al cerrar navegador: {e}")

    # Resumen
    archivo_sesion = Path.home() / ".mesa_virtual_sesion.pkl"
    if archivo_sesion.exists():
        tamanio = archivo_sesion.stat().st_size
        print(f"\n[OK] ¡SESIÓN RENOVADA EXITOSAMENTE!")
        print(f"   Archivo: {archivo_sesion}")
        print(f"   Tamanio: {tamanio} bytes")
        print(f"\n   Ahora puedes usar /descargas/expediente para descargar\n")
        return True
    else:
        print(f"\n[ERROR] Archivo de sesión no encontrado: {archivo_sesion}")
        return False


if __name__ == "__main__":
    try:
        exito = main()
        sys.exit(0 if exito else 1)
    except KeyboardInterrupt:
        print("\n\n[PAUSE] Cancelado por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
