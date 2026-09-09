"""
Emails de aviso de descarga
============================

Cuando el usuario activa "avisarme por email" (Mi cuenta / checkbox en el
formulario de descarga), estas funciones se llaman al final de
_run_pipeline (rutas/descargas.py), dentro del thread del pipeline —
mismo patrón que _enviar_email_reset en rutas/auth.py: nunca deja que un
error de SMTP rompa el job (la descarga ya terminó, exitosa o no, antes
de intentar avisar).

El link al PDF se arma con config.BASE_URL en vez de url_for(_external=True):
este código corre en un thread de background sin request activo, así que
Flask no tiene de dónde sacar host/esquema salvo que SERVER_NAME esté
configurado (no lo está). url_for() sin _external sí funciona dentro de un
app_context (ya lo hay), y da la ruta relativa para concatenar con BASE_URL.
"""

import logging
from flask import current_app, url_for
from flask_mail import Message

from modulos.extensions import mail
import config

logger = logging.getLogger(__name__)


def _url_absoluta(endpoint, **kwargs):
    """
    Arma una URL externa fuera de un request real (este módulo solo corre
    dentro del thread del pipeline, con app_context pero sin request
    context). `url_for(_external=True)` normalmente necesita
    SERVER_NAME configurado para eso — en cambio, se abre un
    test_request_context() efímero atado a config.BASE_URL, el mecanismo
    que Flask expone justo para construir URLs fuera de un request real.
    """
    with current_app.test_request_context(base_url=config.BASE_URL):
        return url_for(endpoint, _external=True, **kwargs)


def enviar_email_descarga(user, expediente_db):
    """Avisa que la descarga terminó OK, con el link para bajar el PDF."""
    try:
        pdf_url = _url_absoluta('descargas.descargar_pdf', expediente_id=expediente_db.id)
        msg = Message(
            subject=f'Tu expediente {expediente_db.numero} está listo · Foja',
            recipients=[user.email],
            html=f"""
            <p>Hola,</p>
            <p>Terminamos de descargar y unificar el expediente <strong>{expediente_db.numero}</strong>.</p>
            <p><a href="{pdf_url}" style="background:#b8860b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;">
               Descargar PDF
            </a></p>
            <p>El enlace requiere que estés logueado en Foja, y va a seguir funcionando desde tu historial de descargas.</p>
            <p>El equipo de Foja</p>
            """,
            body=f"Tu expediente {expediente_db.numero} está listo. Descargalo en: {pdf_url}"
        )
        mail.send(msg)
        logger.info(f"[EMAIL] Aviso de descarga OK enviado a user {user.id}")
    except Exception:
        logger.warning(f"[EMAIL] No se pudo enviar el aviso de descarga a user {user.id}", exc_info=True)


def enviar_email_error(user, numero_expediente, mensaje):
    """Avisa que la descarga falló, con el motivo."""
    try:
        historial_url = _url_absoluta('descargas.historial_descargas')
        msg = Message(
            subject=f'No pudimos descargar el expediente {numero_expediente} · Foja',
            recipients=[user.email],
            html=f"""
            <p>Hola,</p>
            <p>Intentamos descargar el expediente <strong>{numero_expediente}</strong> pero no se pudo completar:</p>
            <p style="color:#b3261e;">{mensaje}</p>
            <p>No se descontó ningún crédito por este intento.</p>
            <p><a href="{historial_url}" style="color:#b8860b;">Ver mi historial</a></p>
            <p>El equipo de Foja</p>
            """,
            body=f"No pudimos descargar el expediente {numero_expediente}: {mensaje}\n\nHistorial: {historial_url}"
        )
        mail.send(msg)
        logger.info(f"[EMAIL] Aviso de error enviado a user {user.id}")
    except Exception:
        logger.warning(f"[EMAIL] No se pudo enviar el aviso de error a user {user.id}", exc_info=True)
