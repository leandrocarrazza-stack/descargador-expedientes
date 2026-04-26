"""
Descargador de Fallos desde Gmail
==================================

Usa Gmail API con OAuth2 para descargar adjuntos PDF
de emails de scamaragualeguaychu@gmail.com.
"""

import logging
import json
import base64
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List

try:
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    pass

import config
from modulos.database import db
from modulos.models import GmailOAuthToken, EmailFallo, Fallo

logger = logging.getLogger(__name__)


class GmailDownloader:
    """
    Conecta a Gmail via API v1 con OAuth2.
    Descarga adjuntos PDF de emails del remitente configurado.
    """

    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

    def __init__(self, app_context):
        """
        Args:
            app_context: Flask app instance
        """
        self.app = app_context
        self.gmail_account = config.GMAIL_TARGET_ACCOUNT
        self.source_email = config.GMAIL_SOURCE_EMAIL
        self.pdfs_dir = config.JURISPRUDENCIA_PDFS_DIR
        self.pdfs_dir.mkdir(parents=True, exist_ok=True)

    def get_credentials(self) -> Credentials:
        """Recupera credenciales desde la BD con auto-refresh de token."""
        try:
            oauth_token = GmailOAuthToken.query.filter_by(
                gmail_account=self.gmail_account
            ).first()

            if not oauth_token:
                logger.warning(f"No se encontraron credenciales para {self.gmail_account}")
                return None

            token_dict = oauth_token.get_token()
            creds = Credentials.from_authorized_user_info(
                token_dict,
                scopes=self.SCOPES
            )

            # Auto-refresh si está expirado
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Guardar token refrescado
                oauth_token.set_token({
                    'access_token': creds.token,
                    'refresh_token': creds.refresh_token,
                    'scope': creds.scopes,
                    'token_uri': creds.token_uri,
                    'client_id': creds.client_id,
                    'client_secret': creds.client_secret,
                    'type': 'authorized_user'
                })
                oauth_token.ultima_descarga = datetime.utcnow()
                db.session.commit()

            return creds

        except Exception as e:
            logger.error(f"Error recuperando credenciales: {e}")
            return None

    def descargar_emails_nuevos(self) -> Dict:
        """
        Consulta Gmail por emails del remitente que NO estén en BD.
        Descarga todos los adjuntos PDF.

        Returns:
            dict: {'emails_procesados': N, 'pdfs_descargados': M, 'errores': [...]}
        """
        try:
            creds = self.get_credentials()
            if not creds:
                return {
                    'emails_procesados': 0,
                    'pdfs_descargados': 0,
                    'errores': ['No se encontraron credenciales de Gmail']
                }

            service = build('gmail', 'v1', credentials=creds)

            # Buscar emails del remitente
            query = f'from:{self.source_email} has:attachment filename:pdf'
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=100
            ).execute()

            messages = results.get('messages', [])
            emails_procesados = 0
            pdfs_descargados = 0
            errores = []

            for msg_metadata in messages:
                try:
                    msg_id = msg_metadata['id']

                    # Verificar si ya está en BD
                    if EmailFallo.query.filter_by(gmail_message_id=msg_id).first():
                        logger.info(f"Email {msg_id} ya procesado, omitiendo")
                        continue

                    # Obtener mensaje completo
                    msg = service.users().messages().get(
                        userId='me',
                        id=msg_id,
                        format='full'
                    ).execute()

                    headers = msg['payload'].get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sin asunto')
                    sender = next((h['value'] for h in headers if h['name'] == 'From'), self.source_email)
                    date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')

                    # Descargar adjuntos
                    pdfs_del_email = self._descargar_adjuntos(service, msg)
                    pdfs_descargados += len(pdfs_del_email)

                    # Guardar metadata en BD
                    email_fallo = EmailFallo(
                        gmail_message_id=msg_id,
                        remitente=sender,
                        asunto=subject,
                        fecha_email=datetime.utcnow(),
                        procesado_en=datetime.utcnow(),
                        adjuntos_count=len(pdfs_del_email)
                    )
                    db.session.add(email_fallo)
                    db.session.flush()

                    # Crear registros Fallo para cada PDF
                    for pdf_path, pdf_name in pdfs_del_email:
                        fallo = Fallo(
                            email_id=email_fallo.id,
                            nombre_archivo=pdf_name,
                            ruta_pdf=str(pdf_path),
                            tamano_bytes=pdf_path.stat().st_size,
                            tribunal='STJER',
                            estado_extraccion='pendiente'
                        )
                        db.session.add(fallo)

                    db.session.commit()
                    emails_procesados += 1
                    logger.info(f"[OK] Email {msg_id}: {len(pdfs_del_email)} PDFs descargados")

                except Exception as e:
                    logger.error(f"Error procesando email {msg_id}: {e}")
                    errores.append(f"Email {msg_id}: {str(e)}")
                    db.session.rollback()

            return {
                'emails_procesados': emails_procesados,
                'pdfs_descargados': pdfs_descargados,
                'errores': errores
            }

        except Exception as e:
            logger.error(f"Error en descargar_emails_nuevos: {e}")
            return {
                'emails_procesados': 0,
                'pdfs_descargados': 0,
                'errores': [str(e)]
            }

    def _descargar_adjuntos(self, service, msg: Dict) -> List[tuple]:
        """
        Descarga adjuntos PDF de un mensaje.

        Args:
            service: Google API service instance
            msg: Mensaje Gmail

        Returns:
            list: [(Path, filename), ...]
        """
        pdfs = []

        def get_parts(payload):
            """Itera sobre partes del mensaje (puede ser multipart)"""
            if 'parts' in payload:
                for part in payload['parts']:
                    yield part
            else:
                yield payload

        try:
            payload = msg.get('payload', {})

            for part in get_parts(payload):
                if part.get('filename'):
                    filename = part['filename']
                    if filename.lower().endswith('.pdf'):
                        part_id = part.get('partId')
                        if part_id:
                            data = service.users().messages().attachments().get(
                                userId='me',
                                messageId=msg['id'],
                                id=part_id
                            ).execute()

                            file_data = base64.urlsafe_b64decode(
                                data.get('data', '')
                            )

                            # Guardar PDF
                            pdf_path = self.pdfs_dir / filename
                            with open(pdf_path, 'wb') as f:
                                f.write(file_data)

                            pdfs.append((pdf_path, filename))
                            logger.info(f"[OK] Descargado {filename}")

        except Exception as e:
            logger.error(f"Error descargando adjuntos: {e}")

        return pdfs
