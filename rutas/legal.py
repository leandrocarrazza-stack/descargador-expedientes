"""
Rutas legales: términos y condiciones y política de privacidad.

GET /terminos   → Términos y condiciones de uso (incluye quién es el responsable
                  y, en el artículo 25, la política de privacidad)
GET /privacidad → Redirige al artículo 25 de los Términos

Son públicas (sin login): se leen antes de crear la cuenta. El texto vive en
templates/legal_terminos.html; los datos de la persona responsable y la versión vigente llegan por context processor
(ver inyectar_responsable en servidor.py).
"""

from flask import Blueprint, redirect, render_template, url_for

legal_bp = Blueprint('legal', __name__)


@legal_bp.route('/terminos', methods=['GET'])
def terminos():
    return render_template('legal_terminos.html')


@legal_bp.route('/privacidad', methods=['GET'])
def privacidad():
    # La política de privacidad es el artículo 25 de los Términos; esta ruta
    # se conserva para los enlaces ya publicados.
    return redirect(url_for('legal.terminos', _anchor='art-25'))
