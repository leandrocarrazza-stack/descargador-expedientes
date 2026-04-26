"""
Scheduler para tareas periódicas
================================

Usa APScheduler para ejecutar trabajos recurrentes.
Por ahora: descarga mensual de emails con fallos.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

scheduler = None


def init_scheduler(app):
    """
    Inicializa APScheduler dentro de la Flask app.
    Registra el job mensual de descarga de Gmail.

    Args:
        app: Flask app instance
    """
    global scheduler

    try:
        scheduler = BackgroundScheduler(timezone='America/Argentina/Buenos_Aires')

        # Agregar job: día 1 de cada mes, 03:00 ARS
        scheduler.add_job(
            func=job_descarga_mensual,
            trigger='cron',
            day=1,
            hour=3,
            minute=0,
            timezone='America/Argentina/Buenos_Aires',
            id='descarga_jurisprudencia_mensual',
            replace_existing=True,
            misfire_grace_time=3600  # Si server estuvo down, run si está dentro de 1h
        )

        scheduler.start()
        logger.info(
            "[OK] APScheduler iniciado — descarga jurisprudencia: "
            "día 1 de cada mes, 03:00 ARS"
        )

    except Exception as e:
        logger.error(f"[ERROR] No se pudo iniciar scheduler: {e}")


def job_descarga_mensual():
    """
    Job ejecutado el día 1 de cada mes a las 03:00 (Buenos Aires).

    Pasos:
    1. Descargar emails nuevos de Gmail
    2. Extraer texto de PDFs nuevos
    3. Loguear resultados
    """
    logger.info("[START] Job mensual de descarga de jurisprudencia")

    from flask import current_app
    from modulos.jurisprudencia.gmail_downloader import GmailDownloader
    from modulos.jurisprudencia.pdf_extractor import ExtractorFallos

    try:
        # Descargar emails
        downloader = GmailDownloader(current_app)
        resultado_descarga = downloader.descargar_emails_nuevos()
        logger.info(f"  → Descarga: {resultado_descarga}")

        # Procesar PDFs pendientes
        extractor = ExtractorFallos()
        resultado_extraccion = extractor.procesar_pendientes_en_lote(limite=50)
        logger.info(f"  → Extracción: {resultado_extraccion}")

        logger.info("[END] Job mensual completado exitosamente")

    except Exception as e:
        logger.error(f"[ERROR] Job mensual falló: {e}", exc_info=True)
