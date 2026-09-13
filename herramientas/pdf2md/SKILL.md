---
name: expediente-a-md
description: >
  Convierte PDFs de expedientes judiciales a Markdown de forma local, sin gastar tokens,
  y mantiene un indice de todos los expedientes convertidos en
  C:\Users\leand\OneDrive\Documentos\Expedientes\md\_indice.md. Usar SIEMPRE que el usuario
  quiera trabajar, leer, buscar algo, resumir o analizar el contenido de un expediente ya
  descargado en PDF (por ejemplo despues de usar el skill descargar-expediente), o pida
  "pasa este expediente a markdown", "convertime este PDF", "que dice el expediente X sobre...".
  NUNCA usar la tool Read sobre un PDF de expediente directamente: siempre convertir primero
  con este skill y trabajar sobre el .md resultante.
---

# expediente-a-md

Herramienta CLI local del usuario, ubicada en:

```
C:\Users\leand\OneDrive\Documentos\Proyectos\expediente-a-md\
```

Todos los comandos se ejecutan con `cwd` en esa carpeta. Es una herramienta local
(Windows/PowerShell): se corre a través del puente al equipo del usuario (`device_bash`), con esa
carpeta — y `C:\Users\leand\OneDrive\Documentos\Expedientes\` — conectadas. Si alguna carpeta
todavía no está conectada en la sesión, pedir acceso antes de intentar ejecutar comandos.

## Por qué existe este skill

Los expedientes pueden tener cientos de páginas. Si Claude lee el PDF directamente (tool Read,
subida de archivo, etc.), ese contenido entra al contexto de la conversación y consume una
cantidad enorme de tokens. Este skill convierte el PDF a Markdown con un script Python local
(0 tokens, corre como proceso aparte) y de ahí en más Claude trabaja **solo sobre el Markdown**,
leyendo apenas los tramos necesarios.

## Reglas obligatorias

1. **Nunca** usar la tool Read (ni pedirle al usuario que suba) un archivo de la carpeta `pdf\`.
2. Antes de responder algo sobre un expediente, verificar si ya tiene `.md` en
   `Expedientes\md\`. Si no existe o el PDF es más nuevo, convertir primero con `pdf2md.py`.
3. Para ubicar un expediente entre varios: leer primero
   `C:\Users\leand\OneDrive\Documentos\Expedientes\md\_indice.md` (es chico, lista todos).
4. Para buscar algo puntual dentro de un expediente: usar Grep sobre el `.md`, no leerlo entero.
   Los resultados de Grep traen el marcador `<!-- p.N -->` más cercano, que es la página real del
   PDF.
5. Si hace falta leer un tramo largo, usar Read con `offset`/`limit` acotado al rango de interés,
   no el archivo completo.
6. Si el pedido del usuario realmente requiere leer el expediente completo (ej. "resumime todo"),
   avisar el volumen (páginas, tamaño del `.md`) antes de hacerlo, y proponer alternativas
   (buscar por palabra clave, ir por el índice de piezas) si el archivo es muy grande.

## Flujo estándar

1. **Convertir los PDFs pendientes:**

```powershell
python pdf2md.py
```

Esto recorre `Expedientes\pdf\`, convierte los que falten o hayan cambiado, y actualiza
`Expedientes\md\_indice.md`. La salida en consola es mínima (una línea por expediente) — eso es
lo único que debe entrar al contexto.

2. **Ver qué hay sin convertir nada:**

```powershell
python pdf2md.py --resumen
```

3. **Migrar PDFs que ya bajó el skill `descargar-expediente`** (quedan en
   `...\Proyectos\descargador_expedientes\`) a la carpeta compartida:

```powershell
python pdf2md.py --importar "C:\Users\leand\OneDrive\Documentos\Proyectos\descargador_expedientes"
```

Después de importar, correr `python pdf2md.py` para generar los `.md` correspondientes.

4. **Convertir un PDF puntual:**

```powershell
python pdf2md.py "C:\ruta\a\ExpedienteX.pdf"
```

## Formato del Markdown generado

Cada `.md` tiene un front-matter (`expediente`, `pdf_origen`, `sha256_origen`, `paginas`,
`paginas_ocr`, `generado`), un índice de piezas detectadas heurísticamente (demandas, cédulas,
resoluciones, etc. con página y fecha), y el texto de cada página bajo un marcador
`<!-- p.N -->` (o `<!-- p.N ocr -->` si esa página se resolvió con OCR). Los marcadores son
invisibles al renderizar el Markdown pero permiten citar la página exacta del PDF original.

## Opciones útiles

- `--forzar` — regenera el `.md` aunque no haya cambiado el PDF.
- `--ocr nunca` — no usar OCR (más rápido, pero páginas escaneadas quedan vacías).
- `--ocr forzar` — OCR en todas las páginas, no solo las que parecen escaneadas.
- `--expedientes "otra\ruta"` — usar otra carpeta base en vez de la default.

## Troubleshooting

- **`[OCR no disponible: falta pytesseract/Pillow]`**: correr
  `pip install -r requirements.txt` en la carpeta del proyecto.
- **Tesseract no encontrado / error de OCR con path**: instalar Tesseract OCR para Windows y
  agregarlo al PATH del sistema (paquete de idioma español incluido). El mismo problema que puede
  aparecer con `libro2epub`.
- **El índice de piezas sale vacío o pobre**: es una heurística por palabras clave y mayúsculas al
  inicio de página, no usa IA. Si el expediente tiene un formato atípico, el índice puede salir
  incompleto — no afecta el resto del `.md`, que sigue teniendo el texto completo por página.
- **Un expediente no se reconvierte después de pisar el PDF**: confirmar que el nombre del archivo
  no cambió (el `.md` de salida se llama igual que el PDF) y que no está corriendo con un PDF
  distinto en la misma ruta sin querer.
