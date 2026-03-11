"""
MÓDULO: Conversión de RTF a PDF
================================

Convierte archivos RTF a PDF para unificarlos con otros PDFs.
Utiliza LibreOffice como herramienta principal de conversión.
"""

from pathlib import Path
from typing import List, Optional
import subprocess
import os
import time
import shutil

from modulos.logger import crear_logger
from modulos.excepciones import ErrorConversion

logger = crear_logger(__name__)


class ConversorRTF:
    """Conversor de archivos RTF a PDF con soporte para LibreOffice."""

    def __init__(self) -> None:
        """Inicializa el conversor y detecta LibreOffice."""
        self.libreoffice_path = self._detectar_libreoffice()
        self.disponible = self.libreoffice_path is not None

        if self.disponible:
            logger.info(f"LibreOffice detectado en: {self.libreoffice_path}")
        else:
            logger.warning("LibreOffice no detectado en el sistema")

    def _detectar_libreoffice(self) -> Optional[str]:
        """
        Detecta la ruta de LibreOffice en el sistema.

        Estrategias de búsqueda:
        1. Rutas estándar de Windows
        2. PATH del sistema
        3. Comandos 'where' o 'which'

        Retorna:
            Optional[str]: Ruta de LibreOffice, o None si no se encuentra
        """
        # Rutas posibles en Windows (ordenadas por probabilidad)
        posibles_rutas = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            r"C:\Program Files\LibreOffice\program\soffice",
            r"C:\Program Files (x86)\LibreOffice\program\soffice",
        ]

        # Buscar en rutas estándar
        for ruta in posibles_rutas:
            if os.path.exists(ruta):
                logger.debug(f"LibreOffice encontrado en ruta estándar: {ruta}")
                return ruta

        # Intentar usar comando 'where' (Windows) o 'which' (Linux/Mac)
        try:
            comando = "where" if os.name == "nt" else "which"
            result = subprocess.run([comando, "soffice"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                ruta = result.stdout.strip()
                logger.debug(f"LibreOffice encontrado via comando '{comando}': {ruta}")
                return ruta
        except Exception as e:
            logger.debug(f"Error al ejecutar comando '{comando}': {str(e)[:50]}")

        # Intentar con 'soffice' directamente si está en PATH
        try:
            result = subprocess.run(
                ["soffice", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                logger.debug("LibreOffice encontrado en PATH del sistema")
                return "soffice"
        except Exception as e:
            logger.debug(f"Error verificando LibreOffice en PATH: {str(e)[:50]}")

        logger.debug("LibreOffice no encontrado en ninguna ubicación")
        return None

    def convertir_rtf_a_pdf(
        self, ruta_rtf: Path, ruta_pdf: Optional[Path] = None
    ) -> Optional[Path]:
        """
        Convierte un archivo RTF a PDF.

        Args:
            ruta_rtf: Path del archivo RTF
            ruta_pdf: Path del archivo PDF de salida (opcional)

        Retorna:
            Optional[Path]: Ruta del archivo PDF generado, o None si falla

        Lanza:
            ErrorConversion: Si la conversión falla debido a problemas de LibreOffice
        """
        ruta_rtf = Path(ruta_rtf)

        # Validar que existe el archivo RTF
        if not ruta_rtf.exists():
            logger.warning(f"Archivo no existe: {ruta_rtf}")
            raise ErrorConversion(f"Archivo RTF no encontrado: {ruta_rtf}")

        # Validar que es RTF: primero por extensión, luego por magic bytes
        es_rtf_por_extension = ruta_rtf.suffix.lower() == ".rtf"
        es_rtf_por_contenido = False

        if not es_rtf_por_extension:
            try:
                with open(ruta_rtf, "rb") as f:
                    magic = f.read(6)
                es_rtf_por_contenido = magic.startswith(b"{\\rtf")
                if es_rtf_por_contenido:
                    # Renombrar el archivo a .rtf para que LibreOffice lo reconozca
                    ruta_renombrada = ruta_rtf.with_suffix(".rtf")
                    ruta_rtf.rename(ruta_renombrada)
                    ruta_rtf = ruta_renombrada
                    logger.debug(
                        f"Archivo renombrado a .rtf por contenido RTF detectado: {ruta_rtf.name}"
                    )
            except Exception as e:
                logger.debug(f"Error verificando magic bytes de {ruta_rtf.name}: {str(e)[:50]}")

        if not es_rtf_por_extension and not es_rtf_por_contenido:
            logger.warning(f"Archivo no es RTF válido: {ruta_rtf.name}")
            raise ErrorConversion(f"Archivo no es RTF válido: {ruta_rtf.name}")

        # Generar nombre de salida si no se proporciona
        if ruta_pdf is None:
            ruta_pdf = ruta_rtf.with_suffix(".pdf")
        else:
            ruta_pdf = Path(ruta_pdf)

        # Verificar si LibreOffice está disponible
        if not self.disponible:
            logger.error("LibreOffice no está instalado en el sistema")
            raise ErrorConversion(
                "LibreOffice no está instalado. "
                "Descargue desde: https://www.libreoffice.org/download/"
            )

        try:
            # Convertir con LibreOffice
            if self._convertir_con_libreoffice(ruta_rtf, ruta_pdf):
                logger.debug(f"Conversión exitosa: {ruta_rtf.name} → {ruta_pdf.name}")
                return ruta_pdf
            else:
                raise ErrorConversion(f"LibreOffice no pudo convertir: {ruta_rtf.name}")

        except ErrorConversion:
            raise
        except Exception as e:
            logger.error(f"Error inesperado en conversión: {str(e)[:50]}", exc_info=True)
            raise ErrorConversion(f"Error en conversión: {str(e)[:50]}")

    def _convertir_con_libreoffice(self, ruta_rtf: Path, ruta_pdf: Path) -> bool:
        """
        Convierte RTF a PDF usando LibreOffice.

        Estrategia:
        1. Ejecuta LibreOffice en modo headless (sin GUI)
        2. Convierte a PDF
        3. Guarda en carpeta de destino
        4. Valida que el PDF se creó correctamente

        Args:
            ruta_rtf: Path del archivo RTF
            ruta_pdf: Path del archivo PDF de salida

        Retorna:
            bool: True si la conversión fue exitosa
        """
        try:
            # Validar que libreoffice_path está disponible
            if not self.libreoffice_path:
                raise ErrorConversion("LibreOffice path no disponible")

            # Crear carpeta de destino si no existe
            ruta_pdf.parent.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Directorio de salida creado/verificado: {ruta_pdf.parent}")

            # Comando de LibreOffice
            comando = [
                self.libreoffice_path,
                "--headless",  # Sin interfaz gráfica
                "--convert-to",
                "pdf",  # Convertir a PDF
                "--outdir",
                str(ruta_pdf.parent),  # Carpeta de salida
                str(ruta_rtf),  # Archivo de entrada
            ]

            logger.debug(f"Ejecutando comando LibreOffice: {' '.join(comando)}")

            # Ejecutar conversión con timeout
            resultado = subprocess.run(
                comando, capture_output=True, text=True, timeout=60  # 60 segundos máximo
            )

            # Dar un poco de tiempo a que se escriba el archivo
            time.sleep(1)

            # Validar que el PDF se creó
            if not ruta_pdf.exists():
                # LibreOffice a veces guarda con el nombre original
                # Intentar encontrar el archivo convertido
                nombre_alternativo = ruta_rtf.with_suffix(".pdf")
                if nombre_alternativo.exists() and nombre_alternativo != ruta_pdf:
                    # Renombrar al destino correcto
                    logger.debug(f"Renombrando PDF generado de: {nombre_alternativo} a {ruta_pdf}")
                    nombre_alternativo.rename(ruta_pdf)
                    return True
                logger.warning(f"PDF no se creó en ubicación esperada: {ruta_pdf}")
                return False

            # Validar que el PDF tiene contenido
            tamaño = ruta_pdf.stat().st_size
            if tamaño < 500:  # Mínimo 500 bytes para un PDF válido
                logger.warning(f"PDF generado es muy pequeño: {tamaño} bytes (mínimo: 500)")
                return False

            logger.debug(f"Tamaño del PDF generado: {tamaño} bytes")

            # Validar que es un PDF válido
            try:
                with open(ruta_pdf, "rb") as f:
                    header = f.read(4)
                    if header != b"%PDF":
                        logger.warning(f"Archivo no es PDF válido (header incorrecto)")
                        return False
            except Exception as e:
                logger.warning(f"Error validando header PDF: {str(e)[:50]}")
                return False

            return True

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout en conversión LibreOffice (>60 segundos)")
            return False
        except Exception as e:
            logger.error(
                f"Error inesperado en _convertir_con_libreoffice: {str(e)[:50]}", exc_info=True
            )
            return False

    def convertir_multiples(self, archivos: List[Path]) -> List[Path]:
        """
        Convierte múltiples archivos RTF a PDF sin bloquear.

        Si una conversión individual falla, continúa con las siguientes.
        Se registran todos los errores pero no se detiene el proceso.

        Args:
            archivos: Lista de rutas de archivos RTF a convertir

        Retorna:
            List[Path]: Lista de rutas de PDFs generados exitosamente

        Ejemplo:
            conversor = crear_conversor()
            rtfs = [Path("doc1.rtf"), Path("doc2.rtf"), Path("doc3.rtf")]
            pdfs_generados = conversor.convertir_multiples(rtfs)
            logger.info(f"Se generaron {len(pdfs_generados)} PDFs de {len(rtfs)} RTFs")
        """
        pdfs_generados: List[Path] = []
        fallidos: List[Path] = []

        logger.info(f"Iniciando conversión de {len(archivos)} archivo(s) RTF a PDF")

        for idx, ruta_rtf in enumerate(archivos, 1):
            try:
                logger.debug(f"[{idx}/{len(archivos)}] Procesando: {ruta_rtf.name}")

                # Intentar convertir el archivo actual
                ruta_pdf = self.convertir_rtf_a_pdf(ruta_rtf)

                if ruta_pdf:
                    pdfs_generados.append(ruta_pdf)
                    logger.debug(f"[{idx}/{len(archivos)}] Conversión exitosa: {ruta_pdf.name}")
                else:
                    fallidos.append(ruta_rtf)
                    logger.warning(
                        f"[{idx}/{len(archivos)}] Conversión retornó None: {ruta_rtf.name}"
                    )

            except ErrorConversion as e:
                # Capturar errores de conversión sin detener el proceso
                fallidos.append(ruta_rtf)
                logger.warning(f"[{idx}/{len(archivos)}] Error en conversión: {str(e)}")

            except Exception as e:
                # Capturar errores inesperados
                fallidos.append(ruta_rtf)
                logger.error(
                    f"[{idx}/{len(archivos)}] Error inesperado procesando {ruta_rtf.name}: {str(e)[:50]}",
                    exc_info=True,
                )

        # Resumen final
        logger.info(
            f"Conversión completada: {len(pdfs_generados)} exitosas, {len(fallidos)} fallidas"
        )

        if fallidos:
            archivos_fallidos = ", ".join([f.name for f in fallidos])
            logger.warning(f"Archivos que fallaron: {archivos_fallidos}")

        return pdfs_generados

    def verificar_disponibilidad(self) -> bool:
        """
        Verifica si LibreOffice está disponible.

        Retorna:
            bool: True si LibreOffice está instalado, False si no
        """
        return self.disponible

    def obtener_info(self) -> dict:
        """
        Obtiene información sobre LibreOffice instalado.

        Retorna:
            dict: Información sobre la instalación con claves:
                - disponible (bool): Si LibreOffice está disponible
                - ruta (str): Ruta donde se encontró LibreOffice
                - version (str): Versión de LibreOffice, o None si no se pudo obtener
        """
        info = {"disponible": self.disponible, "ruta": self.libreoffice_path, "version": None}

        if self.disponible:
            try:
                result = subprocess.run(
                    [self.libreoffice_path, "--version"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    info["version"] = result.stdout.strip()
                    logger.debug(f"Versión de LibreOffice: {info['version']}")
            except Exception as e:
                logger.debug(f"No se pudo obtener versión de LibreOffice: {str(e)[:50]}")

        return info


def crear_conversor() -> ConversorRTF:
    """
    Factory function que crea un conversor de RTF a PDF.

    Retorna:
        ConversorRTF: Conversor listo para usar
    """
    return ConversorRTF()
