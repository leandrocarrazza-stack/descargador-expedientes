# modulos/estadisticas.py
"""
Cálculo de las estadísticas de uso de Foja para /admin/estadisticas.

Lee las tablas que ya existen (users, expedientes_descargados, compras_creditos)
y la tabla eventos_uso (visitas y cálculos de plazos, que se registran desde que
se agregó). Todo se agrupa en Python por mes, así funciona igual en SQLite
(desarrollo) y Postgres (producción). El volumen es chico, no hace falta más.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from modulos.models import CompraCreditos, EventoUso, ExpedienteDescargado, User

MESES_ES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
UTC_MENOS_3 = timedelta(hours=3)   # hora de Argentina


def _mes(fecha):
    """'2026-11' para una fecha guardada en UTC, contada en hora argentina."""
    f = fecha - UTC_MENOS_3
    return f'{f.year}-{f.month:02d}'


def _etiqueta(mes):
    anio, m = mes.split('-')
    return f'{MESES_ES[int(m) - 1]} {anio[2:]}'


def _ultimos_meses(ahora, cantidad):
    """Lista de 'AAAA-MM' de los últimos `cantidad` meses, el actual al final."""
    f = ahora - UTC_MENOS_3
    anio, mes = f.year, f.month
    meses = []
    for _ in range(cantidad):
        meses.append(f'{anio}-{mes:02d}')
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
    return meses[::-1]


def calcular_estadisticas(ahora=None, cantidad_meses=12):
    """Devuelve un dict listo para la plantilla admin_estadisticas.html."""
    ahora = ahora or datetime.utcnow()
    meses = _ultimos_meses(ahora, cantidad_meses)

    usuarios = User.query.with_entities(
        User.id, User.creado_en, User.plan, User.referido_por_id, User.referido_premiado, User.email).all()
    descargas = ExpedienteDescargado.query.with_entities(
        ExpedienteDescargado.user_id, ExpedienteDescargado.estado, ExpedienteDescargado.es_actualizacion,
        ExpedienteDescargado.tribunal, ExpedienteDescargado.creado_en).all()
    compras = CompraCreditos.query.with_entities(
        CompraCreditos.estado, CompraCreditos.monto_pagado, CompraCreditos.creado_en,
        CompraCreditos.completado_en).all()
    eventos = EventoUso.query.with_entities(EventoUso.tipo, EventoUso.ruta, EventoUso.creado_en).all()

    # ── Por mes ──
    nuevos = Counter(_mes(u.creado_en) for u in usuarios if u.creado_en)
    desc_ok, desc_fail, activos = Counter(), Counter(), defaultdict(set)
    for d in descargas:
        if not d.creado_en:
            continue
        m = _mes(d.creado_en)
        if d.estado == 'completed':
            desc_ok[m] += 1
            activos[m].add(d.user_id)
        elif d.estado == 'failed':
            desc_fail[m] += 1
    compras_mes, ingresos_mes = Counter(), Counter()
    for c in compras:
        if c.estado == 'completed':
            m = _mes(c.completado_en or c.creado_en)
            compras_mes[m] += 1
            ingresos_mes[m] += c.monto_pagado or 0
    visitas_mes = Counter(_mes(e.creado_en) for e in eventos if e.tipo == 'visita')
    plazos_mes = Counter(_mes(e.creado_en) for e in eventos if e.tipo == 'plazo_calculado')

    por_mes = [{
        'mes': m, 'etiqueta': _etiqueta(m),
        'usuarios_nuevos': nuevos[m],
        'descargas': desc_ok[m], 'fallidas': desc_fail[m], 'usuarios_activos': len(activos[m]),
        'compras': compras_mes[m], 'ingresos': round(ingresos_mes[m]),
        'visitas': visitas_mes[m], 'plazos': plazos_mes[m],
    } for m in meses]

    # ── Totales ──
    completas = [d for d in descargas if d.estado == 'completed']
    fallidas = [d for d in descargas if d.estado == 'failed']
    con_descarga = {d.user_id for d in completas}
    intentos = len(completas) + len(fallidas)
    compras_ok = [c for c in compras if c.estado == 'completed']
    visitas = [e for e in eventos if e.tipo == 'visita']
    plazos = [e for e in eventos if e.tipo == 'plazo_calculado']
    primer_evento = min((e.creado_en for e in eventos), default=None)

    totales = {
        'usuarios': len(usuarios),
        'usuarios_con_descarga': len(con_descarga),
        'usuarios_sin_descarga': len([u for u in usuarios if u.id not in con_descarga]),
        'descargas': len(completas),
        'actualizaciones': len([d for d in completas if d.es_actualizacion]),
        'fallidas': len(fallidas),
        'tasa_exito': round(100 * len(completas) / intentos) if intentos else None,
        'compras': len(compras_ok),
        'ingresos': round(sum(c.monto_pagado or 0 for c in compras_ok)),
        'visitas': len(visitas),
        'plazos': len(plazos),
        'referidos': len([u for u in usuarios if u.referido_por_id]),
        'referidos_premiados': len([u for u in usuarios if u.referido_premiado]),
    }

    # ── Rankings ──
    email_de = {u.id: u.email for u in usuarios}
    top_usuarios = [{'email': email_de.get(uid, f'usuario {uid}'), 'descargas': n}
                    for uid, n in Counter(d.user_id for d in completas).most_common(10)]
    tribunales = [{'tribunal': t, 'descargas': n}
                  for t, n in Counter((d.tribunal or '(sin dato)') for d in completas).most_common(10)]
    planes = [{'plan': p, 'usuarios': n} for p, n in Counter(u.plan for u in usuarios).most_common()]
    visitas_ruta = [{'ruta': r, 'visitas': n} for r, n in Counter(e.ruta for e in visitas).most_common()]

    return {
        'por_mes': por_mes,
        'totales': totales,
        'top_usuarios': top_usuarios,
        'tribunales': tribunales,
        'planes': planes,
        'visitas_ruta': visitas_ruta,
        'primer_evento': (primer_evento - UTC_MENOS_3) if primer_evento else None,
    }
