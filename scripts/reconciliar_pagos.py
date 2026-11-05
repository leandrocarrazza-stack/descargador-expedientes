#!/usr/bin/env python3
"""
Reconciliación de compras de créditos contra Mercado Pago.

Recorre las compras que quedaron en 'pending' y pregunta a la API de MP si
hay un pago aprobado con su external_reference. Sirve para detectar pagos
cobrados que nunca se acreditaron (webhook rechazado o perdido + comprador
que no volvió por el redirect).

Uso:
    python scripts/reconciliar_pagos.py                 # sólo informa (no toca nada)
    python scripts/reconciliar_pagos.py --dias 60       # ventana de búsqueda (default 30)
    python scripts/reconciliar_pagos.py --aplicar       # acredita las aprobadas
    python scripts/reconciliar_pagos.py --marcar-acreditada 123
        # marca la compra 123 como 'completed' SIN sumar créditos: usar cuando
        # el admin ya acreditó a mano, para que ni un reintento del webhook ni
        # una reconciliación posterior la vuelvan a acreditar. También le
        # habilita al usuario el plan comprado (actualización incremental).
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from servidor import app
from modulos.database import db
from modulos.models import User, CompraCreditos
from modulos.mercado_pago import buscar_pagos_por_referencia, MercadoPagoError
from rutas.pagos import _acreditar_pago_si_corresponde, _actualizar_plan_max_comprado


def reconciliar(dias: int, aplicar: bool) -> int:
    desde = datetime.utcnow() - timedelta(days=dias)
    encontradas = 0
    with app.app_context():
        pendientes = (CompraCreditos.query
                      .filter(CompraCreditos.estado == 'pending',
                              CompraCreditos.creado_en >= desde)
                      .order_by(CompraCreditos.creado_en).all())
        print(f"{len(pendientes)} compras pendientes en los últimos {dias} días")
        for compra in pendientes:
            if not compra.stripe_session_id:
                continue
            try:
                pagos = buscar_pagos_por_referencia(compra.stripe_session_id)
            except MercadoPagoError as e:
                print(f"  ! compra {compra.id}: error consultando MP: {e}")
                continue
            aprobado = next((p for p in pagos if p.get('status') == 'approved'), None)
            if not aprobado:
                continue
            encontradas += 1
            usuario = db.session.get(User, compra.user_id)
            print(f"  * compra {compra.id} ({usuario.email if usuario else compra.user_id}, "
                  f"{compra.plan}, ${compra.monto_pagado:.0f}, {compra.creado_en:%Y-%m-%d %H:%M}) "
                  f"-> pago MP {aprobado.get('id')} APROBADO sin acreditar")
            if aplicar:
                _acreditar_pago_si_corresponde(aprobado)
                db.session.refresh(compra)
                print(f"    acreditada: estado={compra.estado}")
        if encontradas and not aplicar:
            print("\nRevisá si alguna ya la acreditaste a mano (usá --marcar-acreditada ID)"
                  " antes de correr con --aplicar.")
    return encontradas


def marcar_acreditada(compra_id: int) -> None:
    with app.app_context():
        compra = db.session.get(CompraCreditos, compra_id)
        if not compra:
            sys.exit(f"No existe la compra {compra_id}")
        if compra.estado != 'pending':
            print(f"La compra {compra_id} ya estaba en estado {compra.estado!r}; no se cambia.")
            return
        compra.estado = 'completed'
        compra.completado_en = datetime.utcnow()
        # La acreditación manual desde el panel suma créditos pero no habilita
        # la actualización incremental que incluye el plan comprado.
        usuario = db.session.get(User, compra.user_id)
        if usuario:
            _actualizar_plan_max_comprado(usuario, compra.plan)
        db.session.commit()
        print(f"Compra {compra_id} marcada como acreditada (sin sumar créditos); "
              f"plan_max_comprado={usuario.plan_max_comprado if usuario else None!r}.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dias', type=int, default=30)
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--marcar-acreditada', type=int, metavar='ID')
    args = ap.parse_args()
    if args.marcar_acreditada:
        marcar_acreditada(args.marcar_acreditada)
    else:
        reconciliar(args.dias, args.aplicar)
