#!/usr/bin/env python3
"""
Backfill de User.plan_max_comprado para usuarios existentes.

La columna plan_max_comprado (lote de actualización incremental) se
empieza a llenar automáticamente desde la próxima compra de cada usuario
(ver rutas/pagos.py::_confirmar_compra), pero los usuarios que ya
compraron Estudio o Matrícula ANTES de este cambio quedarían con la
columna en NULL — sin acceso a la actualización incremental pese a haber
pagado por un plan que la incluye. Este script recorre las compras
'completed' existentes y la completa una sola vez.

Uso:
    python scripts/backfill_plan_max.py            # aplica los cambios
    python scripts/backfill_plan_max.py --dry-run   # sólo muestra qué haría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from servidor import app
from modulos.database import db
from modulos.models import User, CompraCreditos
from rutas.pagos import ORDEN_PLANES


def backfill(dry_run: bool = False):
    with app.app_context():
        usuarios_actualizados = 0

        for usuario in User.query.all():
            compras = CompraCreditos.query.filter_by(
                user_id=usuario.id, estado='completed'
            ).all()

            mejor_rango = -1
            mejor_plan = None
            for compra in compras:
                if compra.plan not in ORDEN_PLANES:
                    continue
                rango = ORDEN_PLANES.index(compra.plan)
                if rango > mejor_rango:
                    mejor_rango = rango
                    mejor_plan = compra.plan

            if mejor_plan is None:
                continue

            rango_actual = (
                ORDEN_PLANES.index(usuario.plan_max_comprado)
                if usuario.plan_max_comprado in ORDEN_PLANES else -1
            )
            if mejor_rango > rango_actual:
                print(f"  {usuario.email}: plan_max_comprado "
                      f"{usuario.plan_max_comprado!r} -> {mejor_plan!r}")
                usuarios_actualizados += 1
                if not dry_run:
                    usuario.plan_max_comprado = mejor_plan

        if dry_run:
            print(f"\n[DRY-RUN] {usuarios_actualizados} usuario(s) se actualizarían. Nada se guardó.")
        else:
            db.session.commit()
            print(f"\nOK: {usuarios_actualizados} usuario(s) actualizados.")


if __name__ == '__main__':
    backfill(dry_run='--dry-run' in sys.argv)
