"""
Blueprint: Jurisprudencia
==========================

Rutas para:
- Chat conversacional (/jurisprudencia/)
- Admin panel (/jurisprudencia/admin)
- OAuth2 Gmail (/jurisprudencia/admin/gmail-oauth-*)
- Descarga y procesamiento de PDFs
- Exportación Obsidian
"""

import logging
import io
import json
import zipfile
from datetime import datetime
from flask import (
    Blueprint, render_template, request, jsonify,
    current_app, session, redirect, url_for, send_file
)
from modulos.extensions import limiter, csrf

logger = logging.getLogger(__name__)

jurisprudencia_bp = Blueprint(
    'jurisprudencia',
    __name__,
    url_prefix='/jurisprudencia',
    template_folder='../templates/jurisprudencia'
)


# ═══════════════════════════════════════════════════════════════════════════
#  PÁGINA PRINCIPAL - CHAT
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/', methods=['GET'])
def chat_main():
    """Interfaz principal de chat de jurisprudencia."""
    return render_template('chat.html')


# ═══════════════════════════════════════════════════════════════════════════
#  API - CHAT CONVERSACIONAL
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/chat', methods=['POST'])
@csrf.exempt
@limiter.limit("5 per minute; 50 per hour")
def chat_api():
    """
    Endpoint de chat conversacional.

    Request:
        POST /jurisprudencia/chat
        Content-Type: application/json
        Body: {"mensaje": "consulta en lenguaje natural"}

    Response:
        {
            "respuesta": "texto de respuesta",
            "resultados": [{...}],
            "terminos_usados": [...],
            "voces_usadas": [...]
        }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON inválido'}), 400

        mensaje = data.get('mensaje', '').strip()
        if not mensaje:
            return jsonify({'error': 'Mensaje vacío'}), 400

        # Recuperar historial de sesión
        historial = session.get('jur_historial', [])

        import config
        if not config.ANTHROPIC_API_KEY:
            # Modo sin Claude: búsqueda directa por palabras clave
            return _busqueda_directa(mensaje, historial)

        # Inicializar componentes
        try:
            import anthropic
            from modulos.jurisprudencia.chat import ChatJurisprudencia
            from modulos.jurisprudencia.buscador import BuscadorJurisprudencia
            from modulos.database import db

            dialect = db.engine.dialect.name
            buscador = BuscadorJurisprudencia(dialect)
            tesauro = current_app.config.get('TESAURO', {})
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            chat = ChatJurisprudencia(client, buscador, tesauro)

            respuesta = chat.procesar_mensaje(mensaje, historial)

        except ImportError:
            return _busqueda_directa(mensaje, historial)

        # Actualizar historial (máx 10 mensajes)
        historial.append({'role': 'user', 'content': mensaje})
        historial.append({'role': 'assistant', 'content': respuesta.get('respuesta', '')})
        session['jur_historial'] = historial[-10:]

        return jsonify(respuesta), 200

    except Exception as e:
        logger.error(f"[ERROR] /chat: {e}", exc_info=True)
        return jsonify({'error': 'Error interno del servidor'}), 500


def _busqueda_directa(mensaje: str, historial: list) -> tuple:
    """Búsqueda sin Claude: tokeniza la consulta y busca directamente."""
    try:
        from modulos.jurisprudencia.tesauro import obtener_voces_para_consulta
        from modulos.jurisprudencia.buscador import BuscadorJurisprudencia
        from modulos.database import db

        tesauro = current_app.config.get('TESAURO', {})
        voces = obtener_voces_para_consulta(mensaje, tesauro)

        palabras_stop = {'y', 'o', 'de', 'la', 'el', 'en', 'un', 'una', 'los',
                         'las', 'del', 'al', 'por', 'para', 'con', 'que', 'si'}
        terminos = [t for t in mensaje.lower().split()
                    if t not in palabras_stop and len(t) > 2]

        dialect = db.engine.dialect.name
        buscador = BuscadorJurisprudencia(dialect)
        resultados = buscador.buscar(terminos=terminos, voces_tesauro=voces, limite=5)

        nota = " (Modo directo — IA no disponible, configure ANTHROPIC_API_KEY)"
        return jsonify({
            'respuesta': f'Búsqueda por términos: {", ".join(terminos[:3])}{nota}',
            'resultados': resultados,
            'terminos_usados': terminos,
            'voces_usadas': voces
        }), 200

    except Exception as e:
        logger.error(f"Error en búsqueda directa: {e}")
        return jsonify({'error': 'Error en búsqueda'}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  FALLO - DETALLE
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/fallo/<int:fallo_id>', methods=['GET'])
def ver_fallo(fallo_id):
    """Texto completo de un fallo."""
    try:
        from modulos.database import db
        from modulos.models import Fallo, FalloTexto

        fallo = db.session.get(Fallo, fallo_id)
        if not fallo:
            return jsonify({'error': 'Fallo no encontrado'}), 404

        texto = fallo.texto
        return jsonify({
            'id': fallo.id,
            'nombre_archivo': fallo.nombre_archivo,
            'tribunal': fallo.tribunal,
            'materia': fallo.materia,
            'fecha_fallo': fallo.fecha_fallo.isoformat() if fallo.fecha_fallo else None,
            'estado_extraccion': fallo.estado_extraccion,
            'sumarios': texto.get_sumarios() if texto else [],
            'voces': texto.get_voces_tesauro() if texto else [],
            'texto_completo': texto.contenido_texto if texto else None
        }), 200

    except Exception as e:
        logger.error(f"[ERROR] /fallo/{fallo_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  EXPORT - OBSIDIAN
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/export/obsidian', methods=['GET'])
def export_obsidian():
    """
    Genera un ZIP con un .md por fallo para importar a Obsidian.

    Formato de cada archivo:
    ---
    titulo: <nombre_archivo>
    fecha: <fecha_fallo>
    tribunal: <tribunal>
    materia: <materia>
    voces: [...]
    ---
    # Sumario
    <texto completo>
    """
    try:
        from modulos.database import db
        from modulos.models import Fallo

        fallos_indexados = Fallo.query.filter_by(estado_extraccion='indexado').all()

        if not fallos_indexados:
            return jsonify({'error': 'No hay fallos indexados para exportar'}), 404

        # Crear ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fallo in fallos_indexados:
                if not fallo.texto:
                    continue

                sumarios = fallo.texto.get_sumarios()
                voces = fallo.texto.get_voces_tesauro()
                fecha_str = fallo.fecha_fallo.isoformat() if fallo.fecha_fallo else 'desconocida'
                nombre_md = fallo.nombre_archivo.replace('.pdf', '.md')

                contenido = f"""---
titulo: {fallo.nombre_archivo}
fecha: {fecha_str}
tribunal: {fallo.tribunal or 'STJER'}
materia: {fallo.materia or 'Sin clasificar'}
voces: {json.dumps(voces, ensure_ascii=False)}
exportado: {datetime.utcnow().strftime('%Y-%m-%d')}
---

# {fallo.nombre_archivo}

**Tribunal:** {fallo.tribunal or 'STJER'}
**Materia:** {fallo.materia or 'Sin clasificar'}
**Fecha:** {fecha_str}
**Voces jurídicas:** {', '.join(voces) if voces else 'Sin voces'}

## Sumarios

"""
                for i, sumario in enumerate(sumarios, 1):
                    contenido += f"### Sumario {i}\n\n{sumario}\n\n"

                zf.writestr(f"jurisprudencia/{nombre_md}", contenido.encode('utf-8'))

        zip_buffer.seek(0)
        fecha_export = datetime.utcnow().strftime('%Y%m%d')

        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'jurisprudencia_stjer_{fecha_export}.zip'
        )

    except Exception as e:
        logger.error(f"[ERROR] /export/obsidian: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN - PANEL DE CONTROL
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/admin', methods=['GET'])
def admin_panel():
    """Panel de administración de jurisprudencia."""
    return render_template('admin.html')


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN - STATS
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/admin/stats', methods=['GET'])
def admin_stats():
    """Estadísticas del sistema de jurisprudencia."""
    try:
        from modulos.database import db
        from modulos.models import Fallo, FalloTexto, EmailFallo, GmailOAuthToken
        import config

        total_fallos = Fallo.query.count()
        fallos_indexados = Fallo.query.filter_by(estado_extraccion='indexado').count()
        fallos_pendientes = Fallo.query.filter_by(estado_extraccion='pendiente').count()
        fallos_error = Fallo.query.filter_by(estado_extraccion='error').count()
        emails_procesados = EmailFallo.query.count()

        # Estado de OAuth2
        oauth = GmailOAuthToken.query.filter_by(
            gmail_account=config.GMAIL_TARGET_ACCOUNT
        ).first()

        return jsonify({
            'total_fallos': total_fallos,
            'fallos_indexados': fallos_indexados,
            'fallos_pendientes': fallos_pendientes,
            'fallos_error': fallos_error,
            'emails_procesados': emails_procesados,
            'gmail_conectado': oauth is not None,
            'gmail_ultima_descarga': (
                oauth.ultima_descarga.isoformat()
                if oauth and oauth.ultima_descarga else None
            ),
            'tesauro_cargado': len(current_app.config.get('TESAURO', {})) > 0,
            'claude_configurado': bool(config.ANTHROPIC_API_KEY)
        }), 200

    except Exception as e:
        logger.error(f"[ERROR] /admin/stats: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN - DESCARGAR EMAILS AHORA
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/admin/descargar-ahora', methods=['POST'])
@csrf.exempt
def admin_descargar_ahora():
    """Trigger manual para descargar emails de Gmail."""
    try:
        from modulos.jurisprudencia.gmail_downloader import GmailDownloader

        downloader = GmailDownloader(current_app)
        resultado = downloader.descargar_emails_nuevos()

        return jsonify({
            'status': 'completado',
            'emails_procesados': resultado['emails_procesados'],
            'pdfs_descargados': resultado['pdfs_descargados'],
            'errores': resultado['errores']
        }), 200

    except Exception as e:
        logger.error(f"[ERROR] /admin/descargar-ahora: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN - PROCESAR PDFS PENDIENTES
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/admin/procesar-pdfs', methods=['POST'])
@csrf.exempt
def admin_procesar_pdfs():
    """Trigger manual para extraer texto de PDFs pendientes."""
    try:
        from modulos.jurisprudencia.pdf_extractor import ExtractorFallos

        extractor = ExtractorFallos()
        resultado = extractor.procesar_pendientes_en_lote(limite=50)

        return jsonify({
            'status': 'completado',
            'procesados': resultado['procesados'],
            'exitosos': resultado['exitosos'],
            'errores': resultado['errores']
        }), 200

    except Exception as e:
        logger.error(f"[ERROR] /admin/procesar-pdfs: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN - OAUTH2 GMAIL
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/admin/gmail-oauth-init', methods=['GET'])
def gmail_oauth_init():
    """Inicia el flujo OAuth2 de Gmail."""
    try:
        import config

        if not config.GMAIL_CLIENT_ID or not config.GMAIL_CLIENT_SECRET:
            return jsonify({
                'error': 'GMAIL_CLIENT_ID o GMAIL_CLIENT_SECRET no configurados en .env'
            }), 400

        from google_auth_oauthlib.flow import Flow

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

        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )

        session['oauth_state'] = state
        return redirect(auth_url)

    except Exception as e:
        logger.error(f"[ERROR] /gmail-oauth-init: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@jurisprudencia_bp.route('/admin/gmail-oauth-callback', methods=['GET'])
def gmail_oauth_callback():
    """Recibe el código OAuth2 de Google y guarda el token."""
    try:
        import config
        from google_auth_oauthlib.flow import Flow
        from modulos.database import db
        from modulos.models import GmailOAuthToken

        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')

        if error:
            return jsonify({'error': f'Error OAuth2: {error}'}), 400

        if not code:
            return jsonify({'error': 'Código OAuth2 faltante'}), 400

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
            scopes=['https://www.googleapis.com/auth/gmail.readonly'],
            state=state
        )
        flow.redirect_uri = config.GMAIL_OAUTH_REDIRECT_URI
        flow.fetch_token(code=code)

        creds = flow.credentials

        # Guardar token encriptado en BD
        token_dict = {
            'access_token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'type': 'authorized_user'
        }

        oauth_token = GmailOAuthToken.query.filter_by(
            gmail_account=config.GMAIL_TARGET_ACCOUNT
        ).first()

        if not oauth_token:
            oauth_token = GmailOAuthToken(gmail_account=config.GMAIL_TARGET_ACCOUNT)
            db.session.add(oauth_token)

        oauth_token.set_token(token_dict)
        db.session.commit()

        logger.info(f"[OK] Token OAuth2 guardado para {config.GMAIL_TARGET_ACCOUNT}")
        return redirect(url_for('jurisprudencia.admin_panel') + '?oauth=ok')

    except Exception as e:
        logger.error(f"[ERROR] /gmail-oauth-callback: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  EMBED - WIDGET PARA FOJA.COM.AR
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.route('/embed', methods=['GET'])
def embed_widget():
    """Widget embebible para foja.com.ar."""
    return render_template('embed.html')


# ═══════════════════════════════════════════════════════════════════════════
#  OVERRIDE DE SECURITY HEADERS PARA EMBED
# ═══════════════════════════════════════════════════════════════════════════

@jurisprudencia_bp.after_request
def jurisprudencia_headers(response):
    """
    Override global X-Frame-Options: DENY solo en /jurisprudencia/embed
    para permitir embedding en foja.com.ar
    """
    if request.path == '/jurisprudencia/embed':
        response.headers['X-Frame-Options'] = 'ALLOW-FROM https://foja.com.ar'
        csp = response.headers.get('Content-Security-Policy', '')
        csp = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self' https://foja.com.ar")
        response.headers['Content-Security-Policy'] = csp

    return response
