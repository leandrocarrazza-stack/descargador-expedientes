#!/usr/bin/env python3
"""
Script para asignar el rol de administrador a una cuenta.

Uso:
    python scripts/set_admin.py leofard@gmail.com

Descripción:
    - Marca la cuenta como is_admin=True en la base de datos.
    - Los admins pueden descargar sin gastar créditos.
    - Los admins pueden otorgar créditos gratuitos a otros usuarios desde /admin/
    - Si la cuenta no existe, muestra un error.
"""

import sys
import os
from pathlib import Path

# Agregar el directorio raíz del proyecto al path para poder importar los módulos
sys.path.insert(0, str(Path(__file__).parent.parent))

from servidor import app
from modulos.database import db
from modulos.models import User


def set_admin(email: str):
    """Busca al usuario por email y le asigna is_admin=True."""
    with app.app_context():
        usuario = User.query.filter_by(email=email.strip().lower()).first()

        if not usuario:
            print(f"ERROR: No existe ninguna cuenta con el email '{email}'.")
            print("Asegurate de que el usuario se haya registrado primero.")
            sys.exit(1)

        if usuario.is_admin:
            print(f"INFO: '{email}' ya es admin. No se realizaron cambios.")
            sys.exit(0)

        # Asignar rol admin
        usuario.is_admin = True
        db.session.commit()

        print(f"OK: '{email}' ahora es admin.")
        print(f"   - Puede descargar expedientes sin gastar créditos.")
        print(f"   - Puede otorgar créditos desde http://localhost:5000/admin/")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Uso: python scripts/set_admin.py <email>")
        print("Ejemplo: python scripts/set_admin.py leofard@gmail.com")
        sys.exit(1)

    email_arg = sys.argv[1]
    set_admin(email_arg)
