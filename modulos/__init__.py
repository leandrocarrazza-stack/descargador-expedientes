"""Módulos del descargador de expedientes."""

# Excepciones personalizadas
from .excepciones import (
    MesaVirtualError,
    ErrorAutenticacion,
    ErrorBusqueda,
    ErrorDescarga,
    ErrorConversion,
    ErrorUnificacion,
    ErrorConfiguracion,
    ErrorValidacion,
)

# Sistema de logging
from .logger import crear_logger, obtener_logger, configurar_logging_global

# Modelos de datos tipificados
from .modelos import (
    NumeroExpediente,
    Expediente,
    Movimiento,
    Archivo,
    EstadoPipeline,
    ResultadoPipeline,
)

# Pipeline orquestador
from .pipeline import PipelineDescargador

# Clientes y orquestadores
from .login import ClienteSelenium, crear_cliente_sesion
from .navegacion import BuscadorExpedientes, crear_buscador
from .descarga import DescargadorArchivos, crear_descargador
from .conversion import ConversorRTF, crear_conversor
from .unificacion import UnificadorPDF, crear_unificador

__all__ = [
    # Excepciones
    "MesaVirtualError",
    "ErrorAutenticacion",
    "ErrorBusqueda",
    "ErrorDescarga",
    "ErrorConversion",
    "ErrorUnificacion",
    "ErrorConfiguracion",
    "ErrorValidacion",
    # Logging
    "crear_logger",
    "obtener_logger",
    "configurar_logging_global",
    # Modelos de datos
    "NumeroExpediente",
    "Expediente",
    "Movimiento",
    "Archivo",
    "EstadoPipeline",
    "ResultadoPipeline",
    # Pipeline
    "PipelineDescargador",
    # Clientes
    "ClienteSelenium",
    "crear_cliente_sesion",
    "BuscadorExpedientes",
    "crear_buscador",
    "DescargadorArchivos",
    "crear_descargador",
    "ConversorRTF",
    "crear_conversor",
    "UnificadorPDF",
    "crear_unificador",
]
