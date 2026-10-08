"""
Días inhábiles que salen de una regla (no hace falta que nadie los cargue):

- feriados nacionales de fecha fija y los trasladables (Ley 27.399),
- Carnaval y Jueves/Viernes Santo (dependen de la fecha de Pascua),
- feriados provinciales fijos (Caseros, San Miguel, Día del Empleado Judicial),
- inhábiles judiciales del 24 y 31 de diciembre,
- Día de la Magistratura / Día de la Abogacía (rotación anual, Ac. Gral. 22/12),
- feria judicial de enero (1 al 31).

Lo que NO se puede calcular (puentes turísticos o "días no laborables",
feria de julio, inhábiles por acuerdo, santos patronos de cada localidad) se
lee de la base, que se alimenta sola desde el sitio del STJER
(ver sync_stjer.py).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass(frozen=True)
class Entrada:
    """Un día (o rango de días) que afecta el cómputo."""
    desde: date
    hasta: date
    tipo: str                      # feriado_nacional, feriado_provincial, no_laborable,
                                   # inhabil_judicial, feria_enero, feria_julio,
                                   # feriado_local, suspension
    motivo: str
    alcance: str = 'provincia'     # provincia | departamento | localidad | organismo
    departamento: Optional[str] = None
    localidad: Optional[str] = None
    fuero: Optional[str] = None    # si solo rige para un fuero (ej. 'laboral')
    descuenta: bool = True         # False = solo se muestra como aviso
    fuente: str = 'regla'          # regla | scraper | manual | semilla
    fuente_url: Optional[str] = None


def pascua(anio):
    """Domingo de Pascua (algoritmo de Meeus/Jones/Butcher, calendario gregoriano)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(anio, mes, dia)


def trasladar(fecha):
    """
    Feriados trasladables (Ley 27.399, art. 6): martes y miércoles pasan al
    lunes anterior; jueves y viernes, al lunes siguiente; sábado, domingo y
    lunes quedan donde están.
    """
    dow = fecha.weekday()  # lunes=0
    if dow in (1, 2):
        return fecha - timedelta(days=dow)
    if dow in (3, 4):
        return fecha + timedelta(days=7 - dow)
    return fecha


def _un_dia(fecha, tipo, motivo, **kw):
    return Entrada(desde=fecha, hasta=fecha, tipo=tipo, motivo=motivo, **kw)


def dia_rotativo(anio):
    """
    Inhábil anual por rotación (Ac. Gral. 22/12): los años pares se
    conmemora el Día de la Magistratura (15/9) y los impares el Día de la
    Abogacía (29/8). Si cae en día no laborable, pasa al siguiente hábil.
    """
    if anio % 2 == 0:
        fecha, motivo = date(anio, 9, 15), 'Día de la Magistratura y la Función Judicial'
    else:
        fecha, motivo = date(anio, 8, 29), 'Día de la Abogacía'
    while fecha.weekday() >= 5:
        fecha += timedelta(days=1)
    return fecha, motivo


def entradas_del_anio(anio):
    """Todas las entradas que se pueden calcular por regla para un año."""
    e = []
    nac = [
        ((1, 1), 'Año Nuevo'),
        ((3, 24), 'Día Nacional de la Memoria por la Verdad y la Justicia'),
        ((4, 2), 'Día del Veterano y de los Caídos en la Guerra de Malvinas'),
        ((5, 1), 'Día del Trabajador'),
        ((5, 25), 'Día de la Revolución de Mayo'),
        ((6, 20), 'Paso a la Inmortalidad del General Manuel Belgrano'),
        ((7, 9), 'Día de la Independencia'),
        ((12, 8), 'Inmaculada Concepción de María'),
        ((12, 25), 'Navidad'),
    ]
    for (mes, dia), motivo in nac:
        e.append(_un_dia(date(anio, mes, dia), 'feriado_nacional', motivo))

    trasl = [
        ((6, 17), 'Paso a la Inmortalidad del General Martín Miguel de Güemes'),
        ((8, 17), 'Paso a la Inmortalidad del General José de San Martín'),
        ((10, 12), 'Día del Respeto a la Diversidad Cultural'),
        ((11, 20), 'Día de la Soberanía Nacional'),
    ]
    for (mes, dia), motivo in trasl:
        original = date(anio, mes, dia)
        movido = trasladar(original)
        sufijo = '' if movido == original else f' (trasladado del {dia}/{mes})'
        e.append(_un_dia(movido, 'feriado_nacional', motivo + sufijo))

    p = pascua(anio)
    e.append(_un_dia(p - timedelta(days=48), 'feriado_nacional', 'Carnaval'))
    e.append(_un_dia(p - timedelta(days=47), 'feriado_nacional', 'Carnaval'))
    e.append(_un_dia(p - timedelta(days=3), 'feriado_nacional', 'Jueves Santo'))
    e.append(_un_dia(p - timedelta(days=2), 'feriado_nacional', 'Viernes Santo'))

    e.append(_un_dia(date(anio, 2, 3), 'feriado_provincial', 'Batalla de Caseros (Ley 7285)'))
    e.append(_un_dia(date(anio, 9, 29), 'feriado_provincial', 'San Miguel, Patrono de la Provincia'))
    e.append(_un_dia(date(anio, 11, 16), 'inhabil_judicial', 'Día del Empleado Judicial'))
    e.append(_un_dia(date(anio, 12, 24), 'inhabil_judicial', 'Inhábil judicial (24 de diciembre)'))
    e.append(_un_dia(date(anio, 12, 31), 'inhabil_judicial', 'Inhábil judicial (31 de diciembre)'))

    fecha, motivo = dia_rotativo(anio)
    e.append(_un_dia(fecha, 'inhabil_judicial', motivo))

    e.append(Entrada(desde=date(anio, 1, 1), hasta=date(anio, 1, 31),
                     tipo='feria_enero', motivo='Feria judicial de enero'))
    return e
