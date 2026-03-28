"""
MÓDULO 4: Unificación de archivos PDF
======================================

Combina múltiples PDFs descargados en un único archivo ordenado.
Convierte RTF a PDF antes de unificar.

OPTIMIZACIÓN DE MEMORIA:
  Para evitar errores Out-Of-Memory en servidores con RAM limitada,
  los PDFs se unen en lotes (LOTE_UNIFICACION). Ejemplo: si hay 50 PDFs
  y el lote es 10, primero se crean 5 PDFs intermedios (10 c/u)
  y después se unen esos 5 en el PDF final.
"""

from pathlib import Path
from PyPDF2 import PdfMerger, PdfReader
from typing import List, Optional
import os
import gc  # Garbage collector para liberar memoria entre lotes
from .conversion import crear_conversor
from .logger import crear_logger
from .excepciones import ErrorUnificacion

logger = crear_logger(__name__)

# Cantidad de PDFs a unir por lote. Más bajo = menos memoria, más lento.
# 10 es un buen balance para servidores con 512 MB de RAM.
LOTE_UNIFICACION = int(os.environ.get('LOTE_UNIFICACION', '10'))


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

    def unificar(self, numero_expediente: str, archivos_descargados: List[dict]) -> Optional[Path]:
        """
        Unifica múltiples PDFs en un solo archivo.

        Args:
            numero_expediente: Número del expediente (para el nombre del archivo)
            archivos_descargados: Lista de dicts con {path, tipo, movimiento}

        Retorna:
            Path: Ruta del archivo PDF unificado, o None si falla

        Raises:
            ErrorUnificacion: Si ocurre un error durante la unificación
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
                key=lambda x: x.get("movimiento", 0),
                reverse=True,  # Invertir: último movimiento primero (más antiguo)
            )

            print(f"    Orden de procesamiento (de más antiguo a más reciente):")
            for archivo in archivos_ordenados:
                logger.debug(f"Movimiento {archivo.get('movimiento')}: {archivo['path'].name}")

            # Crear conversor para RTF
            conversor = crear_conversor()

            # Convertir archivos RTF a PDF si es necesario
            print(f"\n    Convirtiendo archivos RTF a PDF...")
            for archivo_info in archivos_ordenados:
                ruta_archivo = archivo_info["path"]

                # Si es RTF, convertir a PDF
                if ruta_archivo.suffix.lower() == ".rtf" or "RTF" in ruta_archivo.name:
                    logger.debug(f"Convirtiendo {ruta_archivo.name}...")
                    ruta_pdf = conversor.convertir_rtf_a_pdf(ruta_archivo)
                    if ruta_pdf:
                        archivo_info['path'] = ruta_pdf
                        print("[OK]")
                    else:
                        print("[NO]")

            # ─── PASO 1: Validar todos los PDFs (sin acumular en memoria) ───
            rutas_validas = []
            for i, archivo_info in enumerate(archivos_ordenados, 1):
                ruta_archivo = archivo_info["path"]
                logger.debug(f"[{i}/{len(archivos_ordenados)}] Validando {ruta_archivo.name}...")

                try:
                    if not ruta_archivo.exists():
                        print("[NO] (no existe)")
                        continue

                    # Validar PDF (abrir, contar páginas, cerrar → libera memoria)
                    num_pages = 0
                    try:
                        reader = PdfReader(str(ruta_archivo))
                        num_pages = len(reader.pages)
                        del reader  # Liberar memoria del reader
                        if num_pages == 0:
                            print("[NO] (PDF vacío)")
                            continue
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "eof" in error_msg or "broken" in error_msg or "damaged" in error_msg:
                            tamaño = ruta_archivo.stat().st_size
                            if tamaño < 100:
                                print(f"[NO] (PDF muy pequeño: {tamaño} bytes)")
                                continue
                            else:
                                print(f"[WARN] (PDF con daño menor, intentando usar...)", end=" ", flush=True)
                                num_pages = "?"
                        else:
                            print(f"[NO] (PDF inválido: {str(e)[:30]})")
                            continue

                    rutas_validas.append(ruta_archivo)
                    print(f"[OK] ({num_pages} páginas)")

                except Exception as e:
                    print(f"[NO] ({str(e)[:30]})")
                    continue

            # Si no hay PDFs válidos, intentar modo alternativo (copiar único archivo)
            if not rutas_validas:
                print("\n[WARN] Modo alternativo: PDFs dañados, copiando como está...")
                if len(archivos_ordenados) == 1:
                    return self._copiar_unico(archivos_ordenados[0]["path"], numero_expediente)
                print("   [NO] Múltiples archivos dañados, no se pueden unificar")
                return None

            # ─── PASO 2: Unificar en LOTES para no llenar la memoria ───
            #
            # Si hay 50 PDFs y LOTE_UNIFICACION=10:
            #   Ronda 1: une PDFs 1-10 → _lote_0.pdf
            #   Ronda 2: une PDFs 11-20 → _lote_1.pdf
            #   ...
            #   Ronda final: une _lote_0..4.pdf → PDF final
            #
            # Esto limita la RAM a ~10 PDFs simultáneos en vez de 50.
            numero_sanitizado = (
                str(numero_expediente).replace("/", "_").replace("\\", "_").replace(":", "_")
            )
            nombre_salida = f"Expediente_{numero_sanitizado}_UNIFICADO.pdf"
            ruta_salida = self.carpeta_salida / nombre_salida

            if len(rutas_validas) <= LOTE_UNIFICACION:
                # Pocos archivos → merge directo (sin intermedios)
                print(f"\n    Unificando {len(rutas_validas)} PDFs directamente...")
                self._merge_lista(rutas_validas, ruta_salida)
            else:
                # Muchos archivos → merge por lotes
                print(f"\n    Unificando {len(rutas_validas)} PDFs en lotes de {LOTE_UNIFICACION}...")
                archivos_lote = []
                for idx_lote in range(0, len(rutas_validas), LOTE_UNIFICACION):
                    lote = rutas_validas[idx_lote:idx_lote + LOTE_UNIFICACION]
                    ruta_lote = self.carpeta_temp / f"_lote_{idx_lote // LOTE_UNIFICACION}.pdf"
                    print(f"      Lote {idx_lote // LOTE_UNIFICACION + 1}: {len(lote)} archivos...", end=" ", flush=True)
                    self._merge_lista(lote, ruta_lote)
                    archivos_lote.append(ruta_lote)
                    print("[OK]")
                    gc.collect()  # Forzar liberación de memoria entre lotes

                # Merge final de los archivos intermedios
                print(f"      Merge final: {len(archivos_lote)} lotes...", end=" ", flush=True)
                self._merge_lista(archivos_lote, ruta_salida)
                print("[OK]")

                # Limpiar archivos intermedios de lotes
                for ruta_lote in archivos_lote:
                    try:
                        ruta_lote.unlink()
                    except Exception:
                        pass

            # Verificar resultado
            if not ruta_salida.exists():
                print("   [NO] PDF final no fue generado")
                return None

            tamaño = ruta_salida.stat().st_size
            tamaño_mb = tamaño / (1024 * 1024)

            print(f"    PDF unificado creado exitosamente")
            print(f"      Archivos unidos: {len(rutas_validas)}/{len(archivos_descargados)}")
            print(f"      Tamaño: {tamaño_mb:.2f} MB")
            print(f"      Ubicación: {ruta_salida}\n")

            return ruta_salida

        except ErrorUnificacion:
            raise
        except Exception as e:
            print(f"\n[NO] Error unificando PDFs: {e}")
            return None

    def _merge_lista(self, rutas: List[Path], salida: Path) -> None:
        """
        Une una lista de PDFs en un solo archivo.
        Método auxiliar usado tanto para lotes como para merge directo.

        Args:
            rutas: Lista de Path a PDFs válidos
            salida: Ruta del archivo de salida
        """
        merger = PdfMerger()
        try:
            for ruta in rutas:
                merger.append(str(ruta))
            with open(salida, "wb") as f:
                merger.write(f)
        finally:
            merger.close()

    def _copiar_unico(self, ruta_archivo: Path, numero_expediente: str) -> Optional[Path]:
        """
        Copia un solo archivo como PDF final (fallback cuando hay un único archivo).

        Args:
            ruta_archivo: Ruta del archivo a copiar
            numero_expediente: Número de expediente para el nombre

        Returns:
            Path del PDF copiado, o None si falla
        """
        import shutil
        try:
            numero_sanitizado = (
                str(numero_expediente).replace("/", "_").replace("\\", "_").replace(":", "_")
            )
            nombre_salida = f"Expediente_{numero_sanitizado}_UNIFICADO.pdf"
            ruta_salida = self.carpeta_salida / nombre_salida
            shutil.copy2(str(ruta_archivo), str(ruta_salida))
            tamaño_mb = ruta_salida.stat().st_size / (1024 * 1024)
            print(f"    PDF creado (modo copia): {tamaño_mb:.2f} MB")
            return ruta_salida
        except Exception as e:
            print(f"   [NO] Error copiando: {e}")
            return None

    def limpiar_temporales(self, mantener_originales: bool = False) -> int:
        """
        Limpia los archivos temporales descargados.

        Args:
            mantener_originales: Si True, mantiene los archivos originales

        Retorna:
            int: Cantidad de archivos eliminados

        Raises:
            ErrorUnificacion: Si ocurre un error crítico durante la limpieza
        """
        if mantener_originales:
            logger.debug("Limpieza de temporales deshabilitada (mantener_originales=True)")
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


def crear_unificador(carpeta_temp: Path, carpeta_salida: Optional[Path] = None) -> UnificadorPDF:
    """
    Función auxiliar para crear un unificador preconfigurado.

    Args:
        carpeta_temp: Carpeta con archivos descargados
        carpeta_salida: Carpeta donde guardar el PDF unificado

    Retorna:
        UnificadorPDF: Unificador listo para usar
    """
    logger.debug(
        f"Creando unificador con carpeta_temp={carpeta_temp}, carpeta_salida={carpeta_salida}"
    )
    return UnificadorPDF(carpeta_temp, carpeta_salida)
