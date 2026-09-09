#!/usr/bin/env python3
"""
Tests de User.registrar_uso_credito() (lote 6): descuenta créditos y
resetea creditos_usados_mes la primera vez que se usa un crédito en un
mes distinto al de fecha_reset_creditos. `python test_registrar_uso_credito.py`.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modulos.models import User

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


def test_descuenta_creditos():
    user = User(creditos_disponibles=10, creditos_usados_mes=0, fecha_reset_creditos=datetime.utcnow())
    user.registrar_uso_credito(1)
    check("descuenta 1 crédito", user.creditos_disponibles == 9)
    check("suma 1 a usados este mes", user.creditos_usados_mes == 1)


def test_mismo_mes_acumula():
    ahora = datetime.utcnow()
    user = User(creditos_disponibles=10, creditos_usados_mes=3, fecha_reset_creditos=ahora)
    user.registrar_uso_credito(1)
    check("acumula sin resetear dentro del mismo mes", user.creditos_usados_mes == 4)


def test_mes_distinto_resetea_antes_de_sumar():
    mes_pasado = datetime.utcnow() - timedelta(days=40)
    user = User(creditos_disponibles=10, creditos_usados_mes=7, fecha_reset_creditos=mes_pasado)
    user.registrar_uso_credito(1)
    check("resetea a 0 y suma 1 (no queda en 8)", user.creditos_usados_mes == 1, user.creditos_usados_mes)
    check("fecha_reset_creditos se actualiza a ahora",
          user.fecha_reset_creditos.date() == datetime.utcnow().date())


def test_sin_fecha_reset_previa_usa_creado_en():
    hace_dos_meses = datetime.utcnow() - timedelta(days=65)
    user = User(creditos_disponibles=5, creditos_usados_mes=2, fecha_reset_creditos=None, creado_en=hace_dos_meses)
    user.registrar_uso_credito(1)
    check("sin fecha_reset_creditos, usa creado_en como referencia y resetea",
          user.creditos_usados_mes == 1, user.creditos_usados_mes)


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DE User.registrar_uso_credito() (LOTE 6)")
    print("=" * 70)

    test_descuenta_creditos()
    test_mismo_mes_acumula()
    test_mes_distinto_resetea_antes_de_sumar()
    test_sin_fecha_reset_previa_usa_creado_en()

    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
