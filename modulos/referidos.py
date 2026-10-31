"""
Programa de referidos de Foja ("Recomendá Foja").

- Cada usuario tiene un link personal: /auth/signup?ref=<código>. El código es
  su ID firmado con SECRET_KEY (mismo mecanismo que el link de baja de promos),
  así nadie puede inventar el código de otra persona.
- Al registrarse con ese link se anota quién recomendó (anotar_referido), pero
  todavía no se da ningún premio.
- El premio (2 créditos para quien recomendó) se da cuando el recomendado
  hace su primera compra (premiar_referidor_si_corresponde, que se llama desde
  rutas/pagos.py al confirmar el pago). Así, crear cuentas falsas no rinde.
- Tope: TOPE_PREMIADOS recomendados premiados por usuario.
"""

import config
from itsdangerous import BadSignature, URLSafeSerializer

from modulos.models import User

SALT_REFERIDO = 'referido'

PREMIO_CREDITOS = 2
TOPE_PREMIADOS = 10


def _serializador():
    return URLSafeSerializer(config.SECRET_KEY, salt=SALT_REFERIDO)


def codigo_referido(user_id):
    """Código firmado para el link de recomendación de un usuario."""
    return _serializador().dumps(int(user_id))


def usuario_id_desde_codigo(codigo):
    """ID de usuario del código, o None si fue alterado o no es válido."""
    try:
        valor = _serializador().loads(codigo)
    except BadSignature:
        return None
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def url_referido(user_id):
    return f"{config.BASE_URL.rstrip('/')}/auth/signup?ref={codigo_referido(user_id)}"


def anotar_referido(nuevo_usuario, codigo):
    """
    Guarda quién recomendó a `nuevo_usuario` (si el código es válido y esa
    persona existe). No da créditos y no hace commit: lo hace quien llama.
    Devuelve True si quedó anotado.
    """
    if not codigo:
        return False
    referidor_id = usuario_id_desde_codigo(codigo)
    if referidor_id is None or referidor_id == nuevo_usuario.id:
        return False
    if User.query.get(referidor_id) is None:
        return False
    nuevo_usuario.referido_por_id = referidor_id
    return True


def premiados_de(referidor_id):
    """Cuántos recomendados de este usuario ya le dieron el premio."""
    return User.query.filter_by(referido_por_id=referidor_id, referido_premiado=True).count()


def premiar_referidor_si_corresponde(usuario):
    """
    Se llama cuando una compra de `usuario` queda confirmada. Si vino
    recomendado, todavía no premió y quien lo recomendó no llegó al tope, le
    suma PREMIO_CREDITOS al que recomendó y marca a `usuario` como premiado.

    No hace commit. Devuelve el usuario que recomendó si hubo premio (para
    avisarle por mail), o None.
    """
    if not usuario.referido_por_id or usuario.referido_premiado:
        return None
    referidor = User.query.get(usuario.referido_por_id)
    if referidor is None:
        return None
    if premiados_de(referidor.id) >= TOPE_PREMIADOS:
        return None
    referidor.creditos_disponibles += PREMIO_CREDITOS
    usuario.referido_premiado = True
    return referidor
