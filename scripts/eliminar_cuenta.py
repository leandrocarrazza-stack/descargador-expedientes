#!/usr/bin/env python3
"""
Script para dar de baja una cuenta a pedido del usuario.

Uso (desde la raíz del proyecto, con las mismas variables de entorno que el
servidor: DATABASE_URL, R2_*, ENCRYPTION_KEY, etc.):

    python scripts/eliminar_cuenta.py usuario@ejemplo.com                # solo muestra qué haría
    python scripts/eliminar_cuenta.py usuario@ejemplo.com --confirmar    # ejecuta la baja
    python scripts/eliminar_cuenta.py --id 42 --confirmar                # por id de usuario

Opciones:
    --confirmar        Ejecuta la baja. Sin esta opción no se modifica nada.
    --id N             Identifica la cuenta por su id en vez del correo.
    --borrar-compras   Borra también los registros de compras. Por defecto, si la
                       cuenta tiene compras se conservan (obligaciones contables e
                       impositivas) y la cuenta queda anonimizada.

Qué se borra: PDF (storage y caché local), historial de expedientes, sesión de
Mesa Virtual, tokens de recuperación y mensajes de contacto. Ver el detalle en
modulos/baja_cuenta.py.

Qué NO se toca: los registros técnicos (logs), las copias de seguridad del
proveedor de hosting y los datos que conserven Mercado Pago o el proveedor de
correo. Conviene ejecutarlo cuando el usuario no tenga una descarga en curso.

Este script no levanta la app completa (servidor.py): ese arranque limpia
procesos de conversión y de Chrome de otros usuarios, algo que no corresponde
hacer acá.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask

import config
from modulos.database import db
from modulos.models import User
from modulos.baja_cuenta import BajaNoPermitida, dar_de_baja, resumen_baja


def _crear_app_minima():
    app = Flask(__name__)
    app.config.from_object(config)
    db.init_app(app)
    return app


def _mostrar_resumen(resumen):
    print(f"  - Expedientes en el historial: {resumen['expedientes']}")
    print(f"  - PDF en el storage:           {len(resumen['keys_storage'])}")
    print(f"  - PDF en el caché local:       {len(resumen['archivos_locales'])}")
    print(f"  - Sesión de Mesa Virtual:      {resumen['sesion_mv']}")
    print(f"  - Tokens de recuperación:      {resumen['tokens_reset']}")
    print(f"  - Mensajes de contacto:        {resumen['mensajes_contacto']}")
    print(f"  - Compras de créditos:         {resumen['compras']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Da de baja una cuenta de Foja a pedido del usuario.")
    parser.add_argument('email', nargs='?', help="Correo de la cuenta")
    parser.add_argument('--id', type=int, help="Id de la cuenta (alternativa al correo)")
    parser.add_argument('--confirmar', action='store_true', help="Ejecuta la baja (sin esto solo muestra qué haría)")
    parser.add_argument('--borrar-compras', action='store_true', help="Borra también los registros de compras")
    args = parser.parse_args(argv)

    if bool(args.email) == bool(args.id):
        parser.error("Indicá el correo o --id (uno solo).")

    app = _crear_app_minima()
    with app.app_context():
        if args.id:
            usuario = db.session.get(User, args.id)
        else:
            usuario = User.query.filter_by(email=args.email.strip().lower()).first()
        if not usuario:
            print("ERROR: no existe esa cuenta.")
            return 1

        print(f"Cuenta {usuario.id}: {usuario.email}")
        resumen = resumen_baja(usuario)
        _mostrar_resumen(resumen)

        if usuario.is_admin:
            print("ERROR: la cuenta es administradora. Quitale el rol (is_admin) antes de darla de baja.")
            return 1

        if resumen['compras'] and not args.borrar_compras:
            print("\nTiene compras: se conservan y la cuenta queda ANONIMIZADA (sin correo, nombre ni IP).")
        else:
            print("\nSe BORRA la cuenta entera (incluidos los registros de compras, si los hubiera).")

        if not args.confirmar:
            print("\nNo se modificó nada. Para ejecutar la baja agregá --confirmar.")
            return 0

        try:
            resultado = dar_de_baja(usuario, borrar_compras=args.borrar_compras)
        except BajaNoPermitida as e:
            print(f"ERROR: {e}")
            return 1

        print(f"\nOK: cuenta {resultado['resultado']}.")
        if resultado['keys_restantes']:
            print(f"ATENCIÓN: {len(resultado['keys_restantes'])} archivo(s) siguen en el storage; "
                  "revisalos a mano:")
            for key in resultado['keys_restantes']:
                print(f"   {key}")
            return 2
        print("Recordá: los registros técnicos (logs) y las copias de seguridad del proveedor no se tocan.")
        return 0


if __name__ == '__main__':
    sys.exit(main())
