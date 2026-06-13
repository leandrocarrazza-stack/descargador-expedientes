"""Modelos de datos para el descargador de expedientes."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Archivo:
    """Representa un archivo descargado."""
    id: str
    nombre: str
    url: str
    ruta_local: Optional[str] = None
    tipo: str = "documento"


@dataclass
class Movimiento:
    """Representa un movimiento dentro de un expediente."""
    id: str
    numero: int
    fecha: str
    descripcion: str
    archivos: List[Archivo] = field(default_factory=list)


@dataclass
class Expediente:
    """Representa un expediente completo."""
    numero: str
    tribunal: str
    caratula: str
    movimientos: List[Movimiento] = field(default_factory=list)


@dataclass
class EstadoPipeline:
    """Estados posibles del pipeline."""
    INICIANDO = "iniciando"
    BUSCANDO = "buscando"
    DESCARGANDO = "descargando"
    UNIFICANDO = "unificando"
    COMPLETADO = "completado"
    ERROR = "error"


@dataclass
class ResultadoPipeline:
    """Resultado de la ejecución del pipeline."""
    exitoso: bool
    archivo_pdf: Optional[str] = None
    error: Optional[str] = None
    expediente: Optional[Expediente] = None
