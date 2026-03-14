"""
MÓDULO 4: Unificación de archivos PDF
======================================

Combina múltiples PDFs descargados en un único archivo ordenado.
Convierte RTF a PDF antes de unificar.
"""

from pathlib import Path
from PyPDF2 import PdfMerger, PdfReader
import os
from .conversion import crear_conversor


class UnificadorPDF:
    """Cliente para unificar múltiples PDFs en uno solo."""

    def __init__(self, carpeta_temp, carpeta_salida=None):
        """
        Inicializa el unificador de PDFs.

        Args:
            carpeta_temp: Path de la carpeta con los archivos descargados
            carpeta_salida: Path de la carpeta donde guardar el PDF unificado
        """
        self.carpeta_temp = Path(carpeta_temp)
        self.carpeta_salida = Path(carpeta_salida) if carpeta_salida else self.carpeta_temp
        self.carpeta_salida.mkdir(parents=True, exist_ok=True)

    def unificar(self, numero_expediente, archivos_descargados):
        """
        Unifica múltiples PDFs en un solo archivo.

        Args:
            numero_expediente: Número del expediente (para el nombre del archivo)
            archivos_descargados: Lista de dicts con {path, tipo, movimiento}

        Retorna:
            Path: Ruta del archivo PDF unificado, o None si falla
        """
        if not archivos_descargados:
            print("[WARN]  No hay archivos para unificar")
            return None

        print(f"\n Unificando {len(archivos_descargados)} archivo(s) en PDF único...\n")

        try:
            # Ordenar archivos por movimiento (índice) en orden NORMAL (ascendente)
            # Mesa Virtual muestra los movimientos de más reciente a más antiguo (1, 2, 3...)
            # donde 1 es el más reciente y el último es el más antiguo
            # Queremos invertir para mostrar de más antiguo a más reciente
            # Por eso ordenamos en REVERSO (de mayor a menor índice)
            archivos_ordenados = sorted(
                archivos_descargados,
                key=lambda x: x.get('movimiento', 0),
                reverse=True  # Invertir: último movimiento primero (más antiguo)
            )

            print(f"    Orden de procesamiento (de más antiguo a más reciente):")
            for archivo in archivos_ordenados:
                print(f"      Movimiento {archivo.get('movimiento')}: {archivo['path'].name}")

            # Crear conversor para RTF
            conversor = crear_conversor()

            # Convertir archivos RTF a PDF si es necesario
            print(f"\n    Convirtiendo archivos RTF a PDF...")
            for archivo_info in archivos_ordenados:
                ruta_archivo = archivo_info['path']

                # Si es RTF, convertir a PDF
                if ruta_archivo.suffix.lower() == '.rtf' or 'RTF' in ruta_archivo.name:
                    print(f"      Convirtiendo {ruta_archivo.name}...", end=" ", flush=True)
                    ruta_pdf = conversor.convertir_rtf_a_pdf(ruta_archivo)
                    if ruta_pdf:
                        archivo_info['path'] = ruta_pdf
                        print("[OK]")
                    else:
                        print("[NO]")

            # Crear merger
            merger = PdfMerger()

            archivos_válidos = 0

            for i, archivo_info in enumerate(archivos_ordenados, 1):
                ruta_archivo = archivo_info['path']

                print(f"   [{i}/{len(archivos_ordenados)}] Procesando {ruta_archivo.name}...", end=" ", flush=True)

                try:
                    # Verificar que el archivo exista
                    if not ruta_archivo.exists():
                        print("[NO] (no existe)")
                        continue

                    # Verificar que sea un PDF válido (tolerante con errores menores)
                    num_pages = 0
                    try:
                        reader = PdfReader(str(ruta_archivo))
                        num_pages = len(reader.pages)
                        if num_pages == 0:
                            print("[NO] (PDF vacío)")
                            continue
                    except Exception as e:
                        # PDF está dañado pero intentamos usarlo igual si tiene contenido
                        error_msg = str(e).lower()
                        if 'eof' in error_msg or 'broken' in error_msg or 'damaged' in error_msg:
                            # PDF tiene daño menor (EOF incompleto) pero intentamos
                            tamaño = ruta_archivo.stat().st_size
                            if tamaño < 100:  # Muy pequeño = probablemente corrompido
                                print(f"[NO] (PDF muy pequeño: {tamaño} bytes)")
                                continue
                            else:
                                # Intentar usar de todas formas
                                print(f"[WARN]  (PDF con daño menor, intentando usar...)", end=" ", flush=True)
                                num_pages = "?"
                        else:
                            print(f"[NO] (PDF inválido: {str(e)[:30]})")
                            continue

                    # Agregar al merger
                    merger.append(str(ruta_archivo))
                    archivos_válidos += 1
                    print(f"[OK] ({num_pages} páginas)")

                except Exception as e:
                    print(f"[NO] ({str(e)[:30]})")
                    continue

            # Guardar el PDF unificado
            if archivos_válidos == 0:
                print("\n[WARN]  Modo alternativo: PDFs dañados, copiando archivos como está...")
                merger.close()

                # Si hay solo un archivo, copiarlo directamente
                if len(archivos_ordenados) == 1:
                    print("   [LIST] Un solo archivo - copiando como PDF final...")
                    try:
                        import shutil
                        archivo_unico = archivos_ordenados[0]['path']
                        nombre_salida = f"Expediente_{numero_expediente}_UNIFICADO.pdf"
                        ruta_salida = self.carpeta_salida / nombre_salida

                        shutil.copy2(str(archivo_unico), str(ruta_salida))
                        tamaño = ruta_salida.stat().st_size
                        tamaño_mb = tamaño / (1024 * 1024)

                        print(f"    PDF creado (modo copia)")
                        print(f"      Tamaño: {tamaño_mb:.2f} MB")
                        print(f"      Ubicación: {ruta_salida}\n")

                        return ruta_salida
                    except Exception as e:
                        print(f"   [NO] Error copiando: {e}")
                        return None
                else:
                    print("   [NO] Múltiples archivos dañados, no se pueden unificar")
                    return None

            # Generar nombre del archivo de salida
            nombre_salida = f"Expediente_{numero_expediente}_UNIFICADO.pdf"
            ruta_salida = self.carpeta_salida / nombre_salida

            print(f"\n    Guardando archivo unificado: {nombre_salida}")

            # Escribir PDF unificado
            try:
                with open(ruta_salida, 'wb') as f:
                    merger.write(f)
            except Exception as e:
                print(f"   [NO] Error al escribir: {e}")
                merger.close()
                # Intenta modo alternativo si hay un solo archivo
                if len(archivos_ordenados) == 1:
                    print("   [LIST] Intentando modo alternativo (copiar)...")
                    try:
                        import shutil
                        archivo_unico = archivos_ordenados[0]['path']
                        shutil.copy2(str(archivo_unico), str(ruta_salida))
                        return ruta_salida
                    except:
                        return None
                return None

            merger.close()

            # Obtener tamaño del archivo
            tamaño = ruta_salida.stat().st_size
            tamaño_mb = tamaño / (1024 * 1024)

            print(f"    PDF unificado creado exitosamente")
            print(f"      Archivos unidos: {archivos_válidos}/{len(archivos_descargados)}")
            print(f"      Tamaño: {tamaño_mb:.2f} MB")
            print(f"      Ubicación: {ruta_salida}\n")

            return ruta_salida

        except Exception as e:
            print(f"\n[NO] Error unificando PDFs: {e}")
            return None

    def limpiar_temporales(self, mantener_originales=False):
        """
        Limpia los archivos temporales descargados.

        Args:
            mantener_originales: Si True, mantiene los archivos originales

        Retorna:
            int: Cantidad de archivos eliminados
        """
        if mantener_originales:
            return 0

        print("\n️  Limpiando archivos temporales...\n")

        eliminados = 0
        try:
            # Buscar todos los PDFs en la carpeta temporal
            for archivo in self.carpeta_temp.glob("*.pdf"):
                # No eliminar el archivo unificado
                if "UNIFICADO" not in archivo.name:
                    try:
                        archivo.unlink()
                        eliminados += 1
                        print(f"   [OK] Eliminado: {archivo.name}")
                    except Exception as e:
                        print(f"   [WARN]  Error eliminando {archivo.name}: {e}")

            if eliminados > 0:
                print(f"\n    {eliminados} archivo(s) temporal(es) eliminado(s)")
            else:
                print(f"\n   [INFO]  No hay archivos temporales para eliminar")

        except Exception as e:
            print(f"   [NO] Error limpiando temporales: {e}")

        return eliminados


def crear_unificador(carpeta_temp, carpeta_salida=None):
    """
    Función auxiliar para crear un unificador preconfigurado.

    Args:
        carpeta_temp: Carpeta con archivos descargados
        carpeta_salida: Carpeta donde guardar el PDF unificado

    Retorna:
        UnificadorPDF: Unificador listo para usar
    """
    return UnificadorPDF(carpeta_temp, carpeta_salida)
