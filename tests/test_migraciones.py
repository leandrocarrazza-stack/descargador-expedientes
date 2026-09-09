#!/usr/bin/env python3
"""
Tests de las migraciones ligeras (modulos/migraciones.py).

Simula una BD "vieja" (tablas creadas sin las columnas nuevas de
models.py) y verifica que aplicar_migraciones_ligeras() las agregue, sin
tocar datos existentes, y que correrla una segunda vez no falle
(idempotencia — es lo que corre en cada arranque de la app).

No toca Selenium ni red: SQLite en memoria. `python test_migraciones.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.FLASK_ENV = 'testing'

from flask import Flask
from modulos.database import db
from modulos.migraciones import aplicar_migraciones_ligeras

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


# Esquema "viejo": las tablas tal como existían antes de este lote, sin
# ninguna de las columnas nuevas declaradas en modulos/models.py.
_DDL_VIEJO = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(120) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    nombre VARCHAR(255),
    plan VARCHAR(50) NOT NULL DEFAULT 'free',
    is_admin BOOLEAN NOT NULL DEFAULT 0,
    creditos_disponibles INTEGER NOT NULL DEFAULT 0,
    creditos_usados_mes INTEGER NOT NULL DEFAULT 0,
    fecha_reset_creditos DATETIME,
    creado_en DATETIME NOT NULL,
    actualizado_en DATETIME
);

CREATE TABLE expedientes_descargados (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    numero VARCHAR(50) NOT NULL,
    caratula VARCHAR(500),
    tribunal VARCHAR(255),
    pdf_ruta_temporal VARCHAR(500),
    estado VARCHAR(50) NOT NULL DEFAULT 'pending',
    porcentaje INTEGER DEFAULT 0,
    error_msg TEXT,
    creado_en DATETIME NOT NULL,
    completado_en DATETIME
);
"""


def _app_con_bd_vieja():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        with db.engine.begin() as conn:
            for statement in _DDL_VIEJO.strip().split(';'):
                statement = statement.strip()
                if statement:
                    conn.exec_driver_sql(statement)
    return app


def _columnas(db, tabla):
    from sqlalchemy import inspect
    return {c['name'] for c in inspect(db.engine).get_columns(tabla)}


def test_agrega_columnas_faltantes():
    app = _app_con_bd_vieja()
    with app.app_context():
        antes_users = _columnas(db, 'users')
        check("notificar_email NO existe antes de migrar",
              'notificar_email' not in antes_users)

        aplicar_migraciones_ligeras(db)

        despues_users = _columnas(db, 'users')
        despues_exp = _columnas(db, 'expedientes_descargados')

        check("users.notificar_email agregada", 'notificar_email' in despues_users)
        check("users.plan_max_comprado agregada", 'plan_max_comprado' in despues_users)
        check("expedientes_descargados.storage_key agregada", 'storage_key' in despues_exp)
        check("expedientes_descargados.total_filas agregada", 'total_filas' in despues_exp)
        check("expedientes_descargados.huellas_json agregada", 'huellas_json' in despues_exp)
        check("expedientes_descargados.ultimo_acceso_en agregada", 'ultimo_acceso_en' in despues_exp)


def test_idempotente_no_falla_segunda_vez():
    app = _app_con_bd_vieja()
    with app.app_context():
        aplicar_migraciones_ligeras(db)
        try:
            aplicar_migraciones_ligeras(db)
            ok = True
        except Exception as e:
            ok = False
            print(f"    excepción: {e}")
        check("segunda corrida no falla (columnas ya existen)", ok)


def test_no_pisa_datos_existentes():
    app = _app_con_bd_vieja()
    with app.app_context():
        db.session.execute(db.text(
            "INSERT INTO users (email, password_hash, creado_en) "
            "VALUES ('a@b.com', 'hash', '2024-01-01')"
        ))
        db.session.commit()

        aplicar_migraciones_ligeras(db)

        fila = db.session.execute(db.text(
            "SELECT email, notificar_email FROM users WHERE email='a@b.com'"
        )).fetchone()
        check("la fila existente se conserva", fila is not None and fila[0] == 'a@b.com')
        check("la columna nueva es NULL en filas viejas", fila is not None and fila[1] is None)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE MIGRACIONES LIGERAS")
    print("=" * 70)

    test_agrega_columnas_faltantes()
    test_idempotente_no_falla_segunda_vez()
    test_no_pisa_datos_existentes()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
