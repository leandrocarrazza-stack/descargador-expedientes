#!/usr/bin/env python3
"""
Script para descargar PDFs desde Gmail y poblar la base de datos de jurisprudencia.

Ejecutar con: python script_descargar_gmail.py
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Agregar proyecto a sys.path
sys.path.insert(0, str(Path(__file__).parent))

def check_existing_oauth_token():
    """
    Verifica si ya existe un token OAuth2 válido en la BD.
    Retorna True si existe.
    """
    from servidor import app
    from modulos.models import GmailOAuthToken
    import config

    try:
        with app.app_context():
            oauth_token = GmailOAuthToken.query.filter_by(
                gmail_account=config.GMAIL_TARGET_ACCOUNT
            ).first()

            if oauth_token:
                logger.info(f"✅ Token OAuth2 encontrado para {config.GMAIL_TARGET_ACCOUNT}")
                return True
            else:
                return False

    except Exception as e:
        logger.error(f"❌ Error verificando token: {e}")
        return False


def setup_oauth_token():
    """
    Obtiene un token OAuth2 de Gmail usando el flujo de autenticación.
    Retorna True si el token fue obtenido/actualizado exitosamente.
    """
    from google_auth_oauthlib.flow import Flow
    import config
    from modulos.database import db
    from modulos.models import GmailOAuthToken
    from servidor import app

    try:
        logger.info("=" * 70)
        logger.info("AUTENTICACIÓN OAUTH2 - GMAIL")
        logger.info("=" * 70)

        if not config.GMAIL_CLIENT_ID or not config.GMAIL_CLIENT_SECRET:
            logger.error("❌ GMAIL_CLIENT_ID o GMAIL_CLIENT_SECRET no configurados en .env")
            return False

        # Crear flujo OAuth2
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": config.GMAIL_CLIENT_ID,
                    "client_secret": config.GMAIL_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [config.GMAIL_OAUTH_REDIRECT_URI]
                }
            },
            scopes=['https://www.googleapis.com/auth/gmail.readonly']
        )
        flow.redirect_uri = config.GMAIL_OAUTH_REDIRECT_URI

        # Generar URL de autenticación
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )

        logger.info("\n📱 Abre este enlace en tu navegador:\n")
        logger.info(auth_url)
        logger.info("\n" + "=" * 70)
        logger.info("Después de autorizar, se te redirigirá a una URL.")
        logger.info("Copia TODO el código desde el navegador y pégalo aquí.")
        logger.info("=" * 70 + "\n")

        # Leer el código de autorización del usuario
        auth_code = input("Ingresa el código de autorización: ").strip()

        if not auth_code:
            logger.error("❌ No se ingresó código de autorización")
            return False

        # Intercambiar el código por un token
        logger.info("\n⏳ Intercambiando código por token de acceso...")
        flow.fetch_token(code=auth_code)
        creds = flow.credentials

        # Crear diccionario de token
        token_dict = {
            'access_token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'type': 'authorized_user'
        }

        # Guardar en BD dentro de contexto de app
        with app.app_context():
            oauth_token = GmailOAuthToken.query.filter_by(
                gmail_account=config.GMAIL_TARGET_ACCOUNT
            ).first()

            if not oauth_token:
                oauth_token = GmailOAuthToken(gmail_account=config.GMAIL_TARGET_ACCOUNT)
                db.session.add(oauth_token)

            oauth_token.set_token(token_dict)
            db.session.commit()

        logger.info(f"✅ Token OAuth2 guardado para {config.GMAIL_TARGET_ACCOUNT}")
        return True

    except Exception as e:
        logger.error(f"❌ Error en autenticación OAuth2: {e}", exc_info=True)
        return False


def descargar_pdfs_gmail():
    """
    Descarga PDFs desde Gmail y los guarda en la base de datos.
    """
    from servidor import app
    from modulos.jurisprudencia.gmail_downloader import GmailDownloader

    try:
        logger.info("\n" + "=" * 70)
        logger.info("DESCARGANDO PDFs DESDE GMAIL")
        logger.info("=" * 70 + "\n")

        with app.app_context():
            downloader = GmailDownloader(app)
            result = downloader.descargar_emails_nuevos()

            logger.info("\n" + "─" * 70)
            logger.info(f"📧 Emails procesados: {result['emails_procesados']}")
            logger.info(f"📄 PDFs descargados: {result['pdfs_descargados']}")

            if result['errores']:
                logger.warning(f"⚠️  Errores encontrados:")
                for error in result['errores']:
                    logger.warning(f"   - {error}")

            if result['pdfs_descargados'] > 0:
                logger.info("\n✅ Descarga completada exitosamente")
                return True
            else:
                logger.info("\n⚠️  No se descargaron PDFs (puede que no haya nuevos emails)")
                return False

    except Exception as e:
        logger.error(f"❌ Error descargando PDFs: {e}", exc_info=True)
        return False


def procesar_pdfs():
    """
    Procesa los PDFs descargados extrayendo texto e indexando en BD.
    """
    from servidor import app
    from modulos.jurisprudencia.pdf_extractor import ExtractorFallos

    try:
        logger.info("\n" + "=" * 70)
        logger.info("PROCESANDO PDFs - EXTRAYENDO TEXTO")
        logger.info("=" * 70 + "\n")

        with app.app_context():
            extractor = ExtractorFallos(app)
            result = extractor.procesar_pendientes_en_lote(limite=50)

            logger.info("\n" + "─" * 70)
            logger.info(f"📄 PDFs procesados: {result['procesados']}")
            logger.info(f"✅ Exitosos: {result['exitosos']}")
            logger.info(f"❌ Errores: {result['errores']}")

            if result['errores'] > 0:
                logger.warning(f"⚠️  {result['errores']} errores durante la extracción")

            if result['exitosos'] > 0:
                logger.info("\n✅ Procesamiento completado exitosamente")
                return True
            else:
                logger.info("\n⚠️  No se indexaron PDFs")
                return False

    except Exception as e:
        logger.error(f"❌ Error procesando PDFs: {e}", exc_info=True)
        return False


def mostrar_estadisticas():
    """
    Muestra estadísticas de los fallos indexados.
    """
    from servidor import app
    from modulos.models import Fallo, FalloTexto, EmailFallo

    try:
        with app.app_context():
            total_emails = EmailFallo.query.count()
            total_fallos = Fallo.query.count()
            indexados = FalloTexto.query.count()
            pendientes = Fallo.query.filter_by(estado_extraccion='pendiente').count()
            exitosos = Fallo.query.filter_by(estado_extraccion='indexado').count()

            logger.info("\n" + "=" * 70)
            logger.info("ESTADÍSTICAS DE LA BASE DE DATOS")
            logger.info("=" * 70)
            logger.info(f"📧 Emails procesados: {total_emails}")
            logger.info(f"📄 Fallos descargados: {total_fallos}")
            logger.info(f"✅ Fallos indexados: {indexados}")
            logger.info(f"🔄 Fallos con estado 'indexado': {exitosos}")
            logger.info(f"⏳ Pendientes de procesar: {pendientes}")
            logger.info("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"❌ Error mostrando estadísticas: {e}")


if __name__ == '__main__':
    logger.info("\n🚀 Iniciando proceso de descarga de jurisprudencia\n")

    # Paso 1: Verificar si ya existe token OAuth2
    if not check_existing_oauth_token():
        logger.info("No se encontró token OAuth2 existente. Iniciando autenticación...")
        if not setup_oauth_token():
            logger.error("❌ No se pudo obtener el token OAuth2. Abortando.")
            sys.exit(1)

    # Paso 2: Descargar PDFs desde Gmail
    if not descargar_pdfs_gmail():
        logger.warning("⚠️  No se descargaron PDFs, pero continuando...")

    # Paso 3: Procesar PDFs (opcional, solo si hay PDFs nuevos)
    procesar_pdfs()

    # Paso 4: Mostrar estadísticas finales
    mostrar_estadisticas()

    logger.info("✅ Proceso completado\n")
