# expediente-a-md

Convierte PDFs de expedientes judiciales a Markdown en la máquina local, sin usar ningún LLM en el
proceso (0 tokens). Pensado para trabajar junto al skill `descargar-expediente`.

## Instalación (Windows)

```powershell
cd C:\Users\leand\OneDrive\Documentos\Proyectos\expediente-a-md
pip install -r requirements.txt
```

Para OCR también hace falta **Tesseract** instalado y en el PATH:
https://github.com/UB-Mannheim/tesseract/wiki (instalar el paquete de idioma español, `spa`).

## Carpeta de datos

Por defecto usa `C:\Users\leand\OneDrive\Documentos\Expedientes\`, con dos subcarpetas:

- `pdf\` — PDFs originales de los expedientes.
- `md\` — versión Markdown de cada expediente, más `_indice.md` con el catálogo.

Se puede cambiar con `--expedientes "otra\ruta"` o la variable de entorno `EXPEDIENTES_DIR`.

## Uso

```powershell
python pdf2md.py                      # convierte todos los PDF pendientes de pdf\ a md\
python pdf2md.py "ruta\a\uno.pdf"     # convierte uno solo
python pdf2md.py --resumen            # solo reporta que hay pendiente, no convierte
python pdf2md.py --forzar             # regenera aunque el MD ya exista
python pdf2md.py --ocr auto|forzar|nunca      # default: auto
python pdf2md.py --importar "C:\Users\leand\OneDrive\Documentos\Proyectos\descargador_expedientes"
```

`--importar` copia los `Expediente_*_COMPLETO.pdf` / `..._UNIFICADO*.pdf` de la carpeta vieja del
descargador a `pdf\`, sin duplicar los que ya estén.

La reconversión es idempotente: si el PDF no cambió (mismo hash), no se vuelve a procesar.

Ver `SKILL.md` para las reglas de uso pensadas para Claude (evitar leer el PDF directamente).
