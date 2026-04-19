#!/usr/bin/env python3
"""
Script de migración: Cifra cookies existentes en la BD.

Ejecutar ANTES de deployar el código que usa set_cookies/get_cookies:
    python scripts/encrypt_existing_cookies.py

Requiere ENCRYPTION_KEY configurada en .env
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.database import db
from modulos.models import SesionUsuarioMV
from server import crear_app

def migrate_cookies():
    app = crear_app()
    with app.app_context():
        sesiones = SesionUsuarioMV.query.all()
        if not sesiones:
            print("✓ No hay sesiones para cifrar.")
            return

        migrated = 0
        for sesion in sesiones:
            try:
                # Parsear JSON plano para verificar integridad
                cookies_dict = json.loads(sesion.cookies_json)
                # Re-guardar con cifrado
                sesion.set_cookies(cookies_dict)
                migrated += 1
            except json.JSONDecodeError:
                print(f"✗ Sesión {sesion.id}: JSON inválido, saltando.")
                continue
            except Exception as e:
                print(f"✗ Sesión {sesion.id}: {e}")
                continue

        db.session.commit()
        print(f"✓ {migrated}/{len(sesiones)} sesiones cifradas exitosamente.")

if __name__ == '__main__':
    migrate_cookies()
