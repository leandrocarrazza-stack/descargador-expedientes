"""
Mails promocionales de Foja (novedades y ofertas).

- Plantillas en templates/emails/ (una base común y una por campaña).
- Cada mail lleva un link de baja de un clic: un token firmado con SECRET_KEY
  que identifica al usuario, sin necesidad de iniciar sesión (rutas/promos.py).
- Esto es solo el armado del contenido: el envío lo hace quien administra
  Foja (ver scripts/exportar_destinatarios_promo.py).

Para una campaña nueva: crear templates/emails/promo_<nombre>.html (y .txt)
extendiendo promo_base.html, y agregar su asunto en ASUNTOS.
"""

import csv
import io
import re

import config
from flask import render_template
from itsdangerous import BadSignature, URLSafeSerializer

SALT_BAJA = 'baja-promos'

REMITENTE = 'asistenciafoja@gmail.com'

# campaña → asunto del mail
ASUNTOS = {
    'contador_plazos': 'Nuevo en Foja: calculá tus plazos judiciales en segundos',
    'recomenda': 'Recomendá Foja y ganá 2 descargas por cada colega',
}


def _serializador():
    return URLSafeSerializer(config.SECRET_KEY, salt=SALT_BAJA)


def token_baja(user_id):
    """Token firmado para el link de baja de un usuario."""
    return _serializador().dumps(int(user_id))


def usuario_id_desde_token(token):
    """ID de usuario del token, o None si fue alterado o no es válido."""
    try:
        valor = _serializador().loads(token)
    except BadSignature:
        return None
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def _base_url():
    return config.BASE_URL.rstrip('/')


def url_baja(user_id):
    return f'{_base_url()}/promociones/baja/{token_baja(user_id)}'


def nombre_para_saludo(usuario):
    """Primer nombre si lo tiene; si no, un saludo neutro."""
    nombre = (getattr(usuario, 'nombre', None) or '').strip()
    return nombre.split()[0] if nombre else ''


def url_whatsapp(link_referido):
    """Link de WhatsApp con el mensaje para compartir ya armado (incluye el link personal)."""
    from urllib.parse import quote
    texto = f'Uso Foja para descargar expedientes de Mesa Virtual en un solo PDF. Probala acá: {link_referido}'
    return f'https://wa.me/?text={quote(texto)}'


def _contexto_comun(campana, anio=None):
    """Datos de una campaña que son iguales para todas las personas."""
    from datetime import date
    from modulos.referidos import PREMIO_CREDITOS, TOPE_PREMIADOS
    return {
        'premio_creditos': PREMIO_CREDITOS,
        'tope_premiados': TOPE_PREMIADOS,
        'tope_descargas': PREMIO_CREDITOS * TOPE_PREMIADOS,
        'url_contador': f'{_base_url()}/plazos/',
        'url_img': f'{_base_url()}/static/img/promo',   # imágenes de los mails (static/img/promo/)
        'remitente': REMITENTE,
        'anio': anio or date.today().year,
        'asunto': ASUNTOS[campana],
    }


def renderizar_promo_mailerlite(campana, anio=None):
    """
    Arma el HTML de una campaña para pegar en el editor de HTML de MailerLite.

    No lleva datos de una persona: los que cambian por destinatario quedan como
    marcadores que MailerLite reemplaza al enviar — el link de baja
    ({$unsubscribe}), el saludo ({$name}) y, en la campaña 'recomenda', los
    campos personalizados link_referido y link_whatsapp (se cargan con el CSV de
    scripts/exportar_destinatarios_promo.py). Devuelve el HTML (str). Debe
    llamarse dentro de un contexto de aplicación Flask.
    """
    if campana not in ASUNTOS:
        raise ValueError(f'Campaña desconocida: {campana}')
    from markupsafe import Markup   # para que Jinja no escape las comillas del marcador
    contexto = _contexto_comun(campana, anio)
    contexto.update({
        'nombre': Markup("{$name|default('colega')}"),
        'url_baja': '{$unsubscribe}',
        'url_referido': '{$link_referido}',
        'url_whatsapp': '{$link_whatsapp}',
    })
    return render_template(f'emails/promo_{campana}.html', **contexto)


def renderizar_promo(campana, usuario, anio=None):
    """
    Arma el mail de una campaña para un usuario.

    Devuelve {'asunto', 'html', 'texto', 'remitente'}. Debe llamarse dentro de
    un contexto de aplicación Flask (usa render_template).
    """
    if campana not in ASUNTOS:
        raise ValueError(f'Campaña desconocida: {campana}')
    from modulos.referidos import url_referido
    link_referido = url_referido(usuario.id)
    contexto = _contexto_comun(campana, anio)
    contexto.update({
        'url_referido': link_referido,
        'url_whatsapp': url_whatsapp(link_referido),
        'nombre': nombre_para_saludo(usuario),
        'url_baja': url_baja(usuario.id),
    })
    return {
        'asunto': ASUNTOS[campana],
        'html': render_template(f'emails/promo_{campana}.html', **contexto),
        'texto': render_template(f'emails/promo_{campana}.txt', **contexto),
        'remitente': REMITENTE,
    }


# ---------------------------------------------------------------------------
# Lista de destinatarios (la usan scripts/exportar_destinatarios_promo.py y el
# panel de administración)
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

COLUMNAS_CSV = ['email', 'nombre', 'creditos', 'archivo', 'url_baja', 'url_referido', 'url_whatsapp']


def destinatarios_promo(User, todos=False):
    """
    Usuarios a los que corresponde mandarles una promoción: no se dieron de baja
    de las promociones y tienen un email con formato válido. Por defecto solo
    los que tienen crédito disponible; con todos=True, también los de 0 créditos.
    """
    q = User.query.order_by(User.id)
    if not todos:
        q = q.filter(User.creditos_disponibles > 0)
    return [u for u in q.all()
            if u.recibir_promociones is not False and EMAIL_RE.match((u.email or '').strip())]


def _celda_segura(valor):
    """
    Evita la inyección de fórmulas al abrir el CSV en Excel o Google Sheets: un
    texto escrito por un usuario (su nombre) que empiece con = + - @ se
    interpretaría como fórmula. Se le antepone un apóstrofo.
    """
    texto = '' if valor is None else str(valor)
    return "'" + texto if texto[:1] in ('=', '+', '-', '@', '\t', '\r') else texto


def csv_destinatarios(User, todos=False, archivo=None):
    """
    CSV (texto, con BOM para que Excel lo abra bien) con una fila por destinatario:
    email, nombre, creditos, archivo, url_baja y los dos campos personalizados de
    MailerLite (url_referido y url_whatsapp). `archivo` es una función
    email → nombre de archivo, o None para dejar la columna vacía.
    Devuelve (texto_csv, cantidad_de_filas).
    """
    from modulos.referidos import url_referido
    lista = destinatarios_promo(User, todos=todos)
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(COLUMNAS_CSV)
    for u in lista:
        link = url_referido(u.id)
        w.writerow([_celda_segura(u.email), _celda_segura(u.nombre), u.creditos_disponibles,
                    archivo(u.email) if archivo else '', url_baja(u.id), link, url_whatsapp(link)])
    return '\ufeff' + salida.getvalue(), len(lista)
