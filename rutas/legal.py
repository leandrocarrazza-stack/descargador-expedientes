"""
Rutas legales: términos y condiciones y política de privacidad.

GET /terminos   → Términos y condiciones de uso (incluye quién es el responsable)
GET /privacidad → Política de privacidad

Son públicas (sin login): se leen antes de crear la cuenta. El texto vive en
templates/legal_terminos.html y templates/legal_privacidad.html; los datos de
la persona responsable y la versión vigente llegan por context processor
(ver inyectar_responsable en servidor.py).
"""

from flask import Blueprint, render_template

legal_bp = Blueprint('legal', __name__)


@legal_bp.route('/terminos', methods=['GET'])
def terminos():
    return render_template('legal_terminos.html')


@legal_bp.route('/privacidad', methods=['GET'])
def privacidad():
    return render_template('legal_privacidad.html')
