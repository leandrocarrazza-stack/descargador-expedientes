#!/usr/bin/env python3
"""Convierte PDFs de expedientes a Markdown localmente, sin usar ningun LLM.

Uso:
    python pdf2md.py                      # convierte todos los PDF pendientes de pdf/ a md/
    python pdf2md.py "ruta\\a\\uno.pdf"     # convierte uno solo
    python pdf2md.py --resumen            # solo reporta que hay pendiente, no convierte
    python pdf2md.py --forzar             # regenera aunque el MD ya exista
    python pdf2md.py --ocr auto|forzar|nunca      # default: auto
    python pdf2md.py --importar "C:\\...\\descargador_expedientes"   # migra PDFs viejos a pdf/
    python pdf2md.py --expedientes "C:\\ruta\\Expedientes"           # override de carpeta base
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import pypdfium2 as pdfium
except ImportError:
    print("Falta pypdfium2. Instalar con: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

DEFAULT_EXPEDIENTES_DIR = r"C:\Users\leand\OneDrive\Documentos\Expedientes"
MIN_CHARS_TEXTO_UTIL = 20
OCR_DPI = 300
UMBRAL_ENCABEZADO_REPETIDO = 0.6

PALABRAS_CLAVE_PIEZA = [
    "DEMANDA", "CONTESTA", "CONTESTACION", "CEDULA", "OFICIO", "SENTENCIA",
    "RESOLUCION", "PROVEIDO", "AUDIENCIA", "PERICIA", "RECURSO", "APELACION",
    "NOTIFICACION", "ESCRITO", "DICTAMEN", "ACUERDO",
]
RE_FECHA = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")


def log(msg):
    print(msg, flush=True)


def sha256_de_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def leer_hash_frontmatter(md_path: Path) -> str | None:
    if not md_path.exists():
        return None
    try:
        texto = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r'^sha256_origen:\s*"?([0-9a-f]{64})"?', texto, re.MULTILINE)
    return m.group(1) if m else None


def extraer_texto_pagina(pdf, indice: int) -> str:
    pagina = pdf[indice]
    textpage = pagina.get_textpage()
    texto = textpage.get_text_range()
    textpage.close()
    return texto or ""


def ocr_pagina(pdf, indice: int, lang="spa") -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return "[OCR no disponible: falta pytesseract/Pillow]"
    pagina = pdf[indice]
    escala = OCR_DPI / 72
    bitmap = pagina.render(scale=escala)
    pil_image = bitmap.to_pil()
    try:
        return pytesseract.image_to_string(pil_image, lang=lang)
    except Exception as e:
        return f"[Error OCR: {e}]"


def limpiar_texto(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    # pypdfium2 marca los guiones de corte de linea con U+FFFE en vez de "-\n"
    texto = re.sub(r"(\w)￾(\w)", r"\1\2", texto)
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def quitar_encabezados_repetidos(paginas: list[str]) -> list[str]:
    if len(paginas) < 3:
        return paginas
    conteo: dict[str, int] = {}
    for texto in paginas:
        lineas = {l.strip() for l in texto.splitlines() if l.strip()}
        for l in lineas:
            conteo[l] = conteo.get(l, 0) + 1
    umbral = max(2, int(len(paginas) * UMBRAL_ENCABEZADO_REPETIDO))
    repetidas = {l for l, c in conteo.items() if c >= umbral}
    if not repetidas:
        return paginas
    limpias = []
    for texto in paginas:
        lineas = [l for l in texto.splitlines() if l.strip() not in repetidas]
        limpias.append("\n".join(lineas))
    return limpias


def detectar_pieza(texto_pagina: str) -> tuple[str, str] | None:
    """Busca una linea-titulo (toda en mayusculas, corta) con una palabra clave.

    No alcanza con que la palabra clave aparezca en cualquier parte de la pagina:
    el cuerpo del texto suele mencionar "demanda", "notificacion", etc. de forma
    incidental. Solo cuenta si la propia linea esta en mayusculas (un titulo real).
    """
    lineas = [l.strip() for l in texto_pagina.splitlines() if l.strip()][:8]
    if not lineas:
        return None
    titulo = None
    for l in lineas:
        if len(l) > 60 or l != l.upper() or not re.search(r"[A-ZÁÉÍÓÚÑ]", l):
            continue
        if any(p in l for p in PALABRAS_CLAVE_PIEZA):
            titulo = l
            break
    if not titulo:
        return None
    bloque = " ".join(lineas)
    m = RE_FECHA.search(bloque)
    fecha = m.group(1) if m else ""
    return fecha, titulo[:80]


def convertir_pdf(pdf_path: Path, md_path: Path, modo_ocr: str) -> dict:
    pdf = pdfium.PdfDocument(str(pdf_path))
    n_paginas = len(pdf)
    textos_pagina = []
    ocr_usado = []

    for i in range(n_paginas):
        texto = extraer_texto_pagina(pdf, i)
        es_ocr = False
        if modo_ocr == "forzar" or (modo_ocr == "auto" and len(texto.strip()) < MIN_CHARS_TEXTO_UTIL):
            if modo_ocr != "nunca":
                texto_ocr = ocr_pagina(pdf, i)
                if len(texto_ocr.strip()) > len(texto.strip()):
                    texto = texto_ocr
                    es_ocr = True
        textos_pagina.append(limpiar_texto(texto))
        ocr_usado.append(es_ocr)

    textos_pagina = quitar_encabezados_repetidos(textos_pagina)

    piezas = []
    for i, texto in enumerate(textos_pagina):
        resultado = detectar_pieza(texto)
        if resultado:
            fecha, titulo = resultado
            piezas.append((len(piezas) + 1, i + 1, fecha, titulo))

    nombre_expediente = re.sub(
        r"^Expediente_|_COMPLETO$|_UNIFICADO$", "", pdf_path.stem, flags=re.IGNORECASE
    )
    sha = sha256_de_archivo(pdf_path)
    n_ocr = sum(ocr_usado)

    lineas_md = []
    lineas_md.append("---")
    lineas_md.append(f'expediente: "{nombre_expediente}"')
    lineas_md.append(f'pdf_origen: "pdf/{pdf_path.name}"')
    lineas_md.append(f'sha256_origen: "{sha}"')
    lineas_md.append(f"paginas: {n_paginas}")
    lineas_md.append(f"paginas_ocr: {n_ocr}")
    lineas_md.append(f"generado: {datetime.now().isoformat(timespec='seconds')}")
    lineas_md.append("---")
    lineas_md.append("")
    lineas_md.append(f"# Expediente {nombre_expediente}")
    lineas_md.append("")

    if piezas:
        lineas_md.append("## Indice de piezas")
        lineas_md.append("")
        lineas_md.append("| # | Pag. | Fecha | Pieza |")
        lineas_md.append("|---|------|-------|-------|")
        for num, pag, fecha, titulo in piezas:
            lineas_md.append(f"| {num} | {pag} | {fecha} | {titulo} |")
        lineas_md.append("")

    lineas_md.append("---")
    lineas_md.append("")

    for i, texto in enumerate(textos_pagina):
        marcador = f"<!-- p.{i + 1}{' ocr' if ocr_usado[i] else ''} -->"
        lineas_md.append(marcador)
        lineas_md.append(texto if texto.strip() else "*(pagina sin texto)*")
        lineas_md.append("")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lineas_md), encoding="utf-8")
    pdf.close()

    return {
        "expediente": nombre_expediente,
        "paginas": n_paginas,
        "paginas_ocr": n_ocr,
        "piezas": len(piezas),
        "md_path": md_path,
    }


def actualizar_indice_global(dir_md: Path):
    indice_path = dir_md / "_indice.md"
    filas = []
    for md_file in sorted(dir_md.glob("*.md")):
        if md_file.name == "_indice.md":
            continue
        texto = md_file.read_text(encoding="utf-8", errors="ignore")
        m_exp = re.search(r'^expediente:\s*"(.+?)"', texto, re.MULTILINE)
        m_pag = re.search(r"^paginas:\s*(\d+)", texto, re.MULTILINE)
        m_ocr = re.search(r"^paginas_ocr:\s*(\d+)", texto, re.MULTILINE)
        m_gen = re.search(r"^generado:\s*(.+)$", texto, re.MULTILINE)
        tam_kb = max(1, md_file.stat().st_size // 1024)
        filas.append((
            m_exp.group(1) if m_exp else md_file.stem,
            m_pag.group(1) if m_pag else "?",
            m_ocr.group(1) if m_ocr else "0",
            m_gen.group(1) if m_gen else "",
            tam_kb,
            md_file.name,
        ))

    lineas = ["# Indice de expedientes", "", f"Actualizado: {datetime.now().isoformat(timespec='seconds')}", ""]
    lineas.append("| Expediente | Paginas | Pag. OCR | Generado | Tamano | Archivo |")
    lineas.append("|---|---|---|---|---|---|")
    for exp, pag, ocr, gen, kb, nombre in filas:
        lineas.append(f"| {exp} | {pag} | {ocr} | {gen} | {kb} KB | {nombre} |")
    indice_path.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def importar_pdfs_viejos(origen: Path, dir_pdf: Path):
    if not origen.exists():
        log(f"[importar] No existe la carpeta origen: {origen}")
        return
    encontrados = list(origen.glob("Expediente_*_COMPLETO.pdf")) + list(origen.glob("Expediente_*_UNIFICADO*.pdf"))
    if not encontrados:
        log(f"[importar] No se encontraron PDFs de expedientes en {origen}")
        return
    dir_pdf.mkdir(parents=True, exist_ok=True)
    for pdf_file in encontrados:
        destino = dir_pdf / pdf_file.name
        if destino.exists():
            log(f"[importar] Ya existe, se omite: {pdf_file.name}")
            continue
        shutil.copy2(pdf_file, destino)
        log(f"[importar] Copiado: {pdf_file.name}")


def main():
    ap = argparse.ArgumentParser(description="Convierte PDFs de expedientes a Markdown, sin tokens.")
    ap.add_argument("pdf", nargs="?", help="Ruta a un PDF puntual (opcional).")
    ap.add_argument("--expedientes", default=os.environ.get("EXPEDIENTES_DIR", DEFAULT_EXPEDIENTES_DIR))
    ap.add_argument("--resumen", action="store_true", help="Solo reporta que hay pendiente.")
    ap.add_argument("--forzar", action="store_true", help="Regenera aunque el MD ya exista.")
    ap.add_argument("--ocr", choices=["auto", "forzar", "nunca"], default="auto")
    ap.add_argument("--importar", metavar="CARPETA", help="Migra PDFs viejos a pdf/ antes de convertir.")
    args = ap.parse_args()

    base = Path(args.expedientes)
    dir_pdf = base / "pdf"
    dir_md = base / "md"
    dir_pdf.mkdir(parents=True, exist_ok=True)
    dir_md.mkdir(parents=True, exist_ok=True)

    if args.importar:
        importar_pdfs_viejos(Path(args.importar), dir_pdf)

    if args.pdf:
        pdfs = [Path(args.pdf)]
    else:
        pdfs = sorted(dir_pdf.glob("*.pdf"))

    if not pdfs:
        log("No hay PDFs para procesar.")
        return

    pendientes = []
    for pdf_path in pdfs:
        if not pdf_path.exists():
            log(f"[ERROR] No existe: {pdf_path}")
            continue
        md_path = dir_md / (pdf_path.stem + ".md")
        hash_actual = sha256_de_archivo(pdf_path)
        hash_previo = leer_hash_frontmatter(md_path)
        if args.forzar or hash_actual != hash_previo:
            pendientes.append(pdf_path)

    if args.resumen:
        log(f"Pendientes de convertir: {len(pendientes)} de {len(pdfs)} PDF(s) totales.")
        for p in pendientes:
            log(f"  - {p.name}")
        return

    if not pendientes:
        log(f"Nada para convertir. {len(pdfs)} PDF(s) ya estaban al dia.")
        actualizar_indice_global(dir_md)
        return

    for pdf_path in pendientes:
        md_path = dir_md / (pdf_path.stem + ".md")
        log(f"[convirtiendo] {pdf_path.name} ...")
        try:
            resultado = convertir_pdf(pdf_path, md_path, args.ocr)
            log(
                f"[OK] {resultado['expediente']}: {resultado['paginas']} paginas "
                f"({resultado['paginas_ocr']} con OCR), {resultado['piezas']} piezas detectadas "
                f"-> {md_path}"
            )
        except Exception as e:
            log(f"[ERROR] {pdf_path.name}: {e}")

    actualizar_indice_global(dir_md)
    log(f"Listo. {len(pendientes)} expediente(s) convertido(s). Indice actualizado en {dir_md / '_indice.md'}")


if __name__ == "__main__":
    main()
