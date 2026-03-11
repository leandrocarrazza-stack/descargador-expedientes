#!/usr/bin/env python
"""
Setup: Hacer login manual UNA SOLA VEZ y guardar sesión.

Ejecutar:
  python setup_sesion.py

Esto abrirá Chrome, harás login en Mesa Virtual una sola vez,
y la sesión se guardará en ~/.mesa_virtual_sesion.pkl

Las próximas descargas usarán esa sesión sin necesidad de login manual.
"""

from modulos.login import crear_cliente_sesion
import os
from pathlib import Path

def main():
    archivo_sesion = Path.home() / ".mesa_virtual_sesion.pkl"

    print("="*70)
    print("SETUP: Crear sesión guardada para uso en Celery")
    print("="*70)

    if archivo_sesion.exists():
        print(f"\n[OK] Sesión guardada ya existe en: {archivo_sesion}")
        edad_horas = (os.path.getmtime(archivo_sesion) - __import__('time').time()) / 3600
        print(f"     Antigüedad: {abs(edad_horas):.1f} horas")
        print("\nSi quieres hacer login nuevamente, elimina el archivo:")
        print(f"  rm {archivo_sesion}")
        return

    print("\n[*] No existe sesión guardada. Haciendo login manual...")
    print("    Se abrirá Chrome. Debes completar el login en Mesa Virtual.\n")

    try:
        # Crear cliente CON interfaz visible (no headless)
        cliente = crear_cliente_sesion(usar_sesion_guardada=False)
        print("\n[OK] Setup completado. Sesión guardada para próximas descargas.")
        print(f"     Archivo: {archivo_sesion}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        return 1

    return 0

if __name__ == '__main__':
    exit(main())
