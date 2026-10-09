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

import config
from flask import render_template
from itsdangerous import BadSignature, URLSafeSerializer

SALT_BAJA = 'baja-promos'

REMITENTE = 'asistenciafoja@gmail.com'

# campaña → asunto del mail
ASUNTOS = {
    'contador_plazos': 'Nuevo en Foja: calculá tus plazos judiciales en segundos',
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


def renderizar_promo(campana, usuario, anio=None):
    """
    Arma el mail de una campaña para un usuario.

    Devuelve {'asunto', 'html', 'texto', 'remitente'}. Debe llamarse dentro de
    un contexto de aplicación Flask (usa render_template).
    """
    if campana not in ASUNTOS:
        raise ValueError(f'Campaña desconocida: {campana}')
    from datetime import date
    contexto = {
        'nombre': nombre_para_saludo(usuario),
        'url_contador': f'{_base_url()}/plazos/',
        'url_img': f'{_base_url()}/static/img/promo',   # imágenes de los mails (static/img/promo/)
        'url_baja': url_baja(usuario.id),
        'remitente': REMITENTE,
        'anio': anio or date.today().year,
        'asunto': ASUNTOS[campana],
    }
    return {
        'asunto': ASUNTOS[campana],
        'html': render_template(f'emails/promo_{campana}.html', **contexto),
        'texto': render_template(f'emails/promo_{campana}.txt', **contexto),
        'remitente': REMITENTE,
    }
