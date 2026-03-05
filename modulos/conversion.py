"""
MÓDULO: Conversión de RTF a PDF
================================

Convierte archivos RTF a PDF para unificarlos con otros PDFs.
Utiliza LibreOffice como herramienta principal de conversión.
"""

from pathlib import Path
import subprocess
import os
import time
import shutil


class ConversorRTF:
    """Conversor de archivos RTF a PDF con soporte para LibreOffice."""

    def __init__(self):
        """Inicializa el conversor y detecta LibreOffice."""
        self.libreoffice_path = self._detectar_libreoffice()
        self.disponible = self.libreoffice_path is not None

    def _detectar_libreoffice(self):
        """
        Detecta la ruta de LibreOffice en el sistema.

        Estrategias de búsqueda:
        1. Rutas estándar de Windows
        2. PATH del sistema
        3. Comandos 'where' o 'which'

        Retorna:
            str: Ruta de LibreOffice, o None si no se encuentra
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
                return ruta

        # Intentar usar comando 'where' (Windows) o 'which' (Linux/Mac)
        try:
            comando = "where" if os.name == 'nt' else "which"
            result = subprocess.run(
                [comando, "soffice"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass

        # Intentar con 'soffice' directamente si está en PATH
        try:
            result = subprocess.run(
                ["soffice", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return "soffice"
        except:
            pass

        return None

    def convertir_rtf_a_pdf(self, ruta_rtf, ruta_pdf=None):
        """
        Convierte un archivo RTF a PDF.

        Args:
            ruta_rtf: Path del archivo RTF
            ruta_pdf: Path del archivo PDF de salida (opcional)

        Retorna:
            Path: Ruta del archivo PDF generado, o None si falla
        """
        ruta_rtf = Path(ruta_rtf)

        # Validar que existe el archivo RTF
        if not ruta_rtf.exists():
            print(f"      ⚠️  Archivo no existe: {ruta_rtf}")
            return None

        # Validar que es RTF: primero por extensión, luego por magic bytes
        es_rtf_por_extension = ruta_rtf.suffix.lower() == '.rtf'
        es_rtf_por_contenido = False

        if not es_rtf_por_extension:
            try:
                with open(ruta_rtf, 'rb') as f:
                    magic = f.read(6)
                es_rtf_por_contenido = magic.startswith(b'{\\rtf')
                if es_rtf_por_contenido:
                    # Renombrar el archivo a .rtf para que LibreOffice lo reconozca
                    ruta_renombrada = ruta_rtf.with_suffix('.rtf')
                    ruta_rtf.rename(ruta_renombrada)
                    ruta_rtf = ruta_renombrada
                    print(f"      ℹ️  Renombrado a .rtf por contenido RTF detectado")
            except Exception:
                pass

        if not es_rtf_por_extension and not es_rtf_por_contenido:
            print(f"      ⚠️  No es archivo RTF: {ruta_rtf.name}")
            return None

        # Generar nombre de salida si no se proporciona
        if ruta_pdf is None:
            ruta_pdf = ruta_rtf.with_suffix('.pdf')
        else:
            ruta_pdf = Path(ruta_pdf)

        # Verificar si LibreOffice está disponible
        if not self.disponible:
            print(f"      ⚠️  LibreOffice no está instalado")
            print(f"         Descarga desde: https://www.libreoffice.org/download/")
            return None

        try:
            # Convertir con LibreOffice
            if self._convertir_con_libreoffice(ruta_rtf, ruta_pdf):
                print(f"      ✓ {ruta_rtf.name} → {ruta_pdf.name}")
                return ruta_pdf
            else:
                print(f"      ⚠️  No se pudo convertir: {ruta_rtf.name}")
                return None

        except Exception as e:
            print(f"      ❌ Error: {str(e)[:40]}")
            return None

    def _convertir_con_libreoffice(self, ruta_rtf, ruta_pdf):
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
            # Crear carpeta de destino si no existe
            ruta_pdf.parent.mkdir(parents=True, exist_ok=True)

            # Comando de LibreOffice
            comando = [
                self.libreoffice_path,
                "--headless",           # Sin interfaz gráfica
                "--convert-to", "pdf",  # Convertir a PDF
                "--outdir", str(ruta_pdf.parent),  # Carpeta de salida
                str(ruta_rtf)           # Archivo de entrada
            ]

            # Ejecutar conversión con timeout
            resultado = subprocess.run(
                comando,
                capture_output=True,
                text=True,
                timeout=60  # 60 segundos máximo
            )

            # Dar un poco de tiempo a que se escriba el archivo
            time.sleep(1)

            # Validar que el PDF se creó
            if not ruta_pdf.exists():
                # LibreOffice a veces guarda con el nombre original
                # Intentar encontrar el archivo convertido
                nombre_alternativo = ruta_rtf.with_suffix('.pdf')
                if nombre_alternativo.exists() and nombre_alternativo != ruta_pdf:
                    # Renombrar al destino correcto
                    nombre_alternativo.rename(ruta_pdf)
                    return True
                return False

            # Validar que el PDF tiene contenido
            tamaño = ruta_pdf.stat().st_size
            if tamaño < 500:  # Mínimo 500 bytes para un PDF válido
                print(f"         ⚠️  PDF muy pequeño ({tamaño} bytes)")
                return False

            # Validar que es un PDF válido
            try:
                with open(ruta_pdf, 'rb') as f:
                    header = f.read(4)
                    if header != b'%PDF':
                        print(f"         ⚠️  Archivo no es PDF válido")
                        return False
            except:
                return False

            return True

        except subprocess.TimeoutExpired:
            print(f"         ⏱️  Timeout (>60 segundos)")
            return False
        except Exception as e:
            print(f"         ❌ {str(e)[:40]}")
            return False

    def verificar_disponibilidad(self):
        """
        Verifica si LibreOffice está disponible.

        Retorna:
            bool: True si LibreOffice está instalado, False si no
        """
        return self.disponible

    def obtener_info(self):
        """
        Obtiene información sobre LibreOffice instalado.

        Retorna:
            dict: Información sobre la instalación
        """
        info = {
            'disponible': self.disponible,
            'ruta': self.libreoffice_path,
            'version': None
        }

        if self.disponible:
            try:
                result = subprocess.run(
                    [self.libreoffice_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    info['version'] = result.stdout.strip()
            except:
                pass

        return info


def crear_conversor():
    """
    Crea un conversor de RTF a PDF.

    Retorna:
        ConversorRTF: Conversor listo para usar
    """
    return ConversorRTF()
