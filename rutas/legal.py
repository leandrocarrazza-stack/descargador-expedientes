"""
Rutas legales: términos y condiciones y política de privacidad.

GET /terminos   → Términos y condiciones de uso (incluye quién es el responsable
                  y, en el artículo 25, la política de privacidad)
GET /privacidad → Redirige al artículo 25 de los Términos

Son públicas (sin login): se leen antes de crear la cuenta. El texto vive en
templates/_terminos_texto.html (lo usan la página y los popups de aceptación); los datos de la persona responsable y la versión vigente llegan por context processor
(ver inyectar_responsable en servidor.py).
"""

import logging

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import config
from modulos.auth import ip_cliente, registrar_aceptacion_terminos
from modulos.database import db

logger = logging.getLogger(__name__)

legal_bp = Blueprint('legal', __name__)


@legal_bp.route('/terminos', methods=['GET'])
def terminos():
    return render_template('legal_terminos.html')


@legal_bp.route('/privacidad', methods=['GET'])
def privacidad():
    # La política de privacidad es el artículo 25 de los Términos; esta ruta
    # se conserva para los enlaces ya publicados.
    return redirect(url_for('legal.terminos', _anchor='art-25'))


@legal_bp.route('/terminos/aceptar', methods=['POST'])
@login_required
def aceptar_terminos():
    """Registra que el usuario logueado aceptó la versión vigente de los Términos (popup de base.html)."""
    # Misma regla que el popup: mientras el texto es borrador no hay nada que aceptar
    if config.TERMINOS_BORRADOR:
        return jsonify({'error': 'Los términos todavía no están publicados'}), 409
    try:
        registrar_aceptacion_terminos(current_user, config.TERMINOS_VERSION, ip_cliente(request))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.error("Error registrando aceptación de términos", exc_info=True)
        return jsonify({'error': 'Error interno del servidor'}), 500
    return jsonify({'version': config.TERMINOS_VERSION}), 200
