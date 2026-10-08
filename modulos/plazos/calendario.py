"""
Calendario judicial: junta las reglas (reglas.py) con los registros de la
base (lo que lee el sincronizador del sitio del STJER, o carga el admin) y
responde si un día es hábil para una localidad y un fuero.

Es una clase "pura": recibe las entradas por parámetro y no toca la base,
así se puede testear sin Flask. `cargar_calendario()` arma una a partir de
la tabla DiaInhabil.
"""

from datetime import date, timedelta

from modulos.plazos import reglas
from modulos.plazos.localidades import departamento_de

# Un rango más largo que esto se ignora (protege de datos mal parseados)
MAX_DIAS_RANGO = 62

FUEROS = {
    'civil': 'Civil y Comercial',
    'laboral': 'Laboral',
    'familia': 'Familia',
    'penal': 'Penal',
    'contencioso': 'Contencioso Administrativo',
    'paz': 'Paz',
}


class Calendario:
    def __init__(self, entradas_extra=(), usar_reglas=True):
        self._extra = [e for e in entradas_extra
                       if e.hasta >= e.desde and (e.hasta - e.desde).days <= MAX_DIAS_RANGO]
        self._usar_reglas = usar_reglas
        self._por_anio = {}   # año → {fecha: [Entrada, ...]}

    # ── índice por día ───────────────────────────────────────────────────
    def _indice(self, anio):
        if anio in self._por_anio:
            return self._por_anio[anio]
        indice = {}

        def agregar(entrada):
            d = entrada.desde
            while d <= entrada.hasta:
                if d.year == anio:
                    indice.setdefault(d, []).append(entrada)
                d += timedelta(days=1)

        if self._usar_reglas:
            for e in reglas.entradas_del_anio(anio):
                agregar(e)
        for e in self._extra:
            if e.desde.year <= anio <= e.hasta.year:
                agregar(e)
        self._por_anio[anio] = indice
        return indice

    def entradas_del_dia(self, fecha):
        return self._indice(fecha.year).get(fecha, [])

    # ── ¿esta entrada rige para esta localidad / fuero? ──────────────────
    @staticmethod
    def _rige(entrada, localidad, fuero):
        if entrada.fuero and entrada.fuero != fuero:
            return False
        if entrada.alcance == 'provincia':
            return True
        if not localidad:
            return False
        if entrada.alcance == 'departamento':
            return entrada.departamento == departamento_de(localidad)
        if entrada.alcance == 'localidad':
            return entrada.localidad == localidad
        return False

    def aplicables(self, fecha, localidad=None, fuero=None):
        """Entradas que hacen inhábil ese día para esa localidad y fuero."""
        return [e for e in self.entradas_del_dia(fecha)
                if e.descuenta and self._rige(e, localidad, fuero)]

    # ── API principal ────────────────────────────────────────────────────
    def motivo_inhabil(self, fecha, localidad=None, fuero=None):
        """Texto con el motivo por el que el día es inhábil, o None si es hábil."""
        if fecha.weekday() == 5:
            return 'Sábado'
        if fecha.weekday() == 6:
            return 'Domingo'
        ents = self.aplicables(fecha, localidad, fuero)
        if not ents:
            return None
        return ' + '.join(dict.fromkeys(e.motivo for e in ents))

    def es_habil(self, fecha, localidad=None, fuero=None):
        return self.motivo_inhabil(fecha, localidad, fuero) is None

    def fuente_url(self, fecha, localidad=None, fuero=None):
        """Link a la noticia/acuerdo que declaró inhábil ese día (si hay)."""
        for e in self.aplicables(fecha, localidad, fuero):
            if e.fuente_url:
                return e.fuente_url
        return None

    def siguiente_habil(self, fecha, localidad=None, fuero=None):
        """El primer día hábil posterior a `fecha` (no incluye `fecha`)."""
        d = fecha + timedelta(days=1)
        while not self.es_habil(d, localidad, fuero):
            d += timedelta(days=1)
        return d

    def tiene_feria_julio(self, anio):
        return any(e.tipo == 'feria_julio' for ents in self._indice(anio).values() for e in ents)

    def avisos(self, desde, hasta, localidad=None, fuero=None):
        """
        Inhábiles que NO se descontaron pero que el usuario conviene que sepa:
        - los de un organismo puntual de SU localidad (ej. "inhábil para el Juzgado de Paz de X"),
        - los parciales (de medio día) de toda la provincia, su departamento o su localidad,
        - los de un fuero cuando no eligió ninguno.
        """
        salida = []
        vistos = set()
        d = desde
        while d <= hasta:
            for e in self.entradas_del_dia(d):
                clave = (e.motivo, e.desde, e.fuente_url)
                if clave in vistos:
                    continue
                if not e.descuenta:
                    if e.alcance == 'organismo':
                        relevante = bool(localidad) and e.localidad == localidad
                    else:   # parcial de provincia / departamento / localidad
                        relevante = self._rige(e, localidad, e.fuero or fuero)
                elif e.fuero and not fuero:
                    relevante = self._rige(e, localidad, e.fuero)
                else:
                    relevante = False
                if relevante:
                    vistos.add(clave)
                    salida.append({
                        'desde': e.desde, 'hasta': e.hasta, 'motivo': e.motivo,
                        'fuente_url': e.fuente_url,
                    })
            d += timedelta(days=1)
        return salida


def cargar_calendario():
    """Arma un Calendario con las entradas guardadas en la base (tabla DiaInhabil)."""
    from modulos.models import DiaInhabil
    q = DiaInhabil.query.filter_by(activo=True)
    return Calendario([f.a_entrada() for f in q.all()])
