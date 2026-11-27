#!/usr/bin/env python3
"""
Tests del exportador del calendario de plazos (modulos/plazos/exportacion.py y
GET /plazos/calendario.json). `python tests/test_plazos_exportacion.py`

Lo más importante: un cálculo hecho con el calendario reconstruido en el cliente
(a partir del JSON) da exactamente lo mismo que el cálculo del servidor.
"""

import json
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # antes de importar servidor

from modulos.database import db
from modulos.models import DiaInhabil, CalendarioSyncLog
from modulos.plazos import exportacion
from modulos.plazos.calculo import calcular
from modulos.plazos.calendario import Calendario, cargar_calendario
from modulos.plazos.reglas import Entrada
from servidor import crear_app

_fallos = []


def check(nombre, condicion, detalle=""):
    marca = "  OK  " if condicion else " FALLA"
    print(f"{marca} {nombre}" + (f" -> {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)
        if 'pytest' in sys.modules:
            raise AssertionError(nombre)


def _app():
    app = crear_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _fila(desde, hasta, tipo, motivo, **kw):
    return DiaInhabil(fecha_desde=desde, fecha_hasta=hasta, tipo=tipo, motivo=motivo, **kw)


def _cargar_datos_de_prueba():
    db.session.query(DiaInhabil).delete()
    db.session.query(CalendarioSyncLog).delete()
    db.session.add_all([
        _fila(date(2026, 7, 6), date(2026, 7, 17), 'feria_julio', 'Feria judicial de julio', fuente='scraper'),
        _fila(date(2026, 7, 29), date(2026, 7, 29), 'feriado_local', 'Santo patrono local',
              alcance='localidad', localidad='Paraná', fuente='scraper', fuente_url='https://www.jusentrerios.gov.ar/x'),
        _fila(date(2026, 8, 10), date(2026, 8, 10), 'inhabil_judicial', 'Solo laboral',
              fuero='laboral', fuente='manual'),
        _fila(date(2026, 9, 1), date(2026, 9, 1), 'inhabil_judicial', 'Organismo puntual',
              alcance='organismo', descuenta=False, fuente='scraper'),
        _fila(date(2026, 9, 2), date(2026, 9, 2), 'inhabil_judicial', 'Borrador sin revisar [REVISAR]',
              activo=False, fuente='scraper'),
    ])
    db.session.add(CalendarioSyncLog(fuente='pagina', resultado='ok', filas=3))
    db.session.commit()


# ── módulo puro ──────────────────────────────────────────────────────────
def test_ida_y_vuelta():
    originales = [
        Entrada(date(2026, 7, 6), date(2026, 7, 17), 'feria_julio', 'Feria judicial de julio', fuente='scraper'),
        Entrada(date(2026, 7, 29), date(2026, 7, 29), 'feriado_local', 'Patrono', alcance='localidad',
                localidad='Paraná', departamento='Paraná', fuero=None, descuenta=True, fuente='manual',
                fuente_url='https://www.jusentrerios.gov.ar/x'),
    ]
    datos = json.loads(json.dumps(exportacion.armar_exportacion(originales)))   # pasa por JSON de verdad
    cal, problemas = exportacion.calendario_desde_exportacion(datos)
    check("ida y vuelta sin problemas", problemas == [], problemas)
    recuperadas = [e for d in (date(2026, 7, 6), date(2026, 7, 29)) for e in cal.entradas_del_dia(d)
                   if e.fuente in ('scraper', 'manual')]
    check("las entradas vuelven idénticas", sorted(recuperadas, key=lambda e: e.desde) == sorted(originales, key=lambda e: e.desde))
    check("formato y hora de generación", datos['formato'] == exportacion.FORMATO and datos['generado'].endswith('Z'))


def test_validacion():
    base = {'desde': '2026-07-29', 'hasta': '2026-07-29', 'tipo': 'feriado_local', 'motivo': 'x'}
    malas = {
        'rango invertido': {**base, 'hasta': '2026-07-28'},
        'fecha basura': {**base, 'desde': 'mañana'},
        'alcance desconocido': {**base, 'alcance': 'galaxia'},
        'descuenta no booleano': {**base, 'descuenta': 'si'},
        'tipo con mayúsculas': {**base, 'tipo': 'Feriado'},
        'motivo vacío': {**base, 'motivo': '  '},
        'localidad no texto': {**base, 'localidad': 5},
        'no es objeto': 'hola',
    }
    for nombre, d in malas.items():
        try:
            exportacion.entrada_desde_dict(d)
            check(f"rechaza: {nombre}", False)
        except ValueError:
            check(f"rechaza: {nombre}", True)
    cal, problemas = exportacion.calendario_desde_exportacion(
        {'formato': 1, 'entradas': [base, malas['rango invertido'], base | {'desde': '2026-07-30', 'hasta': '2026-07-30'}]})
    check("una entrada mala se omite y las demás sirven", len(problemas) == 1
          and cal.entradas_del_dia(date(2026, 7, 29)) != [] and cal.entradas_del_dia(date(2026, 7, 30)) != [], problemas)
    for datos, nombre in (({'formato': 2, 'entradas': []}, 'formato futuro'), ({'entradas': []}, 'sin formato'),
                          ([], 'no es objeto'), ({'formato': 1, 'entradas': 'x'}, 'entradas no es lista'),
                          ({'formato': 1, 'entradas': [{}] * (exportacion.MAX_ENTRADAS + 1)}, 'demasiadas entradas')):
        try:
            exportacion.calendario_desde_exportacion(datos)
            check(f"rechaza el archivo: {nombre}", False)
        except exportacion.ErrorExportacion:
            check(f"rechaza el archivo: {nombre}", True)


def test_antiguedad():
    hoy = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    d = exportacion.armar_exportacion([], generado=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))
    check("antigüedad en días", exportacion.dias_de_antiguedad(d, hoy) == 10)
    check("sin fecha → None", exportacion.dias_de_antiguedad({}, hoy) is None)


# ── ruta del servidor ────────────────────────────────────────────────────
def test_ruta_y_paridad_con_el_servidor():
    app = _app()
    with app.app_context():
        _cargar_datos_de_prueba()
        c = app.test_client()
        r = c.get('/plazos/calendario.json')   # sin login, a propósito
        check("público: responde 200 sin iniciar sesión", r.status_code == 200 and r.mimetype == 'application/json', r.status_code)
        datos = r.get_json()
        motivos = [e['motivo'] for e in datos['entradas']]
        check("incluye las entradas activas", 'Feria judicial de julio' in motivos and 'Santo patrono local' in motivos
              and 'Organismo puntual' in motivos, motivos)
        check("no incluye los borradores inactivos", not any('[REVISAR]' in m for m in motivos))
        check("trae el estado de la última sincronización", datos['ultima_sincronizacion']['pagina']['resultado'] == 'ok')
        check("no trae datos de usuarios ni ids internos", set(datos['entradas'][0]) == set(exportacion.entrada_a_dict(
            Entrada(date(2026, 1, 1), date(2026, 1, 1), 't', 'm')))
              and 'email' not in r.get_data(as_text=True))
        check("se puede guardar en caché una hora", 'max-age=3600' in r.headers.get('Cache-Control', ''))
        etag = r.headers.get('ETag')
        r2 = c.get('/plazos/calendario.json', headers={'If-None-Match': etag})
        check("con el mismo ETag responde 304", etag and r2.status_code == 304, r2.status_code)

        # Paridad: el mismo cálculo con el calendario de la base y con el reconstruido desde el JSON
        cal_servidor = cargar_calendario()
        cal_cliente, problemas = exportacion.calendario_desde_exportacion(datos)
        check("el calendario del cliente se arma sin problemas", problemas == [], problemas)
        casos = [
            dict(tipo_notificacion='sne', fecha=date(2026, 6, 30), dias=10, localidad='Paraná', fuero='civil'),
            dict(tipo_notificacion='cedula', fecha=date(2026, 7, 20), dias=5, localidad='Paraná', fuero='laboral'),
            dict(tipo_notificacion='sne', fecha=date(2026, 8, 5), dias=3, localidad='Paraná', fuero='laboral'),
            dict(tipo_notificacion='cedula', fecha=date(2026, 7, 1), dias=40, modo='corridos', localidad=None, fuero=None),
        ]
        for caso in casos:
            a = calcular(cal_servidor, **caso)
            b = calcular(cal_cliente, **caso)
            check(f"mismo resultado servidor/cliente: {caso['tipo_notificacion']} {caso['fecha']} {caso['dias']}d",
                  (a.vencimiento, a.gracia_fecha, [d.a_dict() for d in a.detalle]) ==
                  (b.vencimiento, b.gracia_fecha, [d.a_dict() for d in b.detalle]), f"{a.vencimiento} vs {b.vencimiento}")
        sin_exportar = calcular(Calendario([]), **casos[0])
        check("sin las entradas exportadas el resultado sería otro (por eso se exportan)",
              sin_exportar.vencimiento != calcular(cal_cliente, **casos[0]).vencimiento)


# ── descarga contra un servidor local de prueba ──────────────────────────
class _Servidor:
    """Sirve un cuerpo fijo en 127.0.0.1 para probar descargar_calendario()."""
    def __init__(self, cuerpo, estado=200):
        servidor = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(estado)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(cuerpo)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.httpd.server_port}/plazos/calendario.json'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def cerrar(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_descarga():
    bueno = json.dumps(exportacion.armar_exportacion(
        [Entrada(date(2026, 7, 6), date(2026, 7, 17), 'feria_julio', 'Feria judicial de julio')])).encode()
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / 'sub' / 'calendario_foja.json'
        s = _Servidor(bueno)
        try:
            datos = exportacion.descargar_calendario(s.url, str(destino))
        finally:
            s.cerrar()
        check("baja, valida y guarda", destino.exists() and len(datos['entradas']) == 1
              and exportacion.cargar_archivo(str(destino))['entradas'][0]['motivo'] == 'Feria judicial de julio')
        check("no deja archivos temporales", [p.name for p in destino.parent.iterdir()] == ['calendario_foja.json'])

        for nombre, cuerpo in (('JSON roto', b'<html>no soy json'), ('formato futuro', b'{"formato": 9, "entradas": []}'),
                               ('demasiado grande', b'x' * (exportacion.MAX_BYTES + 10))):
            s = _Servidor(cuerpo)
            try:
                exportacion.descargar_calendario(s.url, str(destino))
                check(f"rechaza: {nombre}", False)
            except exportacion.ErrorExportacion:
                check(f"rechaza: {nombre}", True)
            finally:
                s.cerrar()
        check("ante una descarga mala conserva la última copia buena",
              exportacion.cargar_archivo(str(destino))['entradas'][0]['motivo'] == 'Feria judicial de julio')

        s = _Servidor(b'', estado=500)
        try:
            exportacion.descargar_calendario(s.url, str(destino))
            check("rechaza: error del servidor", False)
        except exportacion.ErrorExportacion:
            check("rechaza: error del servidor", True)
        finally:
            s.cerrar()

    try:
        exportacion.descargar_calendario('http://foja.com.ar/plazos/calendario.json')
        check("rechaza http que no sea local", False)
    except exportacion.ErrorExportacion:
        check("rechaza http que no sea local", True)
    check("la dirección oficial es https", exportacion._url_permitida(exportacion.URL_OFICIAL))


def test_comando_de_terminal():
    bueno = json.dumps(exportacion.armar_exportacion(
        [Entrada(date(2026, 7, 6), date(2026, 7, 17), 'feria_julio', 'Feria judicial de julio')])).encode()
    with tempfile.TemporaryDirectory() as tmp:
        destino = str(Path(tmp) / 'cal.json')
        s = _Servidor(bueno)
        try:
            codigo = exportacion.main([destino, s.url])
        finally:
            s.cerrar()
        check("el comando guarda el archivo y sale con código 0", codigo == 0 and Path(destino).exists())
        s = _Servidor(b'no es json')
        try:
            codigo = exportacion.main([str(Path(tmp) / 'otro.json'), s.url])
        finally:
            s.cerrar()
        check("ante un error sale con código 1 y no crea el archivo", codigo == 1 and not (Path(tmp) / 'otro.json').exists())


if __name__ == '__main__':
    print("=" * 70)
    print(" TESTS DEL EXPORTADOR DEL CALENDARIO")
    print("=" * 70)
    test_ida_y_vuelta()
    test_validacion()
    test_antiguedad()
    test_ruta_y_paridad_con_el_servidor()
    test_descarga()
    test_comando_de_terminal()
    print("\n" + "=" * 70)
    if _fallos:
        print(f" {len(_fallos)} FALLA(S): " + ", ".join(_fallos))
        sys.exit(1)
    print(" TODO OK")
    sys.exit(0)
