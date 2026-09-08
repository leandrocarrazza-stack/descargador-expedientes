#!/usr/bin/env python3
"""
Tests del backend local de storage (modulos/storage.py).

El backend R2 (boto3) no se prueba acá (requiere credenciales reales) —
solo el fallback local, que es el que corre en desarrollo y en producción
sin R2 configurado. `python test_storage.py`.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.storage import StorageLocal, key_pdf_usuario

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def test_guardar_y_descargar():
    with tempfile.TemporaryDirectory() as tmp:
        storage = StorageLocal(Path(tmp) / "store")

        origen = Path(tmp) / "original.pdf"
        origen.write_bytes(b"%PDF-1.4 contenido de prueba")

        key = key_pdf_usuario(42, "1234/2024", "tok123")
        check("key tiene el formato esperado",
              key == "usuarios/42/expedientes/1234_2024-tok123.pdf", key)

        check("no existe antes de guardar", not storage.existe(key))

        storage.guardar(str(origen), key)
        check("existe después de guardar", storage.existe(key))

        destino = Path(tmp) / "descargado.pdf"
        ok = storage.descargar(key, str(destino))
        check("descargar() retorna True", ok)
        check("el contenido coincide", destino.read_bytes() == origen.read_bytes())


def test_descargar_key_inexistente():
    with tempfile.TemporaryDirectory() as tmp:
        storage = StorageLocal(Path(tmp) / "store")
        destino = Path(tmp) / "no_deberia_existir.pdf"
        ok = storage.descargar("usuarios/1/expedientes/no-existe.pdf", str(destino))
        check("descargar() retorna False si la key no existe", not ok)
        check("no crea el archivo destino", not destino.exists())


def test_borrar():
    with tempfile.TemporaryDirectory() as tmp:
        storage = StorageLocal(Path(tmp) / "store")
        origen = Path(tmp) / "original.pdf"
        origen.write_bytes(b"contenido")

        key = "usuarios/1/expedientes/1-tok.pdf"
        storage.guardar(str(origen), key)
        check("existe antes de borrar", storage.existe(key))

        storage.borrar(key)
        check("no existe después de borrar", not storage.existe(key))

        # Borrar una key que ya no existe no debe lanzar.
        try:
            storage.borrar(key)
            ok = True
        except Exception as e:
            ok = False
            print(f"    excepción: {e}")
        check("borrar() es idempotente (no lanza en la segunda llamada)", ok)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE STORAGE (backend local)")
    print("=" * 70)

    test_guardar_y_descargar()
    test_descargar_key_inexistente()
    test_borrar()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
