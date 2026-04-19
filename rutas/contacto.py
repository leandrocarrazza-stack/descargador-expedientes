"""
Rutas de contacto / soporte.

GET  /contacto  → Muestra el formulario
POST /contacto  → Guarda el mensaje y confirma
"""

import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user

from modulos.database import db
from modulos.models import MensajeContacto

logger = logging.getLogger(__name__)

contacto_bp = Blueprint('contacto', __name__, url_prefix='/contacto')

ASUNTOS = [
    'Descarga fallida',
    'Reposición de créditos',
    'Problema técnico',
    'Consulta sobre planes',
    'Otro',
]


@contacto_bp.route('/', methods=['GET'])
def formulario():
    nombre = current_user.nombre or '' if current_user.is_authenticated else ''
    email = current_user.email if current_user.is_authenticated else ''
    return render_template('contacto.html', asuntos=ASUNTOS, nombre=nombre, email=email)


@contacto_bp.route('/', methods=['POST'])
def enviar():
    nombre = request.form.get('nombre', '').strip()
    email = request.form.get('email', '').strip().lower()
    asunto = request.form.get('asunto', '').strip()
    mensaje = request.form.get('mensaje', '').strip()

    errores = []
    if not nombre:
        errores.append('El nombre es obligatorio.')
    if not email or '@' not in email:
        errores.append('El email no es válido.')
    if asunto not in ASUNTOS:
        errores.append('Elegí un asunto de la lista.')
    if not mensaje or len(mensaje) < 10:
        errores.append('El mensaje es demasiado corto (mínimo 10 caracteres).')

    if errores:
        for e in errores:
            flash(e, 'danger')
        return render_template(
            'contacto.html',
            asuntos=ASUNTOS,
            nombre=nombre,
            email=email,
            asunto_sel=asunto,
            mensaje=mensaje,
        )

    registro = MensajeContacto(
        user_id=current_user.id if current_user.is_authenticated else None,
        nombre=nombre,
        email=email,
        asunto=asunto,
        mensaje=mensaje,
    )
    db.session.add(registro)
    db.session.commit()

    logger.info(f"Nuevo mensaje de contacto de {email} — asunto: {asunto}")

    flash('Mensaje enviado. Te responderemos a la brevedad.', 'success')
    return redirect(url_for('contacto.formulario'))
