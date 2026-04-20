"""
Rutas de contacto / soporte.

GET  /contacto  → Muestra el formulario
POST /contacto  → Guarda el mensaje y envía email al admin
"""

import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import current_user
from flask_mail import Message

import config
from modulos.database import db
from modulos.models import MensajeContacto
from modulos.extensions import mail

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

    _enviar_email_contacto(nombre, email, asunto, mensaje)

    flash('Mensaje enviado. Te responderemos a la brevedad.', 'success')
    return redirect(url_for('contacto.formulario'))


def _enviar_email_contacto(nombre, email_remitente, asunto, mensaje):
    """Envía el mensaje de contacto al email de soporte configurado."""
    destino = config.CONTACT_EMAIL
    if not destino:
        logger.warning("CONTACT_EMAIL no configurado — el email de contacto no se envió")
        return

    try:
        msg = Message(
            subject=f'[Contacto Foja] {asunto} — {nombre}',
            recipients=[destino],
            reply_to=email_remitente,
            body=(
                f"Nuevo mensaje de contacto\n"
                f"{'='*40}\n"
                f"Nombre:  {nombre}\n"
                f"Email:   {email_remitente}\n"
                f"Asunto:  {asunto}\n"
                f"{'='*40}\n\n"
                f"{mensaje}\n"
            ),
        )
        mail.send(msg)
        logger.info(f"Email de contacto enviado a {destino} — remitente: {email_remitente}")
    except Exception as e:
        logger.error(f"Error al enviar email de contacto: {e}")
