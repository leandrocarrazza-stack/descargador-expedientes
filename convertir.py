#!/usr/bin/env python3
"""
Convierte un expediente judicial PDF a Markdown para análisis con IA.

El Markdown resultante es legible por Claude con mínimo consumo de tokens:
se puede hacer grep por secciones clave y leer solo los fragmentos relevantes,
en lugar de procesar todo el PDF página por página.

Uso:
    python convertir.py Expediente_21_24_UNIFICADO.pdf
    python convertir.py expediente.pdf --output analisis/exp.md

Requiere: pymupdf4llm  →  pip install pymupdf4llm
"""

import sys
import argparse
from pathlib import Path


def convertir(pdf_path: Path, output_path: Path) -> None:
    try:
        import pymupdf4llm
        import pymupdf
    except ImportError:
        print("Error: instalar con:  pip install pymupdf4llm")
        sys.exit(1)

    # Extraer Markdown por página para poder insertar separadores y detectar escaneadas
    chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
    total = chunks[0]["metadata"]["page_count"] if chunks else 0

    # Detectar páginas escaneadas: tienen imágenes pero texto vacío o muy corto
    doc = pymupdf.open(str(pdf_path))
    paginas_escaneadas = []
    for page in doc:
        n = page.number + 1  # 1-indexed
        texto = page.get_text("text").strip()
        imagenes = page.get_images()
        if imagenes and len(texto) < 30:
            paginas_escaneadas.append(n)
    doc.close()

    bloques = []
    bloques.append(f"# {pdf_path.stem}")
    bloques.append(f"**Archivo:** {pdf_path.name}  ")
    bloques.append(f"**Páginas totales:** {total}  ")
    if paginas_escaneadas:
        pct = len(paginas_escaneadas) / total * 100 if total else 0
        bloques.append(
            f"**Páginas escaneadas:** {len(paginas_escaneadas)} ({pct:.0f}%) "
            f"— págs. {paginas_escaneadas[:20]}{'...' if len(paginas_escaneadas) > 20 else ''}"
        )
    bloques.append("")
    bloques.append("---")
    bloques.append("")

    for chunk in chunks:
        n = chunk["metadata"]["page_number"]
        texto = chunk["text"].strip()

        bloques.append(f"## — Página {n} —")
        bloques.append("")

        if n in paginas_escaneadas:
            bloques.append(f"<!-- pág. {n}: escaneada — sin texto extraíble -->")
        elif texto:
            bloques.append(texto)
        else:
            bloques.append(f"<!-- pág. {n}: en blanco -->")

        bloques.append("")
        bloques.append("---")
        bloques.append("")

    if paginas_escaneadas:
        bloques.append(
            f"<!-- AVISO: {len(paginas_escaneadas)} páginas escaneadas: "
            f"{paginas_escaneadas[:30]}{'...' if len(paginas_escaneadas) > 30 else ''} -->"
        )

    output_path.write_text("\n".join(bloques), encoding="utf-8")

    kb_in = pdf_path.stat().st_size // 1024
    kb_out = output_path.stat().st_size // 1024
    print(f"✓ Generado: {output_path}  ({kb_out} KB desde {kb_in} KB PDF)")
    print(f"  Páginas totales  : {total}")
    if paginas_escaneadas:
        pct = len(paginas_escaneadas) / total * 100 if total else 0
        print(f"  Escaneadas       : {len(paginas_escaneadas)} ({pct:.0f}%) — revisar si son secciones clave")
    else:
        print(f"  Escaneadas       : ninguna — texto 100 % extraíble")


def main():
    parser = argparse.ArgumentParser(
        description="Convierte expediente judicial PDF → Markdown para análisis con IA"
    )
    parser.add_argument("pdf", help="Ruta al archivo PDF del expediente")
    parser.add_argument(
        "--output", "-o",
        help="Archivo de salida .md (por defecto: mismo nombre que el PDF con extensión .md)"
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: no se encuentra '{pdf_path}'")
        sys.exit(1)
    if pdf_path.suffix.lower() != ".pdf":
        print("Error: se esperaba un archivo .pdf")
        sys.exit(1)

    output_path = Path(args.output) if args.output else pdf_path.with_suffix(".md")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Convirtiendo {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)...")
    convertir(pdf_path, output_path)


if __name__ == "__main__":
    main()
