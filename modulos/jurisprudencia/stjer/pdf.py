"""
Descarga de PDFs de fallos
==========================

El endpoint de PDFs es **estatico y publico**: no pide sesion ni captcha.
Eso lo vuelve el camino mas robusto de todo el sistema — sobrevive incluso a
que nos bloqueen el buscador.

    https://jur.jusentrerios.gov.ar/jur/dossier/bCARATULA_b__<NOMBRE>.PDF

Por defecto la descarga es **perezosa**: se baja el PDF del fallo que se va a
leer, y nada mas. Bajar los ~14.800 son ~3,7 GB y varias horas para material
que casi nunca se abre; lo que se cita son los sumarios, y esos ya estan en
el corpus. `descargar_faltantes()` esta para el que quiera el archivo offline
completo.
"""

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import ajustes, corpus
from .normalizacion import normalizar_texto

logger = logging.getLogger(__name__)

MAGIC_PDF = b"%PDF-"


class ErrorPDF(Exception):
    pass


@dataclass
class ResultadoPDF:
    clave: str = ""
    ruta: Path = None
    bytes: int = 0
    sha256: str = ""
    estado: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.ruta is not None and not self.error


def url_desde_enlace(enlace: str) -> str:
    """'dossier/X.PDF' -> URL absoluta."""
    if not enlace:
        raise ErrorPDF("El fallo no tiene enlace al PDF")
    if enlace.startswith("http"):
        return enlace
    return ajustes.BASE_URL + enlace.lstrip("/")


def nombre_archivo(clave: str, caratula: str = "", fecha: str = "") -> str:
    """
    Nombre de archivo legible y seguro para el sistema de archivos.

    Se antepone la fecha para que ordenar por nombre ordene cronologicamente.
    """
    base = normalizar_texto(caratula)[:80].strip().replace(" ", "_")
    if not base:
        base = normalizar_texto(clave).replace(" ", "_")[:80]
    prefijo = (fecha or "")[:10].replace("-", "") or "sinfecha"
    # El hash de la clave evita colisiones entre caratulas iguales.
    sufijo = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:8]
    return f"{prefijo}_{base}_{sufijo}.pdf"


def descargar(url: str, destino: Path, timeout: float = None) -> ResultadoPDF:
    """
    Baja un PDF y verifica que sea un PDF de verdad.

    Escribe primero a un .parcial y recien despues renombra: asi una
    interrupcion no deja un archivo truncado que parezca valido.
    """
    import requests

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": ajustes.UA_NAVEGADOR},
            timeout=timeout or ajustes.TIMEOUT_SEG,
            stream=True,
        )
    except requests.RequestException as e:
        return ResultadoPDF(error=f"fallo de red: {e}")

    if resp.status_code != 200:
        return ResultadoPDF(estado=resp.status_code, error=f"HTTP {resp.status_code}")

    # Si devuelve HTML, es una pagina de error disfrazada de 200.
    tipo = (resp.headers.get("Content-Type") or "").lower()
    if "html" in tipo:
        return ResultadoPDF(
            estado=resp.status_code,
            error=f"el servidor devolvio {tipo}, no un PDF",
        )

    h = hashlib.sha256()
    total = 0
    try:
        with open(parcial, "wb") as fh:
            for trozo in resp.iter_content(chunk_size=65536):
                if not trozo:
                    continue
                if total == 0 and not trozo.startswith(MAGIC_PDF):
                    parcial.unlink(missing_ok=True)
                    return ResultadoPDF(
                        estado=resp.status_code,
                        error="el contenido no empieza con %PDF-",
                    )
                fh.write(trozo)
                h.update(trozo)
                total += len(trozo)
    except OSError as e:
        parcial.unlink(missing_ok=True)
        return ResultadoPDF(error=f"no se pudo escribir: {e}")

    if total == 0:
        parcial.unlink(missing_ok=True)
        return ResultadoPDF(estado=resp.status_code, error="archivo vacio")

    parcial.replace(destino)
    return ResultadoPDF(
        ruta=destino, bytes=total, sha256=h.hexdigest(), estado=resp.status_code
    )


def asegurar_pdf(con, identificador, forzar: bool = False) -> ResultadoPDF:
    """
    Devuelve el PDF de un fallo, bajandolo si hace falta. Descarga perezosa.

    `identificador` puede ser el id numerico o la clave natural.
    """
    columna = "id" if isinstance(identificador, int) else "clave_natural"
    fila = con.execute(
        f"SELECT id, clave_natural, caratula, fecha_fallo, enlace_pdf, pdf_ruta "
        f"FROM fallos WHERE {columna}=?",
        (identificador,),
    ).fetchone()
    if fila is None:
        return ResultadoPDF(error=f"No hay ningun fallo con {columna}={identificador!r}")

    if fila["pdf_ruta"] and not forzar:
        ruta = Path(fila["pdf_ruta"])
        if ruta.exists() and ruta.stat().st_size > 0:
            return ResultadoPDF(
                clave=fila["clave_natural"], ruta=ruta, bytes=ruta.stat().st_size
            )

    if not fila["enlace_pdf"]:
        return ResultadoPDF(
            clave=fila["clave_natural"],
            error="Este fallo todavia no tiene enlace al PDF. Cosechá su "
                  "detalle con: python -m scripts.stjer cosechar detalles",
        )

    destino = ajustes.PDFS_DIR / nombre_archivo(
        fila["clave_natural"], fila["caratula"] or "", fila["fecha_fallo"] or ""
    )
    resultado = descargar(url_desde_enlace(fila["enlace_pdf"]), destino)
    resultado.clave = fila["clave_natural"]

    if resultado.ok:
        with corpus.transaccion(con):
            con.execute(
                "UPDATE fallos SET pdf_ruta=?, pdf_bytes=?, pdf_sha256=?, "
                "actualizado_en=? WHERE id=?",
                (str(resultado.ruta), resultado.bytes, resultado.sha256,
                 corpus.ahora(), fila["id"]),
            )
    return resultado


def descargar_faltantes(
    con, limite: int = None, concurrencia: int = 4, espera: float = 0.25
) -> dict:
    """
    Baja en lote los PDFs que faltan. Es el modo archivo offline.

    Concurrencia moderada (4) porque son archivos estaticos, pero con espera
    entre lanzamientos igual: no hay razon para golpear.
    """
    sql = (
        "SELECT id, clave_natural, caratula, fecha_fallo, enlace_pdf "
        "FROM fallos WHERE enlace_pdf IS NOT NULL AND pdf_ruta IS NULL "
        "ORDER BY fecha_fallo DESC"
    )
    if limite:
        sql += f" LIMIT {int(limite)}"
    pendientes = con.execute(sql).fetchall()

    if not pendientes:
        return {"pendientes": 0, "descargados": 0, "errores": 0, "bytes": 0}

    logger.info("Descargando %d PDFs con concurrencia %d", len(pendientes), concurrencia)
    descargados = errores = total_bytes = 0
    fallidos = []

    def tarea(fila):
        destino = ajustes.PDFS_DIR / nombre_archivo(
            fila["clave_natural"], fila["caratula"] or "", fila["fecha_fallo"] or ""
        )
        r = descargar(url_desde_enlace(fila["enlace_pdf"]), destino)
        r.clave = fila["clave_natural"]
        return fila["id"], r

    with ThreadPoolExecutor(max_workers=concurrencia) as pool:
        futuros = []
        for fila in pendientes:
            futuros.append(pool.submit(tarea, fila))
            time.sleep(espera)

        for futuro in as_completed(futuros):
            try:
                fallo_id, r = futuro.result()
            except Exception as e:
                errores += 1
                fallidos.append(str(e))
                continue

            if r.ok:
                descargados += 1
                total_bytes += r.bytes
                # La escritura en SQLite se hace en el hilo principal.
                with corpus.transaccion(con):
                    con.execute(
                        "UPDATE fallos SET pdf_ruta=?, pdf_bytes=?, pdf_sha256=?, "
                        "actualizado_en=? WHERE id=?",
                        (str(r.ruta), r.bytes, r.sha256, corpus.ahora(), fallo_id),
                    )
            else:
                errores += 1
                fallidos.append(f"{r.clave}: {r.error}")

            if (descargados + errores) % 50 == 0:
                logger.info(
                    "PDFs: %d bajados, %d errores, %.1f MB",
                    descargados, errores, total_bytes / 1e6,
                )

    return {
        "pendientes": len(pendientes),
        "descargados": descargados,
        "errores": errores,
        "bytes": total_bytes,
        "fallidos": fallidos[:20],
    }


def extraer_texto(ruta) -> str:
    """
    Texto de un PDF ya bajado.

    Reutiliza `ExtractorFallos.extraer_texto_completo` de la via Gmail, que ya
    resuelve esto con pdfplumber. Se importa adentro de la funcion porque ese
    modulo arrastra Flask, y este paquete tiene que poder correr sin la app.
    """
    try:
        from modulos.jurisprudencia.pdf_extractor import ExtractorFallos
    except ImportError as e:
        raise ErrorPDF(
            f"No se pudo importar el extractor ({e}). Necesita las "
            f"dependencias de la app web (pdfplumber)."
        ) from e

    return ExtractorFallos().extraer_texto_completo(Path(ruta))
