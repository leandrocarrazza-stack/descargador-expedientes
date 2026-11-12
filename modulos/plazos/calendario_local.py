"""
Calendario de plazos para el plugin local, que se mantiene al día solo.

Una sola llamada devuelve el calendario listo para calcular():

    from modulos.plazos.calendario_local import calendario_actualizado
    from modulos.plazos.calculo import calcular

    cal, estado = calendario_actualizado('calendario_foja.json')
    if estado['aviso']:
        print(estado['aviso'])          # mostrarle al usuario si el calendario puede estar viejo o incompleto
    r = calcular(cal, tipo_notificacion='sne', fecha=..., dias=10, localidad='Paraná', fuero='civil')

Qué hace en cada llamada:
- Si el archivo existe y tiene menos de 15 días, lo usa y listo (no toca internet).
- Si no existe, está vencido o la última lectura de la página de feriados falló, lo
  actualiza leyendo el sitio del STJER (sync_local.py). La primera vez tarda ~1 minuto.
- Si no se puede actualizar (sin internet, sitio caído), sigue con la última copia buena y
  lo avisa. Y no vuelve a intentarlo hasta pasadas 6 horas, para no demorar cada arranque.
- Si nunca hubo calendario y no se pudo bajar, calcula solo con las reglas fijas del código
  (sin feria de julio, puentes ni feriados locales) y lo avisa con claridad.

Archivos que necesita (además de los de sync_local.py): este módulo.
Requiere beautifulsoup4 y requests solo para actualizar; para usar un archivo ya guardado no.
"""

import os
from datetime import datetime, timedelta, timezone

from modulos.plazos import exportacion
from modulos.plazos.calendario import Calendario
from modulos.plazos.exportacion import ErrorExportacion

MAX_DIAS = 15            # antigüedad a partir de la cual se actualiza
REINTENTO_HORAS = 6      # espera mínima entre intentos de actualización


def _cargar(ruta):
    """(Calendario, datos) del archivo guardado, o (None, None) si no existe o no sirve."""
    try:
        datos = exportacion.cargar_archivo(ruta)
        cal, _ = exportacion.calendario_desde_exportacion(datos)
        return cal, datos
    except (OSError, ValueError):   # no existe, JSON roto o formato que no entiende
        return None, None


def _ruta_intento(ruta):
    return ruta + '.intento'


def _intento_reciente(ruta, ahora):
    try:
        with open(_ruta_intento(ruta), 'r', encoding='utf-8') as f:
            ultimo = datetime.strptime(f.read().strip(), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (OSError, ValueError):
        return False
    return timedelta(0) <= ahora - ultimo < timedelta(hours=REINTENTO_HORAS)


def _anotar_intento(ruta, ahora):
    try:
        with open(_ruta_intento(ruta), 'w', encoding='utf-8') as f:
            f.write(ahora.strftime('%Y-%m-%dT%H:%M:%SZ'))
    except OSError:
        pass   # si no se puede anotar, solo se pierde la espera entre intentos


def _hace(dias):
    return f'de hace {dias} días' if dias is not None else 'de fecha desconocida'


def _pagina_ok(datos):
    return ((datos.get('ultima_sincronizacion') or {}).get('pagina') or {}).get('resultado') in ('ok', 'sin_cambios')


def calendario_actualizado(ruta='calendario_foja.json', max_dias=MAX_DIAS, descargar=None, ahora=None, pausa=None):
    """
    Devuelve (Calendario, estado). `estado` trae:
      origen:  'archivo' (se usó la copia guardada), 'actualizado' (se acaba de actualizar)
               o 'solo_reglas' (no hay calendario de Foja: solo reglas fijas)
      dias:    antigüedad del calendario usado, en días (None si no hay)
      aviso:   texto para mostrarle al usuario si algo puede estar viejo o incompleto (o None)
      problemas: entradas del archivo que se omitieron por no ser válidas
    `descargar`, `ahora` y `pausa` son para pruebas (descarga falsa, reloj, sin pausas).
    """
    ahora = ahora or datetime.now(timezone.utc)
    cal, datos = _cargar(ruta)
    dias = exportacion.dias_de_antiguedad(datos, ahora) if datos else None
    vencido = datos is None or dias is None or dias > max_dias or not _pagina_ok(datos)
    aviso = None
    actualizado_ahora = False

    if vencido and not _intento_reciente(ruta, ahora):
        _anotar_intento(ruta, ahora)
        try:
            from modulos.plazos import sync_local
            kw = {} if pausa is None else {'pausa': pausa}
            hoy = ahora.astimezone(timezone(timedelta(hours=-3))).date()   # fecha en Argentina
            r = sync_local.actualizar_calendario_local(ruta, descargar=descargar, hoy=hoy, ahora=ahora, **kw)
            if r['guardado']:
                cal2, datos2 = _cargar(ruta)
                if cal2 is not None:
                    cal, datos, dias = cal2, datos2, 0
                    vencido, actualizado_ahora = False, True
                    if r['codigo'] == 2:
                        aviso = ('El calendario se actualizó solo en parte (alguna fuente del STJER no respondió): '
                                 'puede faltar algún día inhábil reciente.')
            else:
                aviso = f'No se pudo actualizar el calendario: {r["motivo"]}'
        except ErrorExportacion as exc:
            aviso = f'No se pudo actualizar el calendario: {exc}'
        except Exception as exc:   # el cálculo no tiene que caerse por un problema de red
            aviso = f'No se pudo actualizar el calendario ({type(exc).__name__}: {exc}).'

    if cal is None:
        return Calendario([]), {
            'origen': 'solo_reglas', 'dias': None, 'problemas': [],
            'aviso': (aviso or 'Todavía no hay calendario de Foja guardado.') +
                     ' Se calcula solo con los feriados fijos: NO incluye la feria de julio, los puentes ni los '
                     'feriados de cada ciudad, así que los plazos que los crucen pueden dar mal.'}

    if vencido and aviso is None:   # estaba vencido pero hace poco ya se intentó y no funcionó: se reintenta más tarde
        aviso = (f'El calendario guardado es {_hace(dias)} y la última actualización no funcionó '
                 '(se reintenta en unas horas).')
    elif vencido:
        aviso += f' Se usa la última copia guardada, {_hace(dias)}.'
    _, problemas = exportacion.calendario_desde_exportacion(datos)
    return cal, {'origen': 'actualizado' if actualizado_ahora else 'archivo', 'dias': dias, 'aviso': aviso, 'problemas': problemas}
