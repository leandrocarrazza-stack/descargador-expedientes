"""Sistema simple de logging para el descargador de expedientes."""

import logging
from pathlib import Path
from config import LOGS_DIR


def crear_logger(nombre: str) -> logging.Logger:
    """Crea un logger con el nombre especificado."""
    logger = logging.getLogger(nombre)

    # Configurar si no está ya configurado
    if not logger.handlers:
        logger.setLevel(logging.INFO)

        # Handler para consola
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # Handler para archivo
        log_file = LOGS_DIR / f"{nombre}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def obtener_logger(nombre: str) -> logging.Logger:
    """Obtiene un logger existente."""
    return logging.getLogger(nombre)


def configurar_logging_global(nivel: int = logging.INFO):
    """Configura el logging global."""
    logging.basicConfig(
        level=nivel,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
