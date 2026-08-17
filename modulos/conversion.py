"""
MÓDULO: Conversión de RTF a PDF
================================

Convierte archivos RTF a PDF para unificarlos con otros PDFs.
Utiliza LibreOffice como herramienta principal de conversión.
"""

from pathlib import Path
from typing import Dict, List, Optional
import functools
import subprocess
import os
import signal
import time
import shutil

from modulos.logger import crear_logger
from modulos.excepciones import ErrorConversion

logger = crear_logger(__name__)

# Cantidad de RTF por invocación de soffice en convertir_lote(). Más alto =
# menos arranques en frío de LibreOffice (~1.5-3s + 1s de sleep cada uno,
# ver convertir_rtf_a_pdf), pero un timeout del lote entero se paga con
# TODOS esos archivos si soffice se cuelga. 8 es un balance razonable para
# servidores con RAM limitada.
LOTE_CONVERSION = int(os.environ.get('LOTE_CONVERSION', '8'))


def memoria_disponible_mb():
    """
    Memoria disponible para ESTE contenedor, en MB. None si no se pudo leer.

    Dentro de Docker (como en Render), /proc/meminfo reporta la RAM del
    HOST completo, no el límite real del contenedor: el kernel no
    restringe ese archivo por namespace. Por eso los logs de [MEMORIA]
    mostraban valores como "13570 MB disponibles" en un plan de 512 MB —
    ese número nunca reflejó cuánta memoria le quedaba realmente al
    proceso antes de un OOM-kill. El límite que el kernel sí hace cumplir
    está en el cgroup del contenedor, así que se lee de ahí (v2 primero,
    v1 como fallback) y sólo se usa /proc/meminfo si no hay cgroup
    (ej. corriendo fuera de Docker).
    """
    try:
        with open('/sys/fs/cgroup/memory.max') as f:
            limite_raw = f.read().strip()
        if limite_raw != 'max':
            with open('/sys/fs/cgroup/memory.current') as f:
                uso = int(f.read().strip())
            return (int(limite_raw) - uso) // (1024 * 1024)
    except Exception:
        pass

    try:
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes') as f:
            limite = int(f.read().strip())
        if limite < (1 << 62):  # sin límite real, cgroup v1 reporta un número gigante
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes') as f:
                uso = int(f.read().strip())
            return (limite - uso) // (1024 * 1024)
    except Exception:
        pass

    try:
        with open('/proc/meminfo') as f:
            for linea in f:
                if linea.startswith('MemAvailable:'):
                    return int(linea.split()[1]) // 1024
    except Exception:
        pass

    return None


def matar_procesos_soffice():
    """
    Mata cualquier proceso "soffice"/"soffice.bin" residual del sistema.

    LibreOffice, al usarse en modo `--headless --convert-to`, suele dejar un
    proceso soffice.bin corriendo en segundo plano como optimización para
    acelerar conversiones futuras (evita reiniciar todo el entorno de LO en
    cada invocación). En un servidor con 512 MB totales compartidos con
    Chrome, ese proceso residente (100-300 MB, según versión) compite por
    memoria incluso mucho después de haber terminado de convertir algo —
    nada en el código lo cerraba explícitamente hasta ahora.

    No usa psutil ni pkill (ninguno de los dos está instalado en la imagen):
    lee /proc directamente, que es información estándar de cualquier Linux.
    """
    eliminados = 0
    try:
        for entrada in os.listdir('/proc'):
            if not entrada.isdigit():
                continue
            pid = int(entrada)
            try:
                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                    argv = f.read().decode('utf-8', errors='ignore').split('\x00')
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue

            # Comparar el EJECUTABLE exacto (primer argumento, sin path), no una
            # búsqueda de substring en cualquier parte del cmdline: un substring
            # laxo puede matchear por accidente cualquier proceso que mencione la
            # palabra "soffice" en un argumento (ej. una ruta), y aquí el objetivo
            # es sólo el proceso real de LibreOffice.
            ejecutable = os.path.basename(argv[0]) if argv and argv[0] else ''
            if ejecutable not in ('soffice', 'soffice.bin'):
                continue

            try:
                os.kill(pid, signal.SIGKILL)
                eliminados += 1
            except (ProcessLookupError, PermissionError):
                pass

        if eliminados:
            logger.info(f"[MEMORIA] {eliminados} proceso(s) soffice residual(es) eliminado(s)")
    except Exception as e:
        logger.warning(f"[MEMORIA] Error limpiando procesos soffice: {str(e)[:60]}")


def parece_pdf(ruta: Path) -> bool:
    """
    True si el archivo ya es un PDF, por extensión o por magic bytes.

    Función compartida (antes vivía inline y duplicada) entre
    ConversorRTF.convertir_rtf_a_pdf() -el camino barato para archivos que
    ya son PDF, sin pasar por soffice- y pipeline.py, que la usa para
    partir los archivos descargados entre "ya PDF" (van uno por uno, es
    prácticamente gratis) y "RTF real" (van agrupados a convertir_lote()).
    """
    ruta = Path(ruta)
    if ruta.suffix.lower() == '.pdf':
        return True
    try:
        with open(ruta, 'rb') as f:
            return f.read(4).startswith(b'%PDF')
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def detectar_libreoffice() -> Optional[str]:
    """
    Detecta la ruta de LibreOffice en el sistema. Cacheado con lru_cache:
    antes cada ConversorRTF() nuevo repetía este shell-out (`which soffice`
    + `soffice --version`) desde cero, y se construyen 2 conversores por
    job (uno en pipeline.py, otro en unificacion.py) — un chequeo que da
    el mismo resultado durante toda la vida del proceso, no hace falta
    repetirlo.

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


class ConversorRTF:
    """Conversor de archivos RTF a PDF con soporte para LibreOffice."""

    def __init__(self, perfil_dir: Optional[Path] = None) -> None:
        """
        Inicializa el conversor y detecta LibreOffice.

        Args:
            perfil_dir: si se pasa, cada invocación de soffice usa este
                directorio como su propio perfil de usuario
                (-env:UserInstallation) en vez del perfil default
                compartido de LibreOffice. No hace falta con el permiso de
                conversión exclusivo (cap 1, ver modulos/concurrencia.py)
                que ya impide que dos soffice corran a la vez — es
                cinturón y tiradores por si ese cap alguna vez sube.
        """
        self.libreoffice_path = detectar_libreoffice()
        self.disponible = self.libreoffice_path is not None
        self.perfil_dir = Path(perfil_dir) if perfil_dir else None

        if self.disponible:
            logger.info(f"LibreOffice detectado en: {self.libreoffice_path}")
        else:
            logger.warning("LibreOffice no detectado en el sistema")

    def convertir_rtf_a_pdf(
        self, ruta_rtf: Path, ruta_pdf: Optional[Path] = None
    ) -> Optional[Path]:
        """
        Convierte un archivo RTF a PDF, o copia si ya es PDF.

        Args:
            ruta_rtf: Path del archivo RTF o PDF
            ruta_pdf: Path del archivo PDF de salida (opcional)

        Retorna:
            Optional[Path]: Ruta del archivo PDF generado, o None si falla

        Lanza:
            ErrorConversion: Si la conversión falla debido a problemas de LibreOffice
        """
        ruta_rtf = Path(ruta_rtf)

        # Validar que existe el archivo
        if not ruta_rtf.exists():
            print(f"      [WARN]  Archivo no existe: {ruta_rtf}")
            return None

        # Generar nombre de salida si no se proporciona
        if ruta_pdf is None:
            ruta_pdf = ruta_rtf.with_suffix('.pdf')
        else:
            ruta_pdf = Path(ruta_pdf)

        if parece_pdf(ruta_rtf):
            # Ya es PDF. Si nadie pasó un ruta_pdf explícito (el caso normal:
            # pipeline.py llama convertir_rtf_a_pdf(ruta) a secas), ruta_pdf
            # se calculó arriba como ruta_rtf.with_suffix('.pdf') — que para
            # un archivo que YA termina en .pdf es el mismo path. No hay nada
            # que copiar ni mover: el archivo ya está donde tiene que estar.
            #
            # Sin este chequeo, shutil.copy(x, x) fallaba con SameFileError
            # en el 100% de estos casos (confirmado en logs de producción:
            # nunca apareció el mensaje "(ya es PDF)" sin "movido" atrás), y
            # el código sólo "andaba" de rebote gracias al except de abajo,
            # que termina haciendo un rename(x, x) — un no-op que no aportaba
            # nada salvo una excepción de más por archivo.
            if ruta_pdf == ruta_rtf:
                print(f"      [OK] {ruta_rtf.name} (ya es PDF)")
                return ruta_rtf

            # No hay que esperar a que "se libere" antes de copiar a un
            # destino distinto: para cuando esto corre (PASO 4 del pipeline),
            # el archivo ya pasó por _esperar_archivo_nuevo() en descarga.py,
            # que sólo lo devuelve cuando su tamaño se mantuvo estable entre
            # dos lecturas — y Chrome ya está cerrado (pipeline.py lo cierra
            # antes de PASO 4). No queda ningún escritor concurrente al que
            # esperar.
            try:
                ruta_pdf.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ruta_rtf, ruta_pdf)
                print(f"      [OK] {ruta_rtf.name} (ya es PDF)")
                return ruta_pdf
            except Exception as e:
                # Si falla el copy, intentar mover
                try:
                    ruta_pdf.parent.mkdir(parents=True, exist_ok=True)
                    ruta_rtf.rename(ruta_pdf)
                    print(f"      [OK] {ruta_rtf.name} (ya es PDF, movido)")
                    return ruta_pdf
                except:
                    print(f"      [WARN]  Error procesando PDF: {str(e)[:30]}")
                    return None

        # Validar que es RTF: primero por extensión, luego por magic bytes
        ruta_normalizada = self._normalizar_rtf(ruta_rtf)
        if ruta_normalizada is None:
            print(f"      [WARN]  No es archivo RTF: {ruta_rtf.name}")
            return None
        ruta_rtf = ruta_normalizada

        # Verificar si LibreOffice está disponible
        if not self.disponible:
            print(f"      [WARN]  LibreOffice no está instalado")
            print(f"         Descarga desde: https://www.libreoffice.org/download/")
            return None

        try:
            # Convertir con LibreOffice
            if self._convertir_con_libreoffice(ruta_rtf, ruta_pdf):
                print(f"      [OK] {ruta_rtf.name} > {ruta_pdf.name}")
                return ruta_pdf
            else:
                print(f"      [WARN]  No se pudo convertir: {ruta_rtf.name}")
                return None

        except ErrorConversion:
            raise
        except Exception as e:
            print(f"      [NO] Error: {str(e)[:40]}")
            return None

    def _normalizar_rtf(self, ruta_rtf: Path) -> Optional[Path]:
        """
        Confirma que el archivo es un RTF real (por extensión o, si Mesa
        Virtual lo entregó sin extensión, por magic bytes) y le agrega
        ".rtf" si hacía falta. Devuelve la ruta ya normalizada, o None si
        no es RTF.

        Factorizado de convertir_rtf_a_pdf() para poder correr ANTES de
        agrupar varios archivos en una sola invocación de soffice
        (convertir_lote): soffice necesita la extensión correcta en CADA
        archivo del lote desde el principio, no se puede corregir a mitad
        de una conversión conjunta.
        """
        if ruta_rtf.suffix.lower() == ".rtf":
            return ruta_rtf

        try:
            with open(ruta_rtf, "rb") as f:
                magic = f.read(6)
            if magic.startswith(b"{\\rtf"):
                ruta_renombrada = ruta_rtf.with_suffix(".rtf")
                ruta_rtf.rename(ruta_renombrada)
                print(f"      [INFO]  Renombrado a .rtf por contenido RTF detectado")
                return ruta_renombrada
        except Exception:
            pass

        return None

    def _armar_comando_soffice(self, outdir: Path, *entradas: Path) -> List[str]:
        """Arma el comando de soffice compartido entre conversión individual y por lote."""
        comando = [self.libreoffice_path, "--headless"]
        if self.perfil_dir:
            self.perfil_dir.mkdir(parents=True, exist_ok=True)
            # Perfil de usuario propio: evita que dos invocaciones de soffice
            # se pisen el lock del perfil default si algún día corren en
            # paralelo (hoy no pasa: el permiso de conversión es exclusivo).
            comando.append(f"-env:UserInstallation=file://{self.perfil_dir}")
        comando += ["--convert-to", "pdf", "--outdir", str(outdir)]
        comando += [str(e) for e in entradas]
        return comando

    def convertir_lote(self, rutas_rtf: List[Path], outdir: Path) -> Dict[Path, Optional[Path]]:
        """
        Convierte varios RTF a PDF con UNA sola invocación de soffice, en
        vez de un subprocess.run + sleep(1) por archivo (~1.5-3s de
        arranque en frío cada uno). En un expediente con muchos RTF esto
        ahorra minutos: el costo fijo de arrancar LibreOffice se paga una
        vez por lote, no una vez por archivo.

        Los archivos que ya son PDF NO deben venir acá — filtralos antes
        con parece_pdf()/convertir_rtf_a_pdf(): ese camino es prácticamente
        gratis y no necesita soffice. Este método sólo tiene sentido para
        RTF reales (ver el particionado en modulos/pipeline.py PASO 4).

        Si algún archivo del lote no aparece en la salida esperada (un RTF
        corrupto puede hacer que soffice se salte ESE archivo sin abortar
        el resto, o el batch entero puede fallar/expirar), se reintenta
        ESE archivo individualmente por el camino de siempre
        (convertir_rtf_a_pdf) — nunca se pierde un archivo por culpa de
        otro del mismo lote.

        Args:
            rutas_rtf: RTFs a convertir (se normalizan igual acá, así que
                no hace falta haber pasado por _normalizar_rtf antes)
            outdir: carpeta de salida para todos los PDFs del lote

        Retorna:
            dict {ruta_original: ruta_pdf_o_None}, una entrada por cada
            archivo de `rutas_rtf` (la clave es el path ORIGINAL, aunque
            _normalizar_rtf le haya cambiado la extensión por dentro).
        """
        resultado: Dict[Path, Optional[Path]] = {}
        if not rutas_rtf:
            return resultado

        if not self.disponible:
            print(f"      [WARN]  LibreOffice no está instalado")
            for ruta in rutas_rtf:
                resultado[Path(ruta)] = None
            return resultado

        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        # Normalizar ANTES del batch: soffice necesita la extensión
        # correcta en cada archivo de entrada desde el arranque.
        normalizados: Dict[Path, Path] = {}  # original -> normalizado
        for original in rutas_rtf:
            original = Path(original)
            normalizado = self._normalizar_rtf(original)
            if normalizado is None:
                print(f"      [WARN]  No es archivo RTF: {original.name}")
                resultado[original] = None
                continue
            normalizados[original] = normalizado

        if not normalizados:
            return resultado

        comando = self._armar_comando_soffice(outdir, *normalizados.values())
        timeout = min(60 + 20 * len(normalizados), 300)

        logger.debug(f"Ejecutando comando LibreOffice (lote de {len(normalizados)}): {' '.join(comando)}")
        try:
            subprocess.run(comando, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # subprocess.run ya mata al proceso hijo al vencer el timeout.
            # Lo que haya alcanzado a escribirse se valida igual abajo; lo
            # que falte se reintenta uno por uno más adelante.
            logger.warning(f"Timeout en conversión por lote (>{timeout}s, {len(normalizados)} archivos)")
        except Exception as e:
            logger.warning(f"Error ejecutando soffice en lote: {str(e)[:60]}")

        time.sleep(1)  # mismo margen que el camino individual: última escritura a disco

        faltantes: List[Path] = []
        for original, normalizado in normalizados.items():
            ruta_pdf_esperada = outdir / normalizado.with_suffix('.pdf').name
            valido = False
            if ruta_pdf_esperada.exists() and ruta_pdf_esperada.stat().st_size >= 500:
                try:
                    with open(ruta_pdf_esperada, 'rb') as f:
                        valido = f.read(4) == b'%PDF'
                except Exception:
                    valido = False

            if valido:
                resultado[original] = ruta_pdf_esperada
                print(f"      [OK] {normalizado.name} > {ruta_pdf_esperada.name}")
            else:
                faltantes.append(original)

        if faltantes:
            print(f"      [INFO]  {len(faltantes)}/{len(normalizados)} archivo(s) del lote no "
                  f"salieron bien, reintentando uno por uno...")
            for original in faltantes:
                normalizado = normalizados[original]
                destino = outdir / normalizado.with_suffix('.pdf').name
                resultado[original] = self.convertir_rtf_a_pdf(normalizado, destino)

        return resultado

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
            comando = self._armar_comando_soffice(ruta_pdf.parent, ruta_rtf)

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
                print(f"         [WARN]  PDF muy pequeño ({tamaño} bytes)")
                return False

            logger.debug(f"Tamaño del PDF generado: {tamaño} bytes")

            # Validar que es un PDF válido
            try:
                with open(ruta_pdf, "rb") as f:
                    header = f.read(4)
                    if header != b'%PDF':
                        print(f"         [WARN]  Archivo no es PDF válido")
                        return False
            except Exception as e:
                logger.warning(f"Error validando header PDF: {str(e)[:50]}")
                return False

            return True

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout en conversión LibreOffice (>60 segundos)")
            return False
        except Exception as e:
            print(f"         [NO] {str(e)[:40]}")
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
