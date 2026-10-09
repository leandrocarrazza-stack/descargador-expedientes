# rutas/promos.py
"""
Baja de mails promocionales.

GET  /promociones/baja/<token>  → pantalla de confirmación (no cambia nada: los
                                  antivirus y las vistas previas de los clientes
                                  de correo abren los links sin que nadie los toque)
POST /promociones/baja/<token>  → aplica la baja

No requiere iniciar sesión: el token firmado identifica al usuario.
"""

import logging

from flask import Blueprint, render_template, request

from modulos.database import db
from modulos.extensions import limiter
from modulos.models import User
from modulos.promos import usuario_id_desde_token

logger = logging.getLogger(__name__)

promos_bp = Blueprint('promos', __name__, url_prefix='/promociones')


def _usuario(token):
    uid = usuario_id_desde_token(token)
    return db.session.get(User, uid) if uid else None


@promos_bp.route('/baja/<token>', methods=['GET', 'POST'])
@limiter.limit("30 per minute")
def baja(token):
    usuario = _usuario(token)
    if usuario is None:
        return render_template('baja_promociones.html', estado='invalido'), 400
    if request.method == 'POST':
        usuario.recibir_promociones = False
        db.session.commit()
        logger.info(f'[PROMOS] Baja de promociones del usuario {usuario.id}')
        return render_template('baja_promociones.html', estado='listo')
    return render_template('baja_promociones.html', estado='confirmar', ya_de_baja=usuario.recibir_promociones is False)
