"""
Cómputo de plazos procesales (lógica pura: sin Flask ni base de datos).

Reglas que implementa:
- Cédula / notificación personal: el plazo corre desde el día siguiente.
- Notificación electrónica (Ac. Gral. 15/18, Anexo I, arts. 3 y 4): se
  perfecciona el martes o viernes inmediato posterior a la fecha "Para
  Notif. Desde" (o el siguiente día hábil si ese martes/viernes no lo es);
  el plazo corre desde el día siguiente. Con `sne_urgente` (art. 5) se
  perfecciona en el momento.
- Plazo en días HÁBILES: se descuentan sábados, domingos, feriados,
  inhábiles, ferias y suspensiones.
- Plazo en días CORRIDOS (caducidad): cuenta todos los días; si el último
  cae en día inhábil, se prorroga al siguiente hábil.
- Hora de gracia: el escrito puede presentarse en las dos primeras horas
  hábiles del día siguiente al vencimiento (art. 121, último párrafo, del
  Código Procesal Civil y Comercial de Entre Ríos, según el reglamento de
  los juzgados civiles).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

TIPOS_NOTIFICACION = {
    'cedula': 'Cédula / notificación personal',
    'sne': 'Electrónica (SNE) o automática de martes y viernes',
    'sne_urgente': 'Electrónica urgente (perfecciona al publicarse)',
}
MODOS = {'habiles': 'Días hábiles', 'corridos': 'Días corridos (caducidad)'}

DIAS_SEMANA = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
         'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']

MAX_DIAS_PLAZO = 1000
LIMITE_ITERACION = 4000   # corta cualquier bucle ante datos raros


class ErrorPlazo(ValueError):
    """Datos de entrada inválidos (se muestran al usuario)."""


def hoy_argentina(ahora_utc=None):
    """Fecha de hoy en Argentina (UTC-3, sin horario de verano). El servidor corre en UTC:
    entre las 21:00 y las 24:00 locales su `date.today()` ya es el día siguiente."""
    return ((ahora_utc or datetime.utcnow()) - timedelta(hours=3)).date()


def fecha_larga(f):
    return f"{DIAS_SEMANA[f.weekday()].capitalize()} {f.day} de {MESES[f.month - 1]} de {f.year}"


def proximo_martes_o_viernes(fecha):
    """Martes o viernes estrictamente posterior a `fecha`."""
    d = fecha + timedelta(days=1)
    while d.weekday() not in (1, 4):
        d += timedelta(days=1)
    return d


@dataclass
class Dia:
    fecha: date
    habil: bool
    motivo: str
    contador: Optional[int] = None
    fuente_url: Optional[str] = None

    def a_dict(self):
        return {
            'fecha': self.fecha.isoformat(),
            'fecha_texto': f"{self.fecha.day:02d}/{self.fecha.month:02d}/{self.fecha.year}",
            'dia': DIAS_SEMANA[self.fecha.weekday()][:3].capitalize(),
            'habil': self.habil,
            'motivo': self.motivo,
            'contador': self.contador,
            'fuente_url': self.fuente_url,
        }


@dataclass
class Resultado:
    fecha_ingresada: date
    fecha_perfeccion: date
    inicio_computo: date
    vencimiento: date
    modo: str
    dias: int
    detalle: List[Dia] = field(default_factory=list)
    gracia_fecha: Optional[date] = None
    gracia_hora: Optional[str] = None
    notas: List[str] = field(default_factory=list)
    advertencias: List[str] = field(default_factory=list)
    avisos: List[dict] = field(default_factory=list)

    def a_dict(self):
        return {
            'fecha_ingresada': self.fecha_ingresada.isoformat(),
            'fecha_ingresada_texto': fecha_larga(self.fecha_ingresada),
            'fecha_perfeccion': self.fecha_perfeccion.isoformat(),
            'fecha_perfeccion_texto': fecha_larga(self.fecha_perfeccion),
            'inicio_computo': self.inicio_computo.isoformat(),
            'inicio_computo_texto': fecha_larga(self.inicio_computo),
            'vencimiento': self.vencimiento.isoformat(),
            'vencimiento_texto': fecha_larga(self.vencimiento),
            'modo': self.modo,
            'dias': self.dias,
            'detalle': [d.a_dict() for d in self.detalle],
            'gracia': ({
                'fecha': self.gracia_fecha.isoformat(),
                'fecha_texto': fecha_larga(self.gracia_fecha),
                'hora': self.gracia_hora,
            } if self.gracia_fecha and self.gracia_hora else None),
            'notas': self.notas,
            'advertencias': self.advertencias,
            'avisos': [{
                'motivo': a['motivo'],
                'desde': a['desde'].isoformat(),
                'hasta': a['hasta'].isoformat(),
                'fuente_url': a.get('fuente_url'),
            } for a in self.avisos],
        }


def perfeccion_notificacion(cal, tipo, fecha, localidad=None, fuero=None):
    """Fecha en que la notificación queda perfeccionada, y una nota explicativa."""
    if tipo in ('cedula', 'sne_urgente'):
        return fecha, None
    if tipo == 'sne':
        d = proximo_martes_o_viernes(fecha)
        nota = (f"Notificación electrónica: se perfecciona el martes o viernes inmediato "
                f"posterior ({fecha_larga(d)}).")
        if not cal.es_habil(d, localidad, fuero):
            motivo = cal.motivo_inhabil(d, localidad, fuero)
            h = cal.siguiente_habil(d, localidad, fuero)
            nota = (f"Notificación electrónica: el martes/viernes inmediato posterior "
                    f"({fecha_larga(d)}) es inhábil ({motivo}); se perfecciona el siguiente "
                    f"día hábil ({fecha_larga(h)}).")
            d = h
        return d, nota
    raise ErrorPlazo('Tipo de notificación inválido')


def calcular(cal, *, tipo_notificacion, fecha, dias, modo='habiles',
             localidad=None, fuero=None, hora_apertura='07:00'):
    """
    Calcula el vencimiento de un plazo.

    cal: Calendario (modulos/plazos/calendario.py)
    fecha: date. Para cédula, la fecha de la notificación; para electrónica,
        la fecha "Para Notif. Desde" (cuando la resolución quedó disponible).
    dias: cantidad de días del plazo.
    modo: 'habiles' o 'corridos'.
    """
    if modo not in MODOS:
        raise ErrorPlazo('Tipo de plazo inválido')
    if tipo_notificacion not in TIPOS_NOTIFICACION:
        raise ErrorPlazo('Tipo de notificación inválido')
    if not isinstance(dias, int) or dias < 1 or dias > MAX_DIAS_PLAZO:
        raise ErrorPlazo(f'La cantidad de días debe estar entre 1 y {MAX_DIAS_PLAZO}')

    perfeccion, nota_perf = perfeccion_notificacion(cal, tipo_notificacion, fecha, localidad, fuero)
    notas, advertencias = [], []
    if nota_perf:
        notas.append(nota_perf)
    if tipo_notificacion == 'sne':
        notas.append("Si no tenés domicilio electrónico habilitado en el SNE, igual quedás notificado "
                     "los martes y viernes (Ac. Gral. 15/18, art. 3).")
    if tipo_notificacion == 'cedula' and not cal.es_habil(fecha, localidad, fuero):
        advertencias.append(f"La fecha de notificación ingresada es inhábil "
                            f"({cal.motivo_inhabil(fecha, localidad, fuero)}). Verificá la fecha.")

    inicio = perfeccion + timedelta(days=1)
    detalle = []
    cursor = inicio
    contados = 0
    iteraciones = 0

    def dia_habil(d):
        return cal.es_habil(d, localidad, fuero)

    def info_inhabil(d):
        return cal.motivo_inhabil(d, localidad, fuero), cal.fuente_url(d, localidad, fuero)

    if modo == 'habiles':
        while contados < dias:
            iteraciones += 1
            if iteraciones > LIMITE_ITERACION:
                raise ErrorPlazo('No se pudo calcular el plazo (demasiados días inhábiles seguidos)')
            if dia_habil(cursor):
                contados += 1
                detalle.append(Dia(cursor, True, 'Día hábil' + (' · primer día del plazo' if contados == 1 else ''),
                                   contador=contados))
            else:
                motivo, url = info_inhabil(cursor)
                detalle.append(Dia(cursor, False, motivo, fuente_url=url))
            ultimo = cursor
            cursor += timedelta(days=1)
        vencimiento = ultimo
        # Inicio del cómputo = primer día hábil contado (puede ser posterior a perfección + 1)
        inicio_computo = next(d.fecha for d in detalle if d.habil)
    else:
        for n in range(1, dias + 1):
            motivo, url = info_inhabil(cursor) if not dia_habil(cursor) else (None, None)
            detalle.append(Dia(cursor, motivo is None,
                               ('Día corrido' if motivo is None else f'Día corrido (inhábil: {motivo})'),
                               contador=n, fuente_url=url))
            ultimo = cursor
            cursor += timedelta(days=1)
        vencimiento = ultimo
        if not dia_habil(vencimiento):
            while not dia_habil(cursor):
                iteraciones += 1
                if iteraciones > LIMITE_ITERACION:
                    raise ErrorPlazo('No se pudo calcular el plazo')
                motivo, url = info_inhabil(cursor)
                detalle.append(Dia(cursor, False, f'Prórroga: {motivo}', fuente_url=url))
                cursor += timedelta(days=1)
            detalle.append(Dia(cursor, True, 'Prórroga al primer día hábil siguiente'))
            notas.append(f"El último día corrido ({fecha_larga(vencimiento)}) es inhábil "
                         f"({cal.motivo_inhabil(vencimiento, localidad, fuero)}): el plazo vence "
                         f"el siguiente día hábil.")
            vencimiento = cursor
        inicio_computo = inicio

    # Feria de julio sin cargar: puede haber días mal contados
    años = {d.fecha.year for d in detalle}
    for a in sorted(años):
        if any(d.fecha.year == a and d.fecha.month == 7 for d in detalle) and not cal.tiene_feria_julio(a):
            advertencias.append(f"La feria judicial de julio de {a} todavía no está publicada en el calendario: "
                                f"si tu plazo atraviesa julio, verificá las fechas de la feria.")

    # Hora de gracia: primeras 2 horas hábiles del día hábil siguiente al vencimiento
    gracia_fecha = cal.siguiente_habil(vencimiento, localidad, fuero)
    try:
        h, m = (int(x) for x in hora_apertura.split(':'))
        gracia_hora = (datetime(2000, 1, 1, h, m) + timedelta(hours=2)).strftime('%H:%M')
    except (ValueError, AttributeError, TypeError):
        gracia_hora = None   # hora de apertura mal configurada: se muestra la fecha sin hora

    avisos = cal.avisos(inicio, vencimiento, localidad, fuero)

    return Resultado(
        fecha_ingresada=fecha, fecha_perfeccion=perfeccion, inicio_computo=inicio_computo,
        vencimiento=vencimiento, modo=modo, dias=dias, detalle=detalle,
        gracia_fecha=gracia_fecha, gracia_hora=gracia_hora,
        notas=notas, advertencias=advertencias, avisos=avisos,
    )
