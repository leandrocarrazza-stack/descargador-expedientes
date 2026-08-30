# Fase 0 — Descubrimiento del sitio STJER

**Tiempo: ~1 hora. Sin escribir código. La corrés vos, en tu máquina.**

El parser, los selectores y el formato del POST están escritos con valores
razonables pero **adivinados**: nadie capturó nunca el tráfico real de
`jur.jusentrerios.gov.ar`. Esta fase reemplaza esas suposiciones por datos.

Todo va a `data/jurisprudencia/descubrimiento/`. Cuando termines:

```bash
python -m scripts.stjer descubrir
```

te dice qué falta.

> Los `.har` y los `curl_*.txt` **llevan cookies de sesión**: están
> gitignoreados a propósito. Los `.html` sí se versionan, porque son las
> fixtures con las que se testea el parser.

---

## A. Entorno (2 min)

Guardá la salida en `descubrimiento/entorno.txt`:

```bash
python -c "import sqlite3;c=sqlite3.connect(':memory:');c.execute('create virtual table t using fts5(x, tokenize=\"unicode61 remove_diacritics 2\")');print('FTS5 OK', sqlite3.sqlite_version)"
curl -sI -A "Mozilla/5.0" https://jur.jusentrerios.gov.ar/jur/dossier/bCARATULA_b__FARAS__.PDF
curl -s https://jur.jusentrerios.gov.ar/robots.txt
curl -sI https://jur.jusentrerios.gov.ar/jur/dossier/
```

Confirma cuatro cosas: que tu Python trae FTS5 (requisito duro), que el
endpoint de PDF de verdad no pide sesión, si publican reglas de crawleo, y si
el directorio `dossier/` es listable. **Si `dossier/` fuera listable, avisá:
simplifica la enumeración muchísimo.**

---

## B. Captura de red (25 min)

Chrome → F12 → pestaña **Network**, con **Preserve log** y **Disable cache**
tildados.

En cada paso: click derecho sobre el request → **Copy → Copy as cURL (bash)**.
Y del panel Elements: click derecho en `<html>` → **Copy → Copy outerHTML**.

| # | Acción | Guardar como |
|---|---|---|
| 1 | Cargar `https://jur.jusentrerios.gov.ar/jur/?ai=jur\|\|newpublica`, **antes** de resolver el captcha | `00_inicial.html` |
| 2 | Copiar el elemento del captcha (`<img>`). **Anotá si `src` es una URL tipo `captcha.php?...` o un `data:` URI** | dentro de `00_inicial.html` alcanza |
| 3 | Resolver el captcha a mano | `curl_01_captcha.txt` |
| 4 | Una búsqueda: Fuero "Civil y Comercial", fechas 01/01/2024 → 31/01/2024 | `curl_02_buscar.txt` + `01_listado.html` |
| 5 | Click en "Siguiente →" | `curl_03_pagina2.txt` + `02_listado_p2.html` |
| 6 | Click en un resultado para abrir el detalle | `curl_04_detalle.txt` + `03_detalle.html` |
| 7 | Abrir "Buscar voces en el Tesauro"; expandir una Materia y después una Voz Principal | `curl_05_tesauro_materia.txt`, `curl_06_tesauro_voz.txt` + `04_tesauro.html` |
| 8 | Network → click derecho → **Save all as HAR with content** | `captura.har` |

---

## C. Vida de la sesión (30 min de reloj, 2 de atención)

Anotá la hora en que resolviste el captcha. Dejá la pestaña quieta. Probá una
búsqueda a los **+25 min**, y en un perfil nuevo repetí con **+50 min**.

Anotá en `descubrimiento/vida_sesion.txt` a partir de cuántos minutos de
inactividad vuelve a pedir el captcha.

PHP usa `session.gc_maxlifetime` = 1440 s (24 min) por defecto, así que lo
esperable es que caduque por **inactividad**. Durante una cosecha activa no hay
inactividad: por eso el diseño asume **un captcha por corrida**, no uno por
hora. Este número lo confirma o lo desmiente.

---

## D. Regla del nombre de PDF (10 min)

De `01_listado.html` y `03_detalle.html`, juntá **20 pares** (carátula mostrada
→ nombre del archivo PDF) y anotá la transformación que inferís: ¿mayúsculas?,
¿sin acentos?, ¿espacios a `_`?, ¿el envoltorio `b..._b`?, ¿trunca a N
caracteres? Va a `descubrimiento/regla_pdf.txt`.

**La pregunta crítica de esta sección:** ¿el enlace al PDF ya está en la fila
del listado, o solo aparece en el detalle? Si está en el listado, la pasada B
(detalles, ~14.800 requests) pasa de obligatoria a opcional.

---

## E. Las 8 preguntas, y cómo ramifica el plan

| Pregunta | Si SÍ | Si NO |
|---|---|---|
| ¿`buscar`/`detalle` son POST form-encoded a `aplicacion.php`? | Rama A/B | → Rama C |
| ¿El `ah=` es constante en todo el HAR? | **Rama A** (`--motor http-fijo`) | **Rama B** (`--motor http`, default): se re-lee de cada respuesta |
| ¿Hay algún parámetro que no aparezca en la respuesta anterior (HMAC, nonce calculado en JS)? | **Rama C** (`--motor navegador`) | A/B siguen en pie |
| ¿Las respuestas son JSON? | El parser ya lo maneja | Casi seguro JS con HTML embebido: `desenvolver_toba()` ya lo cubre |
| ¿La fila expone un id numérico estable? | Es la `clave_natural` (mejor) | Se cae a `sha1(expediente\|carátula\|fecha)` |
| ¿El tamaño de página es configurable (`filas=100`)? | **Subilo al máximo: 4× menos requests** | Quedan 25/página |
| ¿Hay tope de profundidad de paginado? | Hay que sub-particionar los meses por fuero | Partición solo por mes |
| ¿El árbol del tesauro viene en una sola respuesta? | 1 request | Hasta ~600 requests perezosos (igual < 30 min) |

Probabilidad estimada: **A 30% · B 55% · C 15%**. La rama B es el default y no
requiere tocar nada.

---

## F. Volcar los hallazgos (sin tocar código)

Los tres archivos siguientes son **opcionales**: solo hacen falta si lo que
viste difiere de los valores por defecto. Todos son overrides que se suman a lo
que ya está, no reemplazos totales.

### `descubrimiento/perfil.json` — parser

```json
{
  "encabezados": {
    "Carátula del expediente": "caratula",
    "Fecha de la sentencia": "fecha_fallo"
  },
  "patron_total": "se\\s+encontraron\\s+([\\d.]+)\\s+registro",
  "patron_pdf": "(dossier/[^\"'\\s>]+\\.(?:pdf|PDF))"
}
```

Las claves de `encabezados` se comparan normalizadas (minúsculas, sin acentos,
sin puntuación), así que `"Nº Expediente"` y `"NRO. EXPEDIENTE"` caen en el
mismo lugar sin que tengas que listar las dos.

### `descubrimiento/selectores.json` — formulario

```json
{
  "captcha_img": "#ei_1234 img",
  "captcha_input": "input[name='ei_1234_captcha']",
  "fecha_desde": "input[name='ef_fecha_desde']",
  "boton_buscar": "input[value='Buscar']"
}
```

Acepta un string o una lista. Lo que pongas acá se prueba **antes** que los
candidatos por defecto.

### `descubrimiento/formato_consulta.json` — POST de búsqueda

Sale de `curl_02_buscar.txt`:

```json
{
  "campo_fecha_desde": "ef_fecha_desde",
  "campo_fecha_hasta": "ef_fecha_hasta",
  "campo_fuero": "ef_fuero",
  "campo_pagina": "pagina",
  "campo_filas": "cant_filas",
  "filas_por_pagina": 100,
  "campo_accion": "toba_accion",
  "accion_buscar": "ei_2001_buscar",
  "formato_fecha": "%d/%m/%Y",
  "extra": {"ei_2001_datos__paginado": "1"}
}
```

`extra` es para los campos ocultos que Toba mande y que no encajen en ninguno
de los anteriores: se agregan tal cual al POST.

---

## G. Verificar que quedó bien

```bash
python -m scripts.stjer descubrir              # los 8 artefactos presentes
python -m pytest tests/stjer/ -q               # 73 tests, sin red

# Con las fixtures reales ya guardadas, comprobá que el parser las entiende:
python -c "
from pathlib import Path
from modulos.jurisprudencia.stjer import parser as P
d = Path('data/jurisprudencia/descubrimiento')
l = P.parsear_listado((d/'01_listado.html').read_text(encoding='utf-8'))
print('filas parseadas:', len(l), '| total declarado:', l.total_registros)
det = P.parsear_detalle((d/'03_detalle.html').read_text(encoding='utf-8'))
print('caratula:', det.caratula)
print('sumarios:', len(det.sumarios), '| voces:', len(det.voces), '| votos:', len(det.votos))
print('pdf:', det.enlace_pdf)
print('nodos del tesauro:', len(P.parsear_arbol_tesauro((d/'04_tesauro.html').read_text(encoding='utf-8'))))
"
```

Si `filas parseadas` es 0, o `caratula` sale vacía, ajustá `perfil.json` y
volvé a correr. **No sigas a la Fase 1 hasta que ese script imprima datos
sensatos**: todo lo demás depende de que el parser entienda el HTML real.

Después, un piloto chico antes de comprometerte a la corrida completa:

```bash
python -m scripts.stjer sesion --abrir
python -m scripts.stjer cosechar listas --desde 2024-03 --hasta 2024-03
python -m scripts.stjer estado
```

El conteo guardado tiene que coincidir con el "Se encontraron N registros" del
sitio. Si no coincide, `estado` te lo marca como mes descuadrado.
