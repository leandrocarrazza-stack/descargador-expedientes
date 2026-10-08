#!/usr/bin/env python3
"""
Tests del cómputo de plazos (modulos/plazos): reglas de calendario, notificación
electrónica (Ac. Gral. 15/18), días hábiles y corridos, ferias, feriados locales
y hora de gracia. Lógica pura: no usa Flask ni base de datos.
`python tests/test_plazos_calculo.py`
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.plazos import reglas, textos
from modulos.plazos.calendario import Calendario
from datetime import datetime
from modulos.plazos.calculo import calcular, proximo_martes_o_viernes, ErrorPlazo, hoy_argentina
from modulos.plazos.localidades import buscar_localidades, localidad_oficial, departamento_de
from modulos.plazos.reglas import Entrada

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:   # bajo pytest una falla tiene que hacer fallar el test
            raise AssertionError(nombre)


FERIA_JULIO_2026 = Entrada(date(2026, 7, 6), date(2026, 7, 17), 'feria_julio', 'Feria judicial de julio')


def cal(*extra):
    return Calendario([FERIA_JULIO_2026, *extra])


def test_reglas_calendario_2026():
    c = Calendario()
    esperados = {
        date(2026, 2, 16): 'Carnaval', date(2026, 2, 17): 'Carnaval',
        date(2026, 4, 2): 'Jueves Santo', date(2026, 4, 3): 'Viernes Santo',
        date(2026, 6, 15): 'Güemes', date(2026, 8, 17): 'San Martín',
        date(2026, 10, 12): 'Diversidad', date(2026, 11, 23): 'Soberanía',
        date(2026, 9, 15): 'Magistratura', date(2026, 9, 29): 'San Miguel',
    }
    for d, clave in esperados.items():
        m = c.motivo_inhabil(d)
        check(f"2026 {d} inhábil ({clave})", bool(m) and clave in m, m)
    check("pascua 2026 = 5/4", reglas.pascua(2026) == date(2026, 4, 5))
    check("pascua 2025 = 20/4", reglas.pascua(2025) == date(2025, 4, 20))
    check("rotación 2025 = Abogacía 29/8", reglas.dia_rotativo(2025)[0] == date(2025, 8, 29))
    check("rotación 2024 = 16/9 (15/9 era domingo)", reglas.dia_rotativo(2024)[0] == date(2024, 9, 16))
    check("rotación 2021 = 30/8 (29/8 era domingo)", reglas.dia_rotativo(2021)[0] == date(2021, 8, 30))
    check("el 14 de enero es feria: inhábil",
          not Calendario().es_habil(date(2026, 1, 14)))
    check("un día común es hábil", Calendario().es_habil(date(2026, 10, 8)))


def test_martes_viernes():
    check("miércoles → viernes", proximo_martes_o_viernes(date(2026, 10, 7)) == date(2026, 10, 9))
    check("martes → viernes (estrictamente posterior)", proximo_martes_o_viernes(date(2026, 10, 6)) == date(2026, 10, 9))
    check("viernes → martes", proximo_martes_o_viernes(date(2026, 10, 9)) == date(2026, 10, 13))
    check("sábado → martes", proximo_martes_o_viernes(date(2026, 10, 10)) == date(2026, 10, 13))


def test_cedula_habiles_simple():
    # Cédula lunes 5/10/2026, 5 días hábiles: martes 6 (1), mié 7, jue 8, vie 9, lun 12 (feriado) → mar 13
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 10, 5), dias=5)
    check("cédula 5 hábiles con feriado 12/10 → martes 13/10", r.vencimiento == date(2026, 10, 13), r.vencimiento)
    check("perfección = fecha de cédula", r.fecha_perfeccion == date(2026, 10, 5))
    check("detalle marca el 12/10 inhábil",
          any(d.fecha == date(2026, 10, 12) and not d.habil for d in r.detalle))
    check("gracia: primer día hábil siguiente, 09:00",
          r.gracia_fecha == date(2026, 10, 14) and r.gracia_hora == '09:00', (r.gracia_fecha, r.gracia_hora))


def test_sne():
    # Disponible miércoles 7/10/2026 → se perfecciona viernes 9/10; plazo desde sábado 10
    r = calcular(cal(), tipo_notificacion='sne', fecha=date(2026, 10, 7), dias=3)
    check("SNE miércoles → perfecciona viernes 9/10", r.fecha_perfeccion == date(2026, 10, 9))
    # 3 hábiles: mar 13 (1), mié 14 (2), jue 15 (3) — el lunes 12 es feriado
    check("SNE 3 hábiles → jueves 15/10", r.vencimiento == date(2026, 10, 15), r.vencimiento)
    check("SNE trae la nota sobre martes y viernes", any('martes' in n for n in r.notas))

    # Martes/viernes feriado → siguiente hábil: disponible sábado 14/2/2026 → martes 17/2 es Carnaval → miércoles 18/2
    r = calcular(cal(), tipo_notificacion='sne', fecha=date(2026, 2, 14), dias=1)
    check("martes 17/2 (Carnaval) no es martes hábil → perfecciona el 18/2",
          r.fecha_perfeccion == date(2026, 2, 18), r.fecha_perfeccion)
    check("el plazo corre desde el 19/2", r.vencimiento == date(2026, 2, 19), r.vencimiento)

    r = calcular(cal(), tipo_notificacion='sne_urgente', fecha=date(2026, 10, 7), dias=1)
    check("SNE urgente perfecciona el mismo día", r.fecha_perfeccion == date(2026, 10, 7))


def test_ferias():
    # Feria de julio 2026 (6-17): cédula viernes 3/7/2026, 3 hábiles → 20, 21, 22/7
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 7, 3), dias=3)
    check("feria de julio no se cuenta (20/7 primer hábil)", r.detalle[-3].fecha == date(2026, 7, 20)
          and r.vencimiento == date(2026, 7, 22), r.vencimiento)
    # 9/7 feriado, 10/7 no laborable (agregado como entrada)
    nolab = Entrada(date(2026, 7, 10), date(2026, 7, 10), 'no_laborable', 'Día no laborable')
    check("día no laborable 10/7 es inhábil con la entrada",
          not Calendario([nolab]).es_habil(date(2026, 7, 10)))
    # Feria de enero: cédula 29/12/2025 (lun), 5 hábiles: 30/12 (1), 31/12 inhábil, enero feria → 2/2...
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2025, 12, 29), dias=3)
    # (el 3/2 es feriado por Caseros, así que el tercer día hábil es el 4/2)
    check("cruza 31/12 + feria de enero + Caseros: 30/12, 2/2, 4/2",
          [d.fecha for d in r.detalle if d.habil] == [date(2025, 12, 30), date(2026, 2, 2), date(2026, 2, 4)],
          [str(d.fecha) for d in r.detalle if d.habil])
    r = calcular(Calendario(), tipo_notificacion='cedula', fecha=date(2026, 6, 30), dias=2)
    check("advierte si falta la feria de julio del año",
          any('feria judicial de julio' in a for a in r.advertencias), r.advertencias)


def test_corridos():
    # 30 días corridos desde cédula 1/10/2026: del 2/10 al 31/10 → sábado 31/10 → prórroga al lunes 2/11
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 10, 1), dias=30, modo='corridos')
    check("30 corridos terminan sábado 31/10 → prorroga al lunes 2/11", r.vencimiento == date(2026, 11, 2), r.vencimiento)
    # 10 corridos desde 1/10 → 11/10 domingo, 12/10 feriado → martes 13/10
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 10, 1), dias=10, modo='corridos')
    check("10 corridos → domingo 11 + feriado 12 → martes 13/10", r.vencimiento == date(2026, 10, 13), r.vencimiento)
    # Los corridos NO descuentan la feria: 10 corridos desde 26/6 → 6/7... el 6/7 es feria → prórroga al 20/7
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 6, 26), dias=10, modo='corridos')
    check("corridos que terminan en feria prorrogan al 20/7", r.vencimiento == date(2026, 7, 20), r.vencimiento)
    # Corridos que terminan en día hábil: sin prórroga
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 10, 1), dias=5, modo='corridos')
    check("5 corridos desde jueves 1/10 → martes 6/10", r.vencimiento == date(2026, 10, 6), r.vencimiento)


def test_localidad_y_fuero():
    patrona = Entrada(date(2026, 10, 7), date(2026, 10, 7), 'feriado_local', 'Patrona: Ntra. Sra. del Rosario',
                      alcance='localidad', localidad='Gualeguaychú', fuente='scraper')
    c = cal(patrona)
    check("7/10 inhábil en Gualeguaychú", not c.es_habil(date(2026, 10, 7), 'Gualeguaychú'))
    check("7/10 hábil en Concordia", c.es_habil(date(2026, 10, 7), 'Concordia'))
    check("7/10 hábil si no se eligió localidad", c.es_habil(date(2026, 10, 7), None))

    laboral = Entrada(date(2025, 10, 23), date(2025, 10, 24), 'inhabil_judicial', 'Congreso Derecho del Trabajo',
                      fuero='laboral')
    c = Calendario([laboral])
    check("inhábil laboral rige para fuero laboral", not c.es_habil(date(2025, 10, 23), 'Paraná', 'laboral'))
    check("inhábil laboral no rige para civil", c.es_habil(date(2025, 10, 23), 'Paraná', 'civil'))
    av = c.avisos(date(2025, 10, 20), date(2025, 10, 31), 'Paraná', None)
    check("sin fuero elegido, el inhábil laboral sale como aviso", len(av) == 1, av)

    juzgado = Entrada(date(2026, 8, 19), date(2026, 8, 19), 'inhabil_judicial', 'Inhábil Juzgado de Paz de Libertador San Martín',
                      alcance='organismo', localidad='Libertador San Martín', descuenta=False,
                      fuente_url='https://example.org/noticia')
    c = Calendario([juzgado])
    check("inhábil de un organismo no descuenta", c.es_habil(date(2026, 8, 19), 'Libertador San Martín'))
    check("pero se muestra como aviso en esa localidad",
          len(c.avisos(date(2026, 8, 17), date(2026, 8, 21), 'Libertador San Martín')) == 1)
    check("y no en otra localidad", c.avisos(date(2026, 8, 17), date(2026, 8, 21), 'Concordia') == [])

    depto = Entrada(date(2026, 3, 10), date(2026, 3, 10), 'inhabil_judicial', 'Temporal', alcance='departamento',
                    departamento='Gualeguay')
    c = Calendario([depto])
    check("inhábil departamental rige para General Galarza (dpto. Gualeguay)",
          not c.es_habil(date(2026, 3, 10), 'General Galarza'))


def test_hoy_argentina_y_gracia():
    check("a las 23:30 del 31/12 en Argentina, el servidor (UTC) ya está en el 1/1: hoy sigue siendo 31/12",
          hoy_argentina(datetime(2027, 1, 1, 2, 30)) == date(2026, 12, 31))
    check("a las 12:00 UTC es el mismo día", hoy_argentina(datetime(2026, 10, 8, 12, 0)) == date(2026, 10, 8))
    r = calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 10, 5), dias=1, hora_apertura='7 am')
    check("hora de apertura mal escrita: no hay 'null' en la gracia (se omite el bloque)",
          r.gracia_hora is None and r.a_dict()['gracia'] is None, r.a_dict()['gracia'])


def test_avisos_parciales():
    parcial_prov = Entrada(date(2026, 10, 14), date(2026, 10, 14), 'inhabil_judicial', 'Inhábil desde las 10 hs',
                           alcance='provincia', descuenta=False)
    c = Calendario([parcial_prov])
    check("un parcial de toda la provincia se avisa a cualquiera",
          len(c.avisos(date(2026, 10, 13), date(2026, 10, 15), 'Paraná')) == 1
          and len(c.avisos(date(2026, 10, 13), date(2026, 10, 15), None)) == 1)
    check("y no descuenta", c.es_habil(date(2026, 10, 14), 'Paraná'))
    juz = Entrada(date(2026, 10, 14), date(2026, 10, 14), 'inhabil_judicial', 'Juzgado de Paz de Hernández',
                  alcance='organismo', localidad='Hernández', departamento='Nogoyá', descuenta=False)
    c = Calendario([juz])
    check("el inhábil de un juzgado de Hernández NO se avisa en otra localidad del mismo departamento",
          c.avisos(date(2026, 10, 13), date(2026, 10, 15), 'Nogoyá') == []
          and len(c.avisos(date(2026, 10, 13), date(2026, 10, 15), 'Hernández')) == 1)


def test_validaciones():
    try:
        calcular(cal(), tipo_notificacion='cedula', fecha=date(2026, 10, 1), dias=0)
        check("0 días → error", False)
    except ErrorPlazo:
        check("0 días → error", True)
    try:
        calcular(cal(), tipo_notificacion='xx', fecha=date(2026, 10, 1), dias=3)
        check("tipo inválido → error", False)
    except ErrorPlazo:
        check("tipo inválido → error", True)


def test_textos_y_localidades():
    f = textos.fechas_en_texto("16 y 17 de Febrero", 2026)
    check("'16 y 17 de Febrero'", f == [date(2026, 2, 16), date(2026, 2, 17)], f)
    f = textos.fechas_en_texto("1 al 31 de Enero", 2026)
    check("'1 al 31 de Enero' = 31 días", len(f) == 31 and f[0] == date(2026, 1, 1) and f[-1] == date(2026, 1, 31))
    f = textos.fechas_en_texto("1º de Mayo", 2026)
    check("'1º de Mayo'", f == [date(2026, 5, 1)], f)
    f = textos.fechas_en_texto("23 y 24 de octubre de 2025", 2026)
    check("año explícito", f == [date(2025, 10, 23), date(2025, 10, 24)], f)
    f = textos.fechas_en_noticia("El viernes 31 de este mes", date(2025, 10, 29))
    check("'31 de este mes'", f == [date(2025, 10, 31)], f)
    f = textos.fechas_en_noticia("Mañana será inhábil judicial", date(2025, 7, 30))
    check("'mañana'", f == [date(2025, 7, 31)], f)
    f = textos.fechas_en_noticia("Será inhábil el lunes", date(2025, 4, 11))
    check("'el lunes' (noticia de viernes)", f == [date(2025, 4, 14)], f)
    f = textos.fechas_en_noticia("inhábil el 2 de enero", date(2025, 12, 23))
    check("noticia de diciembre sobre '2 de enero' → año siguiente", f == [date(2026, 1, 2)], f)

    check("alias 'C. del Uruguay'", localidad_oficial('C. del Uruguay') == 'Concepción del Uruguay')
    check("'Villa de Rosario' → Villa del Rosario", localidad_oficial('Villa de Rosario') == 'Villa del Rosario')
    locs = buscar_localidades("El 7 de octubre será inhábil judicial en Paraná, Crespo, Gualeguaychú, Pueblo Brugo, "
                              "Villa San Marcial y Rosario del Tala")
    check("busca varias localidades", locs == ['Paraná', 'Crespo', 'Gualeguaychú', 'Pueblo Brugo',
                                                'Villa San Marcial', 'Rosario del Tala'], locs)
    locs = buscar_localidades("Hoy es inhábil judicial en Gualeguay y Galarza")
    check("'Gualeguay y Galarza' no confunde Gualeguaychú", locs == ['Gualeguay', 'General Galarza'], locs)
    locs = buscar_localidades("inhábil en San José de Feliciano")
    check("'San José de Feliciano' no se parte en 'San José'", locs == ['San José de Feliciano'], locs)
    check("departamento de Pueblo Brugo", departamento_de('Pueblo Brugo') == 'Paraná')


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE CÓMPUTO DE PLAZOS")
    print("=" * 70)
    test_reglas_calendario_2026()
    test_martes_viernes()
    test_cedula_habiles_simple()
    test_sne()
    test_ferias()
    test_corridos()
    test_localidad_y_fuero()
    test_hoy_argentina_y_gracia()
    test_avisos_parciales()
    test_validaciones()
    test_textos_y_localidades()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
