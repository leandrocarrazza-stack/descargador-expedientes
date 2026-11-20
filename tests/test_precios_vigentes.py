#!/usr/bin/env python3
"""
Tests del cambio de precios del 1/12/2026 (config.planes_vigentes y /pagos/planes).
`python -m pytest tests/test_precios_vigentes.py`
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
config.MAIL_DEFAULT_SENDER = 'foja@example.com'
config.TESTING = True

import pytest

from modulos.database import db
from modulos.models import User
from servidor import crear_app

AR = ZoneInfo('America/Argentina/Buenos_Aires')


def _precios(ahora):
    p = config.planes_vigentes(ahora)
    return p['individual']['precio_ars'], p['estudio']['precio_ars'], p['matricula']['precio_ars']


def test_hasta_el_30_11_rigen_precios_viejos():
    assert _precios(datetime(2026, 11, 30, 23, 59, 59, tzinfo=AR)) == (3000, 24000, 63000)


def test_desde_el_1_12_rigen_precios_nuevos():
    assert _precios(datetime(2026, 12, 1, 0, 0, 0, tzinfo=AR)) == (5000, 40000, 90000)


def test_el_corte_es_hora_argentina_no_utc():
    # 30/11 22:00 en Argentina = 01:00 UTC del 1/12: todavía rige el precio viejo
    ahora_utc = datetime(2026, 12, 1, 1, 0, tzinfo=ZoneInfo('UTC'))
    assert _precios(ahora_utc) == (3000, 24000, 63000)


def test_descripciones_coinciden_con_el_precio():
    for plan in config.PLANES_NUEVOS.values():
        c_u = plan['precio_ars'] // plan['creditos']
        assert f"${c_u:,}".replace(',', '.') in plan['descripcion']


@pytest.fixture
def client():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['RATELIMIT_ENABLED'] = False
    with app.app_context():
        db.drop_all()
        db.create_all()
        u = User(email='cliente@gmail.com', creditos_disponibles=0)
        u.establecer_password('x')
        db.session.add(u)
        db.session.commit()
        yield app.test_client()


def test_pagina_planes_muestra_precios_nuevos(client, monkeypatch):
    monkeypatch.setattr(config, 'FECHA_AUMENTO_PRECIOS', datetime(2020, 1, 1, tzinfo=AR))
    html = client.get('/pagos/planes').get_data(as_text=True)
    for texto in ('$5.000', '$40.000', '$90.000', '$4.000 c/descarga', '$3.000 c/descarga',
                  'Ahorrás $10.000', 'Ahorrás $60.000'):
        assert texto in html


def test_pagina_planes_muestra_precios_viejos_antes_de_la_fecha(client, monkeypatch):
    monkeypatch.setattr(config, 'FECHA_AUMENTO_PRECIOS', datetime(2099, 1, 1, tzinfo=AR))
    html = client.get('/pagos/planes').get_data(as_text=True)
    for texto in ('$3.000', '$24.000', '$63.000', '$2.400 c/descarga', '$2.100 c/descarga',
                  'Ahorrás $6.000', 'Ahorrás $27.000'):
        assert texto in html
