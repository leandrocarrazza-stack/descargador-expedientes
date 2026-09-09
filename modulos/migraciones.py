"""
Migraciones ligeras
====================

Esta app no usa Flask-Migrate/Alembic para migraciones reales: nunca se
generó una migración y `servidor.py` solo llama `db.create_all()`, que
crea tablas que faltan pero NUNCA agrega columnas a una tabla que ya
existe. Cualquier columna nueva sobre una tabla en producción quedaría
huérfana (el modelo la conoce, la tabla no) hasta que alguien corra un
ALTER TABLE a mano.

`aplicar_migraciones_ligeras(db)` reemplaza eso con algo mínimo y explícito:
una lista de (tabla, columna) que se espera que existan, comparada contra
lo que el motor realmente tiene (`sqlalchemy.inspect`), agregando por ALTER
TABLE las que falten.

Reglas de diseño (no las rompas al agregar columnas nuevas):
- Todas las columnas son NULLABLE y sin server-default. SQLite no soporta
  agregar una columna NOT NULL sin default constante, y no soporta
  defaults con expresiones — así que en vez de pelear con eso, el código
  que lee estas columnas trata NULL como "fila vieja" (ej. False para
  booleanos).
- Si el ALTER falla, esta función deja propagar la excepción (fail-fast):
  tragar el error dejaría que el primer INSERT/SELECT que toque la columna
  reviente con UndefinedColumn, mucho más difícil de diagnosticar.
- Se llama una sola vez, en servidor.py, inmediatamente después de
  `db.create_all()` y dentro del mismo app_context.
"""

import logging
from sqlalchemy import inspect, Column
from sqlalchemy.schema import CreateColumn

from modulos.models import User, ExpedienteDescargado

logger = logging.getLogger(__name__)


# (tabla, columna) — la columna se toma tal cual está declarada en el
# modelo correspondiente, así que agregar un campo acá y en models.py son
# los dos únicos pasos para dar de alta una columna nueva seguro.
COLUMNAS_NUEVAS = [
    (User.__tablename__, User.notificar_email),
    (User.__tablename__, User.plan_max_comprado),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.storage_key),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.total_filas),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.total_archivos),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.huellas_json),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.mv_expediente_href),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.es_actualizacion),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.parcial),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.actualizado_desde_id),
    (ExpedienteDescargado.__tablename__, ExpedienteDescargado.ultimo_acceso_en),
]


def aplicar_migraciones_ligeras(db):
    """
    Agrega las columnas de COLUMNAS_NUEVAS que falten en la BD real.

    Idempotente: si ya existen, no hace nada. Debe llamarse dentro de un
    app_context (usa db.engine).
    """
    engine = db.engine
    dialect = engine.dialect
    inspector = inspect(engine)
    existentes = inspector.get_table_names()

    agregadas = 0
    with engine.begin() as conn:
        for tabla, columna_modelo in COLUMNAS_NUEVAS:
            if tabla not in existentes:
                # La tabla todavía no existe (se creará con create_all en el
                # próximo arranque, o ya la creó create_all en esta misma
                # corrida con la columna incluida desde el modelo).
                continue

            columnas_actuales = {c['name'] for c in inspector.get_columns(tabla)}
            if columna_modelo.name in columnas_actuales:
                continue

            # Recrear la columna "suelta" (sin tabla asociada) para que
            # CreateColumn la compile como fragmento DDL portable, sin
            # arrastrar la FK/PK de la tabla original.
            columna_ddl = Column(
                columna_modelo.name,
                columna_modelo.type,
                nullable=True,
            )
            fragmento = CreateColumn(columna_ddl).compile(dialect=dialect)
            tabla_citada = dialect.identifier_preparer.quote(tabla)

            if dialect.name == 'postgresql':
                sql = f'ALTER TABLE {tabla_citada} ADD COLUMN IF NOT EXISTS {fragmento}'
            else:
                sql = f'ALTER TABLE {tabla_citada} ADD COLUMN {fragmento}'

            logger.info(f"[MIGRACION] {tabla}.{columna_modelo.name}: {sql}")
            conn.exec_driver_sql(sql)
            agregadas += 1

    if agregadas:
        logger.info(f"[MIGRACION] {agregadas} columna(s) nueva(s) agregada(s)")
    else:
        logger.info("[MIGRACION] BD ya al día, ninguna columna nueva")
