"""Excepciones personalizadas para el descargador de expedientes."""


class MesaVirtualError(Exception):
    """Excepción base para errores relacionados con Mesa Virtual."""
    pass


class ErrorAutenticacion(MesaVirtualError):
    """Error al autenticarse en Mesa Virtual."""
    pass


class ErrorBusqueda(MesaVirtualError):
    """Error al buscar expedientes."""
    pass


class ErrorDescarga(MesaVirtualError):
    """Error al descargar archivos."""
    pass


class ErrorConversion(MesaVirtualError):
    """Error al convertir archivos (RTF a PDF)."""
    pass


class ErrorUnificacion(MesaVirtualError):
    """Error al unificar PDFs."""
    pass


class ErrorConfiguracion(MesaVirtualError):
    """Error de configuración."""
    pass


class ErrorValidacion(MesaVirtualError):
    """Error de validación."""
    pass
